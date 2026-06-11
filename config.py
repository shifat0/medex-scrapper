# ─── config.py ────────────────────────────────────────────
# Centralized configuration for all MedEx scrapers.
# Import this in every scraper — never hardcode these values.
# ──────────────────────────────────────────────────────────

# ── Site ──────────────────────────────────────────────────
BASE_URL = "https://medex.com.bd"

ENDPOINTS = {
    "companies":  f"{BASE_URL}/companies",
    "brands":     f"{BASE_URL}/brands",
    "medicines":  f"{BASE_URL}/medicines",
}

# ── Output files ──────────────────────────────────────────
OUTPUT = {
    "companies": "medex_companies.csv",
    "medicines": "medex_medicines.csv",
    "brands":    "medex_brands.csv",
}

# ── Request behaviour ────────────────────────────────────
MIN_DELAY        = 1.5          # seconds between requests (lower bound)
MAX_DELAY        = 3.5          # seconds between requests (upper bound)
REQUEST_TIMEOUT  = 15           # seconds before a request times out
MAX_RETRIES      = 3            # tenacity: max retry attempts
RETRY_MIN_WAIT   = 2            # tenacity: minimum back-off seconds
RETRY_MAX_WAIT   = 10           # tenacity: maximum back-off seconds

# ── Progress ──────────────────────────────────────────────
SAVE_EVERY = 10                 # persist to CSV every N scraped items

# ── HTTP headers (static parts) ──────────────────────────
# User-Agent is injected dynamically in utils.py via fake_useragent.
STATIC_HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         f"{BASE_URL}/",
}

# ── URL path patterns (used for link validation) ──────────
# A valid internal link for each entity has these path segments.
LINK_PATTERNS = {
    "companies": {"prefix": "companies", "id_index": 1},
    "brands":    {"prefix": "brands",    "id_index": 1},
    "medicines": {"prefix": "medicines", "id_index": 1},
}