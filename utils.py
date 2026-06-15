# ─── utils.py ─────────────────────────────────────────────
# Shared helpers: HTTP session, retry logic, link collection,
# progress saving.  Import these — don't re-implement them.
# ──────────────────────────────────────────────────────────

import random
import time

import pandas as pd
import requests
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential

from config import (
    MAX_DELAY,
    MAX_RETRIES,
    MIN_DELAY,
    REQUEST_TIMEOUT,
    RETRY_MAX_WAIT,
    RETRY_MIN_WAIT,
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

