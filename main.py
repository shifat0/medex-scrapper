import requests
import random
import time
import pandas as pd
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tenacity import retry, wait_exponential, stop_after_attempt

# ─── Config ───────────────────────────────────────────────
BASE_URL = "https://medex.com.bd"
OUTPUT_FILE = "medex_companies.csv"
MIN_DELAY = 1.5
MAX_DELAY = 3.5
# ──────────────────────────────────────────────────────────

ua = UserAgent()
session = requests.Session()


def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://medex.com.bd/companies",
    }


@retry(wait=wait_exponential(min=2, max=10), stop=stop_after_attempt(3))
def safe_get(url):
    session.headers.update(get_headers())
    res = session.get(url, timeout=15)
    res.raise_for_status()
    return res


def get_all_company_links():
    """Scrape all company links across all pages"""
    all_links = []
    page = 1

    while True:
        url = f"{BASE_URL}/companies?page={page}"
        print(f"  Fetching page {page}: {url}")

        try:
            res = safe_get(url)
        except Exception as e:
            print(f"  Failed to fetch page {page}: {e}")
            break

        soup = BeautifulSoup(res.text, "html.parser")

        # Find company links — pattern: /companies/{id}/{slug}
        company_tags = soup.select("a[href*='/companies/']")
        print(f"company_tags found: {len(company_tags)}")
        page_links = []

        for tag in company_tags:
            href = tag["href"]
            parts = href.strip("/").split("/")
            # Valid company link has exactly 3 parts: companies / id / slug
            if len(parts) == 3 and parts[0] == "companies" and parts[1].isdigit():
                full_url = BASE_URL + "/" + href.strip("/")
                if full_url not in page_links:
                    page_links.append(full_url)

        if not page_links:
            print(f"  No companies found on page {page}, stopping.")
            break

        all_links.extend(page_links)
        print(f"  Found {len(page_links)} companies on page {page} | Total so far: {len(all_links)}")

        # Check if next page exists
        next_page = soup.find("a", string="›") or soup.find("a", rel="next")
        if not next_page:
            print("  No more pages.")
            break

        page += 1
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    return list(dict.fromkeys(all_links))  # deduplicate preserving order


def get_company_details(url):
    """Scrape all details from a company page"""
    res = safe_get(url)
    soup = BeautifulSoup(res.text, "html.parser")

    data = {"url": url}

    # ── Company name ──
    h1 = soup.find("h1")
    data["name"] = h1.text.strip() if h1 else "N/A"

    # ── Logo URL ──
    logo = soup.select_one("img[src*='company_logos']")
    data["logo_url"] = logo["src"] if logo else "N/A"

    # ── Table fields (Established, Market Share, Growth, etc.) ──
    rows = soup.select("table tr")
    for row in rows:
        cols = row.find_all("td")
        if len(cols) == 2:
            key = cols[0].text.strip().lower().replace(" ", "_")
            value = cols[1].text.strip()
            data[key] = value

    # ── Description ──
    paragraphs = soup.select("p")
    desc_parts = [p.text.strip() for p in paragraphs if len(p.text.strip()) > 50]
    data["description"] = " ".join(desc_parts) if desc_parts else "N/A"

    # ── Top brands ──
    # brand_tags = soup.select("a[href*='/brands/']")
    # brands = [tag.text.strip() for tag in brand_tags if tag.text.strip()]
    # data["top_brands"] = " | ".join(brands[:10]) if brands else "N/A"

    return data


def save_progress(all_data, filename):
    """Save current progress to CSV"""
    df = pd.DataFrame(all_data)
    df.to_csv(filename, index=False, encoding="utf-8-sig")


def main():
    print("=" * 55)
    print("       MedEx Company Scraper")
    print("=" * 55)

    # Step 1: Get all company links
    print("\n[Step 1] Collecting company links from all pages...\n")
    company_links = get_all_company_links()
    print(f"\nTotal unique companies found: {len(company_links)}")

    # Step 2: Scrape each company
    print(f"\n[Step 2] Scraping company details...\n")
    all_data = []

    for i, url in enumerate(company_links, 1):
        print(f"[{i}/{len(company_links)}] {url}")

        try:
            details = get_company_details(url)
            all_data.append(details)
            print(f"  ✓ {details.get('name', 'Unknown')}")
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            all_data.append({"url": url, "name": "FAILED", "error": str(e)})

        # Save progress every 10 companies
        if i % 10 == 0:
            save_progress(all_data, OUTPUT_FILE)
            print(f"  [Progress saved: {i} companies]")

        # Random delay between requests
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        time.sleep(delay)

    # Step 3: Final save
    save_progress(all_data, OUTPUT_FILE)

    print("\n" + "=" * 55)
    print(f"Done! Scraped {len(all_data)} companies")
    print(f"Saved to: {OUTPUT_FILE}")
    print("=" * 55)



main()

