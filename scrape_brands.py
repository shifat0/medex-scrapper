# ─── scrape_brands.py ────────────────────────────────────
# Scrapes brand detail pages from MedEx.
# Run:  python scrape_brands.py
# ──────────────────────────────────────────────────────────

from bs4 import BeautifulSoup

from config import OUTPUT
from utils import safe_get, scrape_all


# ── Page parser ───────────────────────────────────────────

def parse_brand(url: str) -> dict:
    """Extract all fields from a single brand detail page."""
    soup = BeautifulSoup(safe_get(url).text, "html.parser")

    data: dict = {"url": url}

    # Brand name
    h1 = soup.find("h1")
    data["name"] = h1.text.strip() if h1 else "N/A"

    # Generic / INN name
    generic_tag = soup.select_one(".generic-name, .subtitle, h2")
    data["generic_name"] = generic_tag.text.strip() if generic_tag else "N/A"

    # Key-value table (strength, dosage form, pack size, unit price, …)
    for row in soup.select("table tr"):
        cols = row.find_all("td")
        if len(cols) == 2:
            key   = cols[0].text.strip().lower().replace(" ", "_")
            value = cols[1].text.strip()
            data[key] = value

    # Manufacturer
    mfr_tag = soup.select_one("a[href*='/companies/']")
    data["manufacturer"]     = mfr_tag.text.strip() if mfr_tag else "N/A"
    data["manufacturer_url"] = mfr_tag["href"]       if mfr_tag else "N/A"

    # Related medicine link
    med_tag = soup.select_one("a[href*='/medicines/']")
    data["medicine_url"] = med_tag["href"] if med_tag else "N/A"

    # Description / indications
    desc_parts = [
        p.text.strip()
        for p in soup.select("p")
        if len(p.text.strip()) > 50
    ]
    data["description"] = " ".join(desc_parts) if desc_parts else "N/A"

    return data


# ── Entry point ───────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("       MedEx — Brand Scraper")
    print("=" * 55)

    scrape_all(
        entity="brands",
        output_file=OUTPUT["brands"],
        scrape_fn=parse_brand,
    )