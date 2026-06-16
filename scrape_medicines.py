# ─── scrape_medicines.py ──────────────────────────────────
# Phase 1: Load companies_detail.csv → for each company URL,
#          paginate /brands pages → collect all brand list rows
#          (name, dosage form, strength, generic, price, url)
# Phase 2: Visit each brand detail URL → scrape full details
#
# Run:  python scrape_medicines.py
# ──────────────────────────────────────────────────────────

import random
import re
import time

import pandas as pd
from lxml import html as lhtml

from config import MIN_DELAY, MAX_DELAY, SAVE_EVERY
from utils import safe_get

# ── Files ─────────────────────────────────────────────────
COMPANY_FILE = "../companies_detail.csv"   # input  (needs: url, name columns)
BRAND_LIST   = "../brands_list.csv"        # Phase 1 output
BRAND_DETAIL = "../brands_detail.csv"      # Phase 2 output

# ── XPath constants ───────────────────────────────────────
# The three col-divs that hold data-rows sit inside div[2] of the grid
BRAND_COLS_ROOT   = '//*[@id="ms-block"]/section/div/div[2]'
# Every hoverable-block anchor IS one brand card
BRAND_CARD_XPATH  = './/a[contains(@class,"hoverable-block")]'
# Within each card:
NAME_XPATH        = './/div[contains(@class,"data-row-top")]'
STRENGTH_XPATH    = './/div[contains(@class,"data-row-strength")]//span[contains(@class,"grey")]'
GENERIC_XPATH     = './/div[not(contains(@class,"data-row-top")) and not(contains(@class,"data-row-strength")) and not(contains(@class,"packages-wrapper"))]'
PRICE_XPATH       = './/span[contains(@class,"package-pricing")]'
PACKAGE_XPATH     = './/span[contains(@class,"unit-price")]'
# Pagination — next link
NEXT_PAGE_XPATH   = '//a[@rel="next"]'


def polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def brands_url(company_url: str, page: int) -> str:
    """
    Build the brands listing URL for a company.
    company_url examples:
      https://medex.com.bd/companies/2/aci-limited
      https://medex.com.bd/companies/103/ad-din-pharmaceuticals-ltd/brands
    Always normalise to: <base>/brands?page=N
    """
    base = company_url.rstrip("/")
    if not base.endswith("/brands"):
        base += "/brands"
    return f"{base}?page={page}"


def parse_price(card) -> tuple[str, str]:
    """
    Return (package_label, price_value) from the package-pricing span.
    e.g. '100 ml bottle', '৳ 280.00'  or  'Unit Price', '৳ 21.14'
    """
    unit_span = card.xpath(PACKAGE_XPATH)
    price_span = card.xpath(PRICE_XPATH)

    price = price_span[0].text_content().strip() if price_span else "N/A"

    if unit_span:
        # full text of unit-price span, minus the nested pricing text
        full = unit_span[0].text_content().strip()
        # label is everything before the price value
        label = full.replace(price, "").strip().strip(":")
        label = label if label else "Unit Price"
    else:
        label = "Unit Price"

    return label, price


# ─────────────────────────────────────────────────────────
# PHASE 1 — Collect brand list rows for every company
# ─────────────────────────────────────────────────────────

def scrape_brand_cards(tree) -> list[dict]:
    """Parse all hoverable-block cards from an already-fetched tree."""
    roots = tree.xpath(BRAND_COLS_ROOT)
    if not roots:
        return []

    cards = []
    for card in roots[0].xpath(BRAND_CARD_XPATH):
        brand_url = card.get("href", "").strip()

        # Name + dosage form live together in data-row-top
        name_div = card.xpath(NAME_XPATH)
        if name_div:
            # dosage form is in a <span class="inline-dosage-form">
            dosage_span = name_div[0].xpath('.//span[contains(@class,"inline-dosage-form")]')
            dosage_form = dosage_span[0].text_content().strip() if dosage_span else ""
            # brand name = full text minus the dosage form text
            full_name = name_div[0].text_content().strip()
            brand_name = full_name.replace(dosage_form, "").strip()
        else:
            brand_name = dosage_form = "N/A"

        # Strength
        strength_el = card.xpath(STRENGTH_XPATH)
        strength = strength_el[0].text_content().strip() if strength_el else "N/A"

        # Generic name — the plain div that isn't top/strength/packages
        generic_divs = card.xpath(GENERIC_XPATH)
        # Filter to direct children of the data-row div, skip empties
        generic = "N/A"
        for el in generic_divs:
            text = el.text_content().strip()
            if text and len(text) > 1:
                generic = text
                break

        # Price
        pkg_label, price = parse_price(card)

        cards.append({
            "brand_name":    brand_name,
            "dosage_form":   dosage_form,
            "strength":      strength,
            "generic":       generic,
            "package_label": pkg_label,
            "price":         price,
            "url":           brand_url,
        })

    return cards


def collect_brands_for_company(company_name: str, company_url: str) -> list[dict]:
    """Paginate all /brands pages for one company and return every card."""
    all_cards: list[dict] = []
    page = 1

    while True:
        url  = brands_url(company_url, page)
        print(f"    [Page {page}] {url}")

        try:
            tree  = lhtml.fromstring(safe_get(url).content)
            cards = scrape_brand_cards(tree)
        except Exception as exc:
            print(f"    ✗ Failed: {exc}")
            break

        if not cards:
            print("    No cards found — stopping.")
            break

        # Tag each card with its source company
        for c in cards:
            c["company_name"] = company_name
            c["company_url"]  = company_url

        all_cards.extend(cards)
        print(f"    ✓ {len(cards)} brands | Running total: {len(all_cards)}")

        # Follow next page if present
        if tree.xpath(NEXT_PAGE_XPATH):
            page += 1
            polite_delay()
        else:
            break

    return all_cards


def collect_all_brands(company_df: pd.DataFrame) -> list[dict]:
    """Iterate every company row and collect all brand list entries."""
    all_brands: list[dict] = []
    total = len(company_df)

    for i, row in enumerate(company_df.itertuples(), 1):
        print(f"\n[{i}/{total}] {row.name}")
        brands = collect_brands_for_company(row.name, row.url)
        all_brands.extend(brands)

        # Periodic save
        if i % SAVE_EVERY == 0:
            pd.DataFrame(all_brands).to_csv(BRAND_LIST, index=False, encoding="utf-8-sig")
            print(f"  [Progress saved: {i}/{total} companies]")

        polite_delay()

    pd.DataFrame(all_brands).to_csv(BRAND_LIST, index=False, encoding="utf-8-sig")
    return all_brands


# ─────────────────────────────────────────────────────────
# PHASE 2 — Fetch full brand detail pages
# ─────────────────────────────────────────────────────────

def parse_brand_detail(url: str) -> dict:
    """Extract full details from a single brand detail page."""
    tree = lhtml.fromstring(safe_get(url).content)
    data: dict = {"url": url}

    # Brand name (h1)
    h1 = tree.xpath("//h1/text()")
    data["name"] = h1[0].strip() if h1 else "N/A"

    # Key-value detail table (Generic, Strength, Dosage Form, Pack Size, Price…)
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


def enrich_brands(brand_df: pd.DataFrame) -> list[dict]:
    """Visit every brand URL and merge list-level fields into detail dict."""
    all_data: list[dict] = []
    total = len(brand_df)

    for i, row in enumerate(brand_df.itertuples(), 1):
        url = row.url
        print(f"[{i}/{total}] {url}")

        try:
            detail = parse_brand_detail(url)
            # Carry over list-level data as fallbacks
            detail.setdefault("brand_name",    row.brand_name)
            detail.setdefault("dosage_form",   row.dosage_form)
            detail.setdefault("strength",      row.strength)
            detail.setdefault("generic",       row.generic)
            detail.setdefault("price",         row.price)
            detail.setdefault("company_name",  row.company_name)
            detail.setdefault("company_url",   row.company_url)
            all_data.append(detail)
            print(f"  ✓ {detail.get('name', 'Unknown')}")
        except Exception as exc:
            print(f"  ✗ Failed: {exc}")
            all_data.append({**row._asdict(), "error": str(exc)})

        if i % SAVE_EVERY == 0:
            pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")
            print(f"  [Saved progress: {i}/{total}]")

        polite_delay()

    pd.DataFrame(all_data).to_csv(BRAND_DETAIL, index=False, encoding="utf-8-sig")
    return all_data


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  MedEx — Medicine / Brand Scraper")
    print("=" * 55)

    # ── Phase 1 ───────────────────────────────────────────
    print(f"\n[Phase 1] Loading companies from: {COMPANY_FILE}\n")
    company_df = pd.read_csv(COMPANY_FILE, usecols=["name", "url"])
    company_df = company_df.dropna(subset=["url"])
    print(f"  {len(company_df)} companies loaded.")

    print("\nCollecting brand listing rows...\n")
    brands = collect_all_brands(company_df)
    print(f"\n✓ {len(brands)} brands saved to: {BRAND_LIST}")

    # ── Phase 2 ───────────────────────────────────────────
    print(f"\n[Phase 2] Fetching full brand details...\n")
    brand_df = pd.read_csv(BRAND_LIST)
    enrich_brands(brand_df)

    print("\n" + "=" * 55)
    print(f"  Done! Full details saved to: {BRAND_DETAIL}")
    print("=" * 55)