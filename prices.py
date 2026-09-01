"""Live price lookup — pure `requests` against Yahoo's public chart JSON.

Deliberately NOT yfinance: it pulls native C-extensions (curl_cffi, frozendict)
that segfault (signal 11) on Streamlit Cloud under Yahoo rate-limiting. This
module is stdlib + requests only, never raises, and degrades to an empty quote
so the dashboard still renders when Yahoo is unreachable.
"""

import time

import requests
import streamlit as st

CACHE_TTL = 300  # 5 minutes — NSE data via Yahoo is ~15 min delayed anyway

# Browser-like headers reduce Yahoo's bot throttling.
_YHEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# A quote whose last trade is older than this is a dead/zombie listing.
STALE_QUOTE_SECONDS = 10 * 86400


def _yahoo_chart(symbol: str, params: dict):
    """Call Yahoo's chart endpoint, return result[0] or None. Never raises."""
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            resp = requests.get(
                f"https://{host}/v8/finance/chart/{symbol}",
                params=params, headers=_YHEADERS, timeout=6,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            result = (data.get("chart") or {}).get("result")
            if result:
                return result[0]
        except Exception:
            continue
    return None


def candidates(ticker: str) -> list[str]:
    """Symbols to try, in order. Bare NSE codes get .NS, then .BO as a fallback."""
    t = str(ticker or "").strip().upper()
    if not t:
        return []
    if "." in t or ":" in t:  # already exchange-qualified
        return [t]
    return [t + ".NS", t + ".BO"]


def _quote_from(res: dict) -> dict | None:
    meta = res.get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price in (None, 0):
        return None

    rmt = meta.get("regularMarketTime")
    if rmt:
        try:
            if (time.time() - float(rmt)) > STALE_QUOTE_SECONDS:
                return None
        except (TypeError, ValueError):
            pass

    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
    try:
        price, prev = float(price), float(prev or 0)
    except (TypeError, ValueError):
        return None

    change = price - prev if prev else 0.0
    return {
        "price": price,
        "prev_close": prev,
        "change": change,
        "pct_change": (change / prev * 100) if prev else 0.0,
        "symbol": meta.get("symbol", ""),
        "currency": meta.get("currency", "INR"),
        "as_of": rmt,
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_quote(ticker: str) -> dict | None:
    """Latest quote for one ticker, or None if nothing usable came back."""
    for sym in candidates(ticker):
        res = _yahoo_chart(sym, {"range": "1d", "interval": "1d"})
        if res:
            q = _quote_from(res)
            if q:
                return q
    return None


# Hard ceiling on how long one page render may spend fetching prices. The
# dashboard must always paint: a slow or throttled Yahoo degrades to "no live
# price" rather than hanging the app (a long hang trips Streamlit Cloud's
# health check and shows the blank "Oh no" crash).
TOTAL_BUDGET_SECONDS = 20


def fetch_quotes(tickers: tuple) -> dict:
    """Quotes for many tickers, newest cache first, within a total time budget.

    Deliberately NOT cached itself — `fetch_quote` holds the per-ticker cache,
    so adding one new stock re-fetches only that stock instead of the whole board.
    """
    out = {}
    started = time.time()
    for t in tickers:
        if not t:
            continue
        if time.time() - started > TOTAL_BUDGET_SECONDS:
            break  # leave the rest unpriced; they fall back to manual price
        q = fetch_quote(t)
        if q:
            out[str(t).strip().upper()] = q
    return out
