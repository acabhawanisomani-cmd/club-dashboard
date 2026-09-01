"""Live price lookup — pure `requests` against Yahoo's public chart JSON.

Deliberately NOT yfinance: it pulls native C-extensions (curl_cffi, frozendict)
that segfault (signal 11) on Streamlit Cloud under Yahoo rate-limiting. This
module is stdlib + requests only, never raises, and degrades to an empty quote
so the dashboard still renders when Yahoo is unreachable.
"""

import re
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
    """Symbols to try, in order.

    Order matters and is not arbitrary:
      1. `.NS`     — the ordinary NSE main-board listing.
      2. `-SM.NS`  — NSE SME. Yahoo ALSO keeps a plain-symbol zombie for many SME
                     scrips, frozen years in the past (VINYAS.NS still reports a
                     price from 2023). The staleness check in `_quote_from`
                     rejects those, and this variant is what actually trades.
      3. `.BO`     — BSE, which covers alphabetic BSE tickers (RELIANCE.BO etc).
    A purely numeric input is a BSE scrip code (500325 → 500325.BO).
    """
    t = str(ticker or "").strip().upper()
    if not t:
        return []
    if "." in t or ":" in t:      # already exchange-qualified, trust it
        return [t]
    if t.isdigit():               # BSE scrip code
        return [t + ".BO"]
    if t.endswith("-SM"):         # SME spelled out explicitly
        return [t + ".NS"]
    return [t + ".NS", t + "-SM.NS", t + ".BO"]


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def resolve_symbol(ticker: str) -> str | None:
    """Ask Yahoo's search endpoint what this ticker is actually called.

    The safety net for anything the suffix guesses miss — BSE-only scrips,
    renamed companies, SME listings. Cached for a day: a symbol's identity does
    not change intraday, and this must not add a request per page load.
    """
    for variant in _search_variants(ticker):
        try:
            r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                             params={"q": variant, "quotesCount": 8, "newsCount": 0},
                             headers=_YHEADERS, timeout=6)
            if r.status_code != 200:
                continue
            quotes = [x for x in (r.json().get("quotes") or []) if x.get("symbol")]
        except Exception:
            continue
        # Prefer Indian exchanges, NSE ahead of BSE, before anything else.
        for want in ("NSI", "BSE", "BOM"):
            for x in quotes:
                if x.get("exchange") == want:
                    return x["symbol"]
        for x in quotes:
            if str(x["symbol"]).endswith((".NS", ".BO")):
                return x["symbol"]
    return None


# Yahoo's search matches literally: "Eternal Ltd" finds nothing while "Eternal"
# finds ETERNAL.NS. So try the text as given, then progressively simpler forms.
_SUFFIXES = re.compile(
    r"\b(ltd|limited|pvt|private|inc|corp|corporation|company|co|plc)\b\.?",
    re.IGNORECASE)


def _search_variants(text: str) -> list:
    q = re.sub(r"[^A-Za-z0-9&.\- ]", " ", str(text or "")).strip()
    if not q:
        return []
    out = [q]
    cleaned = re.sub(r"\s+", " ", _SUFFIXES.sub("", q)).strip(" .-&")
    if cleaned and cleaned.lower() != q.lower():
        out.append(cleaned)
    words = cleaned.split()
    if len(words) > 2:
        out.append(" ".join(words[:2]))
    if len(words) > 1:
        out.append(words[0])
    seen, uniq = set(), []
    for v in out:
        k = v.lower()
        if v and k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq[:4]


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
def fetch_quote(ticker: str, name: str = "") -> dict | None:
    """Latest quote for one holding, or None if nothing usable came back.

    Three passes, cheapest first:
      1. suffix variants of the ticker (no extra request),
      2. Yahoo search on the ticker,
      3. Yahoo search on the COMPANY NAME — this is what rescues BSE-only and
         oddly-coded scrips whose ticker nobody remembers correctly
         (Bombay Oxygen is BOMOXY-B1.BO; Ador Welding is ADOR, not ADORWELD).
    """
    tried = []

    def _try(sym):
        if not sym or sym in tried:
            return None
        tried.append(sym)
        res = _yahoo_chart(sym, {"range": "1d", "interval": "1d"})
        return _quote_from(res) if res else None

    for sym in candidates(ticker):
        q = _try(sym)
        if q:
            return q

    q = _try(resolve_symbol(ticker))
    if q:
        return q

    if name and str(name).strip().lower() != str(ticker or "").strip().lower():
        q = _try(resolve_symbol(name))
        if q:
            return q
    return None


# Hard ceiling on how long one page render may spend fetching prices. The
# dashboard must always paint: a slow or throttled Yahoo degrades to "no live
# price" rather than hanging the app (a long hang trips Streamlit Cloud's
# health check and shows the blank "Oh no" crash).
TOTAL_BUDGET_SECONDS = 20


def fetch_quotes(items: tuple) -> dict:
    """Quotes for many holdings, within a total time budget.

    `items` is a tuple of (ticker, company_name) pairs; the name is only used if
    the ticker cannot be resolved. Keyed by the UPPERCASED ticker as typed.

    Deliberately NOT cached itself — `fetch_quote` holds the per-holding cache,
    so adding one stock re-fetches only that stock instead of the whole board.
    """
    out = {}
    started = time.time()
    for item in items:
        ticker, name = (item if isinstance(item, (tuple, list)) else (item, ""))
        if not ticker:
            continue
        if time.time() - started > TOTAL_BUDGET_SECONDS:
            break  # leave the rest unpriced; they fall back to manual price
        q = fetch_quote(ticker, name)
        if q:
            out[str(ticker).strip().upper()] = q
    return out
