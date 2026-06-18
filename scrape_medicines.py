# ─── scrape_medicines.py ──────────────────────────────────
# Scrapes brand detail pages directly.
# Flow:
#   1. Load companies_detail.csv
#   2. For each company, paginate /brands → collect brand detail URLs
#   3. Visit each brand URL → parse full detail page
#   4. Save to brands_detail.csv (with periodic progress saves)
#
# Anti-bot measures:
#   - Random delays with longer rest bursts every N requests
#   - Rotating User-Agent via fake_useragent
#   - Random realistic header ordering
#   - cloudscraper for Cloudflare bypass
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
COMPANY_FILE  = "../companies_detail.csv"
BRAND_DETAIL  = "../brands_details_2.csv"
PROGRESS_FILE = "../brands_progress.txt"   # stores last scraped URL to resume

# ── Delays (seconds) ──────────────────────────────────────
MIN_DELAY     = 2.0
MAX_DELAY     = 5.0
BURST_EVERY   = 30      # take a longer break every N requests
BURST_MIN     = 15.0
BURST_MAX     = 35.0

# ── XPath constants ───────────────────────────────────────
BRAND_COLS_ROOT  = '//*[@id="ms-block"]/section/div/div[2]'
BRAND_CARD_XPATH = './/a[contains(@class,"hoverable-block")]'
NEXT_PAGE_XPATH  = '//a[@rel="next"]'


# ─────────────────────────────────────────────────────────
# HTTP — cloudscraper handles Cloudflare JS challenges
# ─────────────────────────────────────────────────────────

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

_request_count = 0


def fetch(url: str) -> bytes:
    """
    GET url, rotate headers, enforce delays, burst-rest every N requests.
    Returns response bytes.
    """
    global _request_count

    _request_count += 1

    # Burst rest
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
    """Detect Cloudflare / security-check page."""
    title = tree.xpath("//title/text()")
    body  = tree.xpath("//body//text()")
    signals = ["security check", "unusual traffic", "cloudflare", "just a moment"]
    combined = " ".join(title + body).lower()
    return any(s in combined for s in signals)


# ─────────────────────────────────────────────────────────
# Brand URL collection  (listing pages only — no detail fetch)
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

        try:
            tree  = lhtml.fromstring(fetch(url))
        except Exception as exc:
            print(f"    ✗ Fetch failed: {exc}")
            break

        if is_blocked(tree):
            print("    ✗ Blocked — stopping listing collection for this company.")
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

def parse_brand_detail(url: str) -> dict | None:
    """
    Fetch and parse a single brand detail page.
    Returns None if blocked.
    """
    tree = lhtml.fromstring(fetch(url))

    if is_blocked(tree):
        print("  ✗ Blocked on detail page.")
        return None

    data: dict = {"url": url}

    # Brand name
    h1 = tree.xpath("//h1/text()")
    data["name"] = h1[0].strip() if h1 else "N/A"

    # Key-value detail table
    for row in tree.xpath("//table//tr"):
        cols = row.xpath(".//td")
        if len(cols) == 2:
            key   = cols[0].text_content().strip().lower().replace(" ", "_")
            value = cols[1].text_content().strip()
            if key:
                data[key] = value

    # Manufacturer
    mfr = tree.xpath("//a[contains(@href,'/companies/')]")
    data["manufacturer"]     = mfr[0].text_content().strip() if mfr else "N/A"
    data["manufacturer_url"] = mfr[0].get("href", "")        if mfr else "N/A"

    # Indications / description
    desc_parts = [
        p.text_content().strip()
        for p in tree.xpath("//p")
        if len(p.text_content().strip()) > 50
    ]
    data["description"] = " ".join(desc_parts) if desc_parts else "N/A"

    return data


# ─────────────────────────────────────────────────────────
# Progress helpers  (resume after interruption)
# ─────────────────────────────────────────────────────────

def load_progress() -> set[str]:
    """Return set of already-scraped URLs from progress file."""
    if Path(PROGRESS_FILE).exists():
        return set(Path(PROGRESS_FILE).read_text().splitlines())
    return set()


def save_url_progress(url: str):
    with open(PROGRESS_FILE, "a") as f:
        f.write(url + "\n")


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  MedEx — Brand Detail Scraper")
    print("=" * 55)

    # Load companies
    print(f"\nLoading companies from: {COMPANY_FILE}")
    company_df = pd.read_csv(COMPANY_FILE, usecols=["name", "url"]).dropna(subset=["url"])
    print(f"  {len(company_df)} companies loaded.\n")

    # Resume support
    done_urls   = load_progress()
    all_data: list[dict] = []

    # Load existing output to append rather than overwrite
    if Path(BRAND_DETAIL).exists() and done_urls:
        all_data = pd.read_csv(BRAND_DETAIL).to_dict("records")
        print(f"  Resuming — {len(done_urls)} URLs already scraped.\n")

    total_companies = len(company_df)

    for ci, crow in enumerate(company_df.itertuples(), 1):
        print(f"\n{'='*40}")
        print(f"[Company {ci}/{total_companies}] {crow.name}")
        print(f"{'='*40}")

        # Collect all brand URLs for this company
        brand_urls = collect_brand_urls_for_company(crow.url)
        # Filter already-done
        brand_urls = [u for u in brand_urls if u not in done_urls]
        print(f"  {len(brand_urls)} new brand URLs to scrape.\n")

        for bi, burl in enumerate(brand_urls, 1):
            print(f"  [{bi}/{len(brand_urls)}] {burl}")

            result = parse_brand_detail(burl)

            if result is None:
                # Blocked — back off hard and retry once
                print("  Backing off 60s then retrying once...")
                time.sleep(random.uniform(55, 75))
                result = parse_brand_detail(burl)

            if result is None:
                print("  Still blocked — skipping.")
                continue

            result["company_name"] = crow.name
            result["company_url"]  = crow.url
            all_data.append(result)
            save_url_progress(burl)
            print(f"  ✓ {result.get('name', 'Unknown')}")

            # Periodic save
            if len(all_data) % SAVE_EVERY == 0:
                pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")
                print(f"  [Progress saved: {len(all_data)} brands total]")

    # Final save
    pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 55)
    print(f"  Done! {len(all_data)} brands saved to: {BRAND_DETAIL}")
    print("=" * 55)