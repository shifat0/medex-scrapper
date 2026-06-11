# ─── utils.py ─────────────────────────────────────────────
# Shared helpers: HTTP session, retry logic, link collection,
# progress saving.  Import these — don't re-implement them.
# ──────────────────────────────────────────────────────────

import random
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential

from config import (
    BASE_URL,
    LINK_PATTERNS,
    MAX_DELAY,
    MAX_RETRIES,
    MIN_DELAY,
    REQUEST_TIMEOUT,
    RETRY_MAX_WAIT,
    RETRY_MIN_WAIT,
    SAVE_EVERY,
    STATIC_HEADERS,
)

# ── Shared session & UA ───────────────────────────────────
_ua = UserAgent()
session = requests.Session()


# ── HTTP helpers ──────────────────────────────────────────

def _build_headers() -> dict:
    """Return a fresh headers dict with a randomised User-Agent."""
    return {**STATIC_HEADERS, "User-Agent": _ua.random}


@retry(
    wait=wait_exponential(min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    stop=stop_after_attempt(MAX_RETRIES),
)
def safe_get(url: str) -> requests.Response:
    """GET *url* with retries, randomised UA, and a shared session."""
    session.headers.update(_build_headers())
    res = session.get(url, timeout=REQUEST_TIMEOUT)
    res.raise_for_status()
    return res


def polite_delay() -> None:
    """Sleep for a random duration within the configured delay range."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ── Link collection ───────────────────────────────────────

def get_all_links(entity: str) -> list[str]:
    """
    Paginate through ``/entity?page=N`` and return every unique
    detail-page URL that matches the entity's path pattern.

    Parameters
    ----------
    entity : str
        One of the keys defined in ``config.LINK_PATTERNS``
        (e.g. ``"companies"``, ``"brands"``, ``"medicines"``).
    """
    pattern  = LINK_PATTERNS[entity]
    prefix   = pattern["prefix"]
    id_index = pattern["id_index"]
    base_list_url = f"{BASE_URL}/{entity}"

    all_links: list[str] = []
    page = 1

    while True:
        url = f"{base_list_url}?page={page}"
        print(f"  Fetching page {page}: {url}")

        try:
            res = safe_get(url)
        except Exception as exc:
            print(f"  Failed to fetch page {page}: {exc}")
            break

        soup = BeautifulSoup(res.text, "html.parser")

        # Collect links matching the entity pattern
        tags = soup.select(f"a[href*='/{prefix}/']")
        page_links: list[str] = []

        for tag in tags:
            href  = tag["href"].strip("/")
            parts = href.split("/")
            # Expect:  prefix / numeric-id / slug  (3 parts)
            if (
                len(parts) == 3
                and parts[0] == prefix
                and parts[id_index].isdigit()
            ):
                full_url = f"{BASE_URL}/{href}"
                if full_url not in page_links:
                    page_links.append(full_url)

        if not page_links:
            print(f"  No {entity} found on page {page} — stopping.")
            break

        all_links.extend(page_links)
        print(
            f"  Found {len(page_links)} on page {page} "
            f"| Total so far: {len(all_links)}"
        )

        # Stop if there is no "next page" control
        next_btn = soup.find("a", string="›") or soup.find("a", rel="next")
        if not next_btn:
            print("  No more pages.")
            break

        page += 1
        polite_delay()

    return list(dict.fromkeys(all_links))  # deduplicate, preserve order


# ── Persistence ───────────────────────────────────────────

def save_progress(data: list[dict], filepath: str) -> None:
    """Write *data* to *filepath* as UTF-8 CSV (overwrites)."""
    pd.DataFrame(data).to_csv(filepath, index=False, encoding="utf-8-sig")


def scrape_all(
    entity: str,
    output_file: str,
    scrape_fn,          # callable(url) -> dict
) -> list[dict]:
    """
    Generic scraping loop shared by every entity scraper.

    1. Collects all detail-page URLs via ``get_all_links``.
    2. Calls *scrape_fn* on each URL.
    3. Saves progress every ``SAVE_EVERY`` items.
    4. Returns the full list of result dicts.

    Parameters
    ----------
    entity      : key for ``LINK_PATTERNS`` / display label
    output_file : destination CSV path
    scrape_fn   : a function ``(url: str) -> dict`` that parses one page
    """
    print(f"\n[Step 1] Collecting {entity} links from all pages...\n")
    links = get_all_links(entity)
    total = len(links)
    print(f"\nTotal unique {entity} found: {total}")

    print(f"\n[Step 2] Scraping {entity} details...\n")
    all_data: list[dict] = []

    for i, url in enumerate(links, 1):
        print(f"[{i}/{total}] {url}")
        try:
            row = scrape_fn(url)
            all_data.append(row)
            print(f"  ✓ {row.get('name', 'Unknown')}")
        except Exception as exc:
            print(f"  ✗ Failed: {exc}")
            all_data.append({"url": url, "name": "FAILED", "error": str(exc)})

        if i % SAVE_EVERY == 0:
            save_progress(all_data, output_file)
            print(f"  [Progress saved: {i} / {total}]")

        polite_delay()

    save_progress(all_data, output_file)
    print(f"\nDone! Scraped {len(all_data)} {entity}. Saved to: {output_file}")
    return all_data


