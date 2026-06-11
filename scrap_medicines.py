# ─── scrape_medicines.py ─────────────────────────────────
# Scrapes individual medicine detail pages from MedEx.
# Run:  python scrape_medicines.py
# ──────────────────────────────────────────────────────────

from bs4 import BeautifulSoup

from config import OUTPUT
from utils import safe_get, scrape_all


# ── Page parser ───────────────────────────────────────────

def parse_medicine(url: str) -> dict:
    """Extract all fields from a single medicine detail page."""
    soup = BeautifulSoup(safe_get(url).text, "html.parser")

    data: dict = {"url": url}

    # Medicine / product name
    h1 = soup.find("h1")
    data["name"] = h1.text.strip() if h1 else "N/A"

    # Generic / INN name (commonly in a subtitle or small tag)
    generic_tag = soup.select_one(".generic-name, .subtitle, h2")
    data["generic_name"] = generic_tag.text.strip() if generic_tag else "N/A"

    # Key-value detail table (strength, form, pack size, price, …)
    for row in soup.select("table tr"):
        cols = row.find_all("td")
        if len(cols) == 2:
            key   = cols[0].text.strip().lower().replace(" ", "_")
            value = cols[1].text.strip()
            data[key] = value

    # Manufacturer link / name
    mfr_tag = soup.select_one("a[href*='/companies/']")
    data["manufacturer"]     = mfr_tag.text.strip() if mfr_tag else "N/A"
    data["manufacturer_url"] = mfr_tag["href"]       if mfr_tag else "N/A"

    # Indications / description paragraphs
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
    print("       MedEx — Medicine Scraper")
    print("=" * 55)

    scrape_all(
        entity="medicines",
        output_file=OUTPUT["medicines"],
        scrape_fn=parse_medicine,
    )