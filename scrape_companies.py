# ─── scrape_companies.py ─────────────────────────────────
# Phase 1: Scrape company list pages → save basic info (url, name, counts)
# Phase 2: Load saved URLs → visit each and extract full details
# Run:  python scrape_companies.py
# ─────────────────────────────────────────────────────────

import random
import re
import time

import pandas as pd
from lxml import html as lhtml

from config import BASE_URL, MIN_DELAY, MAX_DELAY, SAVE_EVERY
from utils import safe_get

# ── Output files ──────────────────────────────────────────
LIST_FILE   = "companies_list.csv"    # Phase 1 output (basic info)
DETAIL_FILE = "companies_detail.csv"  # Phase 2 output (full details)

# ── XPath constants ───────────────────────────────────────
COMPANY_LIST_ROOT = '//*[@id="ms-block"]/section/div/div[2]'
DATA_ROW_XPATH    = './/div[contains(@class,"data-row")]'
NAME_LINK_XPATH   = './/div[contains(@class,"data-row-top")]/a'
COUNT_DIV_XPATH   = './/div[not(contains(@class,"data-row-top")) and not(contains(@class,"data-row"))]'


def polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ─────────────────────────────────────────────────────────
# PHASE 1 — Collect company list (name, url, counts)
# ─────────────────────────────────────────────────────────

def parse_count_text(raw: str) -> tuple[int, int]:
    """
    Parse '408 generics, 814 brand names' → (408, 814).
    Returns (0, 0) if pattern not matched.
    """
    generics = re.findall(r"(\d+)\s+generic", raw)
    brands   = re.findall(r"(\d+)\s+brand", raw)
    return (int(generics[0]) if generics else 0,
            int(brands[0])   if brands   else 0)


def scrape_list_page(page: int) -> list[dict]:
    """Scrape one listing page and return rows from all data-row divs."""
    url  = f"{BASE_URL}/companies?page={page}"
    tree = lhtml.fromstring(safe_get(url).content)

    roots = tree.xpath(COMPANY_LIST_ROOT)
    if not roots:
        return []

    rows = []
    for data_row in roots[0].xpath(DATA_ROW_XPATH):
        anchor = data_row.xpath(NAME_LINK_XPATH)
        if not anchor:
            continue

        name       = anchor[0].text_content().strip()
        detail_url = anchor[0].get("href", "").strip()

        count_div  = data_row.xpath(COUNT_DIV_XPATH)
        raw_counts = count_div[0].text_content().strip() if count_div else ""
        generics, brands = parse_count_text(raw_counts)

        rows.append({
            "name":     name,
            "url":      detail_url,
            "generics": generics,
            "brands":   brands,
        })

    return rows


def has_next_page(tree) -> bool:
    """Check whether a next-page control exists on an already-parsed tree."""
    return bool(
        tree.xpath("//a[text()='›']") or
        tree.xpath("//a[@rel='next']")
    )


def collect_all_companies() -> list[dict]:
    """Paginate through all listing pages and collect every company row."""
    all_rows: list[dict] = []
    page = 1

    while True:
        url  = f"{BASE_URL}/companies?page={page}"
        print(f"  [Page {page}] {url}")

        try:
            res  = safe_get(url)
            tree = lhtml.fromstring(res.content)
            roots = tree.xpath(COMPANY_LIST_ROOT)

            if not roots:
                print("  Root XPath not found — stopping.")
                break

            rows = []
            for data_row in roots[0].xpath(DATA_ROW_XPATH):
                anchor = data_row.xpath(NAME_LINK_XPATH)
                if not anchor:
                    continue

                name       = anchor[0].text_content().strip()
                detail_url = anchor[0].get("href", "").strip()

                count_div  = data_row.xpath(COUNT_DIV_XPATH)
                raw_counts = count_div[0].text_content().strip() if count_div else ""
                generics, brands = parse_count_text(raw_counts)

                rows.append({
                    "name":     name,
                    "url":      detail_url,
                    "generics": generics,
                    "brands":   brands,
                })

            if not rows:
                print("  No data-rows found — stopping.")
                break

            all_rows.extend(rows)
            print(f"  ✓ {len(rows)} companies | Total: {len(all_rows)}")

            if not has_next_page(tree):
                print("  No next page.")
                break

        except Exception as exc:
            print(f"  ✗ Page {page} failed: {exc}")
            break

        page += 1
        polite_delay()

    # Deduplicate by URL
    seen, unique = set(), []
    for row in all_rows:
        if row["url"] not in seen:
            seen.add(row["url"])
            unique.append(row)

    return unique


# ─────────────────────────────────────────────────────────
# PHASE 2 — Fetch full details for each company URL
# ─────────────────────────────────────────────────────────

def parse_company_detail(url: str) -> dict:
    """Extract full details from a single company detail page."""
    tree = lhtml.fromstring(safe_get(url).content)
    data: dict = {"url": url}

    # Company name
    h1 = tree.xpath("//h1/text()")
    data["name"] = h1[0].strip() if h1 else "N/A"

    # Logo URL
    logo = tree.xpath("//img[contains(@src, 'company_logos')]/@src")
    data["logo_url"] = logo[0] if logo else "N/A"

    # Key-value table rows (Established, Market Share, Growth, …)
    for row in tree.xpath("//table//tr"):
        cols = row.xpath(".//td")
        if len(cols) == 2:
            key   = cols[0].text_content().strip().lower().replace(" ", "_")
            value = cols[1].text_content().strip()
            if key:
                data[key] = value

    # Description paragraphs (skip short nav/UI strings)
    desc_parts = [
        p.text_content().strip()
        for p in tree.xpath("//p")
        if len(p.text_content().strip()) > 50
    ]
    data["description"] = " ".join(desc_parts) if desc_parts else "N/A"

    return data


def enrich_with_details(list_df: pd.DataFrame) -> list[dict]:
    """Visit every URL from the list CSV and merge in full details."""
    all_data: list[dict] = []
    total = len(list_df)

    for i, row in enumerate(list_df.itertuples(), 1):
        url = row.url
        print(f"[{i}/{total}] {url}")

        try:
            detail = parse_company_detail(url)
            # Keep list-level counts even if detail page doesn't have them
            detail.setdefault("generics", row.generics)
            detail.setdefault("brands",   row.brands)
            all_data.append(detail)
            print(f"  ✓ {detail.get('name', 'Unknown')}")
        except Exception as exc:
            print(f"  ✗ Failed: {exc}")
            all_data.append({
                "url":      url,
                "name":     row.name,
                "generics": row.generics,
                "brands":   row.brands,
                "error":    str(exc),
            })

        if i % SAVE_EVERY == 0:
            pd.DataFrame(all_data).to_csv(DETAIL_FILE, index=False, encoding="utf-8-sig")
            print(f"  [Saved progress: {i}/{total}]")

        polite_delay()

    pd.DataFrame(all_data).to_csv(DETAIL_FILE, index=False, encoding="utf-8-sig")
    return all_data


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  MedEx — Company Scraper")
    print("=" * 55)

    # ── Phase 1 ───────────────────────────────────────────
    print("\n[Phase 1] Collecting company list...\n")
    companies = collect_all_companies()

    list_df = pd.DataFrame(companies)
    list_df.to_csv(LIST_FILE, index=False, encoding="utf-8-sig")
    print(f"\n✓ {len(companies)} companies saved to: {LIST_FILE}")

    # ── Phase 2 ───────────────────────────────────────────
    print(f"\n[Phase 2] Fetching full details...\n")
    enrich_with_details(list_df)

    print("\n" + "=" * 55)
    print(f"  Done! Full details saved to: {DETAIL_FILE}")
    print("=" * 55)