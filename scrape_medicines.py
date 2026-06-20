# ─── scrape_medicines.py ──────────────────────────────────
# Scrapes brand detail pages directly.
# Flow:
#   1. Load companies_detail.csv
#   2. For each company, paginate /brands → collect brand detail URLs
#   3. Visit each brand URL → parse full detail page
#   4. Save to brands_detail.csv (with periodic progress saves)
#
# Anti-bot measures:
#   - cloudscraper for Cloudflare JS-challenge bypass
#   - Random delays + longer burst rests every N requests
#   - Escalating cooldowns on repeated blocks (90s → 180s → 300s)
#
# Run:  python scrape_medicines.py
# ──────────────────────────────────────────────────────────

import random
import time
from pathlib import Path

import cloudscraper
import pandas as pd
from lxml import html as lhtml

from config import SAVE_EVERY

# ── Files ─────────────────────────────────────────────────
COMPANY_FILE   = "../companies_detail.csv"
BRAND_DETAIL   = "../brands_detail_3.csv"
PROGRESS_FILE  = "../brands_progress.txt"     # already-scraped brand URLs
COMPANY_DONE_FILE = "../companies_done.txt"   # companies fully finished —
                                            # skip their listing pages entirely

# ── Delays (seconds) ──────────────────────────────────────
MIN_DELAY    = 4.0
MAX_DELAY    = 9.0
BURST_EVERY  = 15             # take a longer break every N requests
BURST_MIN    = 30.0
BURST_MAX    = 60.0

# ── Block recovery ────────────────────────────────────────
BLOCK_COOLDOWNS = [30, 60, 90]      # short escalating wait (s) per consecutive
                                     # block — soft rate-limits clear quickly;
                                     # if it's a hard IP block, longer waits
                                     # within this session won't help anyway
MAX_CONSECUTIVE_BLOCKED_BRANDS = 3  # abort whole run if this many brands in a
                                     # row are fully blocked — IP is likely
                                     # hard-blocked; waiting longer won't help


class IPBlockedError(Exception):
    """Raised when the IP appears hard-blocked for the rest of this session."""

# ── XPath constants — listing page ────────────────────────
BRAND_COLS_ROOT  = '//*[@id="ms-block"]/section/div/div[2]'
BRAND_CARD_XPATH = './/a[contains(@class,"hoverable-block")]'
NEXT_PAGE_XPATH  = '//a[@rel="next"]'

# ── XPath constants — detail page ─────────────────────────
DETAIL_NAME_XPATH       = '//h1[contains(@class,"brand")]'
DETAIL_DOSAGE_XPATH     = '//h1[contains(@class,"brand")]/small'
DETAIL_GENERIC_XPATH    = '//div[@title="Generic Name"]'
DETAIL_STRENGTH_XPATH   = '//div[@title="Strength"]'
DETAIL_MANUFACTURER_XPATH = '//div[@title="Manufactured by"]/a[1]'
DETAIL_PACKAGE_BLOCK    = '//div[contains(@class,"package-container")]'
# Labeled body sections (Indications, Pharmacology, Dosage, etc.)
DETAIL_SECTION_XPATH    = '//div[h3[contains(@class,"ac-header")]]'


def polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ─────────────────────────────────────────────────────────
# HTTP — cloudscraper handles Cloudflare JS challenges
# ─────────────────────────────────────────────────────────

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

_request_count = 0


def fetch(url: str) -> bytes:
    """GET url with rotation, delay, and periodic burst-rest."""
    global _request_count
    _request_count += 1

    if _request_count % BURST_EVERY == 0:
        rest = random.uniform(BURST_MIN, BURST_MAX)
        print(f"  [Burst rest: {rest:.1f}s after {_request_count} requests]")
        time.sleep(rest)
    else:
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    res = scraper.get(url, timeout=20)
    res.raise_for_status()
    return res.content


def is_blocked(tree) -> bool:
    """Detect Cloudflare / security-check interstitial page."""
    title = tree.xpath("//title/text()")
    body  = tree.xpath("//body//text()")
    signals = ["security check", "unusual traffic", "cloudflare", "just a moment"]
    combined = " ".join(title + body).lower()
    return any(s in combined for s in signals)


def fetch_with_block_recovery(url: str):
    """
    Fetch + parse url, retrying through BLOCK_COOLDOWNS on detection.
    Returns parsed tree, or None if still blocked after all cooldowns.
    """
    for attempt, cooldown in enumerate([0] + BLOCK_COOLDOWNS):
        if cooldown:
            print(f"  Blocked — cooling down {cooldown}s (attempt {attempt})...")
            time.sleep(cooldown)

        try:
            tree = lhtml.fromstring(fetch(url))
        except Exception as exc:
            print(f"  ✗ Fetch error: {exc}")
            continue

        if not is_blocked(tree):
            return tree

    return None  # exhausted all cooldowns


# ─────────────────────────────────────────────────────────
# Brand URL collection  (listing pages only)
# ─────────────────────────────────────────────────────────

def brands_list_url(company_url: str, page: int) -> str:
    base = company_url.rstrip("/")
    if not base.endswith("/brands"):
        base += "/brands"
    return f"{base}?page={page}"


def collect_brand_urls_for_company(company_url: str) -> list[str]:
    """Return all brand detail URLs from a company's paginated /brands pages."""
    urls: list[str] = []
    page = 1

    while True:
        url = brands_list_url(company_url, page)
        print(f"    [Listing page {page}] {url}")

        tree = fetch_with_block_recovery(url)
        if tree is None:
            print("    ✗ Permanently blocked on listing page — stopping company.")
            break

        roots = tree.xpath(BRAND_COLS_ROOT)
        if not roots:
            print("    No grid root found — stopping.")
            break

        page_urls = [
            card.get("href", "").strip()
            for card in roots[0].xpath(BRAND_CARD_XPATH)
            if card.get("href", "").strip()
        ]

        if not page_urls:
            print("    No brand cards found — stopping.")
            break

        urls.extend(page_urls)
        print(f"    ✓ {len(page_urls)} brands | Total: {len(urls)}")

        if tree.xpath(NEXT_PAGE_XPATH):
            page += 1
        else:
            break

    return urls


# ─────────────────────────────────────────────────────────
# Brand detail page parser
# ─────────────────────────────────────────────────────────

def parse_package_info(tree) -> dict:
    """
    Parse the package-container block into structured price fields.
    Handles patterns like:
      Unit Price: ৳ 21.14   (2 x 10: ৳ 422.80)   Strip Price: ৳ 211.40
      100 ml bottle: ৳ 280.00
    """
    blocks = tree.xpath(DETAIL_PACKAGE_BLOCK)
    result = {
        "unit_price":   "N/A",
        "pack_size_info": "N/A",
        "strip_price":  "N/A",
        "package_text": "N/A",
    }
    if not blocks:
        return result

    block = blocks[0]
    full_text = " ".join(
        t.strip() for t in block.xpath(".//text()") if t.strip()
    )
    result["package_text"] = full_text

    # Unit Price
    unit_span = block.xpath('.//span[contains(text(),"Unit Price")]/following-sibling::span[1]')
    if unit_span:
        result["unit_price"] = unit_span[0].text_content().strip()
    else:
        # Fallback: label-less price, e.g. "100 ml bottle: ৳ 280.00"
        price_spans = block.xpath(".//span")
        prices = [s.text_content().strip() for s in price_spans if "৳" in s.text_content()]
        if prices:
            result["unit_price"] = prices[0]

    # Pack size info, e.g. "(2 x 10: ৳ 422.80)"
    pack_span = block.xpath('.//span[contains(@class,"pack-size-info")]')
    if pack_span:
        result["pack_size_info"] = pack_span[0].text_content().strip()

    # Strip price
    strip_span = block.xpath('.//span[contains(text(),"Strip Price")]/following-sibling::span[1]')
    if strip_span:
        result["strip_price"] = strip_span[0].text_content().strip()

    return result


def parse_labeled_sections(tree) -> dict:
    """
    Parse every '<h3 class="ac-header">Label</h3> ... <div class="ac-body">text</div>'
    block into {label_snake_case: text}.
    Covers: Indications, Pharmacology, Dosage & Administration, Interaction,
    Contraindications, Side Effects, Pregnancy & Lactation, Precautions &
    Warnings, Overdose Effects, Therapeutic Class, Reconstitution,
    Storage Conditions, etc. — works for ANY section the page has.
    """
    sections = {}
    for block in tree.xpath(DETAIL_SECTION_XPATH):
        header = block.xpath('.//h3[contains(@class,"ac-header")]/text()')
        body   = block.xpath('.//div[contains(@class,"ac-body")]')
        if not header or not body:
            continue

        label = header[0].strip()
        key = (
            label.lower()
            .replace("&", "and")
            .replace(" ", "_")
        )
        # Use full_str variant if present (min-str-block has a truncated +
        # full version — full_str holds the complete text)
        full = body[0].xpath('.//div[contains(@class,"full-str")]')
        text = (full[0] if full else body[0]).text_content().strip()
        sections[key] = text

    return sections


def parse_brand_detail(url: str) -> dict | None:
    """Fetch and parse a single brand detail page. Returns None if blocked."""
    tree = fetch_with_block_recovery(url)
    if tree is None:
        return None

    data: dict = {"url": url}

    # ── Name & dosage form ──
    name_el = tree.xpath(DETAIL_NAME_XPATH)
    if name_el:
        dosage_el = tree.xpath(DETAIL_DOSAGE_XPATH)
        dosage_form = dosage_el[0].text_content().strip() if dosage_el else "N/A"
        full_text = name_el[0].text_content().strip()
        brand_name = full_text.replace(dosage_form, "").strip()
        data["name"] = brand_name
        data["dosage_form"] = dosage_form
    else:
        data["name"] = "N/A"
        data["dosage_form"] = "N/A"

    # ── Generic name ──
    generic_el = tree.xpath(DETAIL_GENERIC_XPATH)
    data["generic"] = generic_el[0].text_content().strip() if generic_el else "N/A"
    generic_link = tree.xpath(DETAIL_GENERIC_XPATH + "/a/@href")
    data["generic_url"] = generic_link[0] if generic_link else "N/A"

    # ── Strength ──
    strength_el = tree.xpath(DETAIL_STRENGTH_XPATH)
    data["strength"] = strength_el[0].text_content().strip() if strength_el else "N/A"

    # ── Manufacturer ──
    mfr_el = tree.xpath(DETAIL_MANUFACTURER_XPATH)
    if mfr_el:
        data["manufacturer"] = mfr_el[0].text_content().strip()
        data["manufacturer_url"] = mfr_el[0].get("href", "")
    else:
        data["manufacturer"] = "N/A"
        data["manufacturer_url"] = "N/A"

    # ── Pricing (MANDATORY) ──
    data.update(parse_package_info(tree))

    # ── Labeled body sections (Indications, Dosage, Side Effects, ...) ──
    data.update(parse_labeled_sections(tree))

    return data


# ─────────────────────────────────────────────────────────
# Progress helpers  (resume after interruption)
# ─────────────────────────────────────────────────────────

def load_progress() -> set[str]:
    """Return the set of brand URLs already scraped successfully."""
    if Path(PROGRESS_FILE).exists():
        return set(Path(PROGRESS_FILE).read_text().splitlines())
    return set()


def save_url_progress(url: str):
    """Append one successfully-scraped brand URL to the progress file."""
    with open(PROGRESS_FILE, "a") as f:
        f.write(url + "\n")


def load_done_companies() -> set[str]:
    """Return the set of company URLs whose brand lists were fully scraped."""
    if Path(COMPANY_DONE_FILE).exists():
        return set(Path(COMPANY_DONE_FILE).read_text().splitlines())
    return set()


def mark_company_done(company_url: str):
    """Record a company as fully scraped so future runs skip its listing pages."""
    with open(COMPANY_DONE_FILE, "a") as f:
        f.write(company_url + "\n")


def load_existing_brand_data() -> list[dict]:
    """Load previously-saved brand rows from the detail CSV, if it exists."""
    if Path(BRAND_DETAIL).exists():
        return pd.read_csv(BRAND_DETAIL).to_dict("records")
    return []


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("=" * 55)
    print("  MedEx — Brand Detail Scraper")
    print("=" * 55)

    print(f"\nLoading companies from: {COMPANY_FILE}")
    company_df = pd.read_csv(COMPANY_FILE, usecols=["name", "url"]).dropna(subset=["url"])
    print(f"  {len(company_df)} companies loaded.\n")

    done_urls      = load_progress()         # individual brand URLs already scraped
    done_companies = load_done_companies()    # companies fully finished — skip entirely
    all_data       = load_existing_brand_data()

    if done_urls or done_companies:
        print(
            f"  Resuming — {len(done_urls)} brand(s) and "
            f"{len(done_companies)} compan(y/ies) already done.\n"
        )

    # Skip companies that are fully finished — saves listing-page requests too
    remaining_df = company_df[~company_df["url"].isin(done_companies)]
    skipped = len(company_df) - len(remaining_df)
    if skipped:
        print(f"  Skipping {skipped} already-completed compan(y/ies).\n")

    total_companies = len(remaining_df)
    consecutive_blocked = 0

    try:
        for ci, crow in enumerate(remaining_df.itertuples(), 1):
            print(f"\n{'='*40}")
            print(f"[Company {ci}/{total_companies}] {crow.name}")
            print(f"{'='*40}")

            brand_urls = collect_brand_urls_for_company(crow.url)
            new_urls   = [u for u in brand_urls if u not in done_urls]
            print(
                f"  {len(new_urls)} new brand URL(s) to scrape "
                f"({len(brand_urls) - len(new_urls)} already done).\n"
            )

            for bi, burl in enumerate(new_urls, 1):
                print(f"  [{bi}/{len(new_urls)}] {burl}")

                result = parse_brand_detail(burl)

                if result is None:
                    consecutive_blocked += 1
                    print(
                        f"  ✗ Blocked after all cooldowns "
                        f"({consecutive_blocked}/{MAX_CONSECUTIVE_BLOCKED_BRANDS} in a row)."
                    )
                    if consecutive_blocked >= MAX_CONSECUTIVE_BLOCKED_BRANDS:
                        raise IPBlockedError(
                            f"{MAX_CONSECUTIVE_BLOCKED_BRANDS} brands in a row were "
                            "blocked even after escalating cooldowns. The IP is "
                            "likely hard-blocked by Cloudflare for this session — "
                            "waiting longer here won't help."
                        )
                    continue

                consecutive_blocked = 0  # reset on success
                result["company_name"] = crow.name
                result["company_url"]  = crow.url
                all_data.append(result)
                save_url_progress(burl)
                done_urls.add(burl)
                print(f"  ✓ {result.get('name', 'Unknown')} | Price: {result.get('unit_price', 'N/A')}")

                if len(all_data) % SAVE_EVERY == 0:
                    pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")
                    print(f"  [Progress saved: {len(all_data)} brands total]")

            # All brand URLs for this company were either already-done or
            # just scraped successfully → safe to mark the company itself done.
            mark_company_done(crow.url)
            print(f"  ✓ Company fully done: {crow.name}")

    except IPBlockedError as exc:
        pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")
        print("\n" + "!" * 55)
        print("  STOPPED — likely IP block")
        print("!" * 55)
        print(f"  {exc}")
        print(f"\n  Progress saved: {len(all_data)} brands so far.")
        print(f"  {len(done_urls)} brand URL(s) marked done in {PROGRESS_FILE}.")
        print(f"  Companies fully finished are recorded in {COMPANY_DONE_FILE}.")
        print("\n  What to do:")
        print("  1. Wait a few hours (or overnight) before resuming.")
        print("  2. Consider using a proxy / VPN / different network.")
        print("  3. Just re-run this script — it automatically skips")
        print("     finished companies and already-scraped brands, and")
        print("     only fetches what's new.")
        raise SystemExit(1)

    pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 55)
    print(f"  Done! {len(all_data)} brands saved to: {BRAND_DETAIL}")
    print("=" * 55)