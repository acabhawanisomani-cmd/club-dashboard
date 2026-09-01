# Ledger & Ticker — investment club dashboard

A shared dashboard for an investment club. **Anyone with the link can open it, no
account needed.** Four tabs, all keyed to one member master:

| Tab | What it holds |
|---|---|
| **Recommendations** | Date, who recommended it, stock, Buy/Sell/Hold, recommended price, **live CMP**, and return-to-date. A Sell call gains when the price falls. |
| **Attendance** | Date, place, who attended. Absentees are derived automatically, plus a watchlist of anyone who missed **all of the latest 3 meetings**. |
| **Fund & Expenses** | Credit/debit ledger with a running balance, and per-member contribution totals. |
| **Members** | The roster every other tab reads from, with each member's calls, average return, attendance and contributions. |

Prices come from Yahoo Finance and are **delayed roughly 15 minutes** — fine for
tracking multi-week calls, not for intraday trading.

---

## Deploying (one time, ~15 minutes)

### 1. Put this folder in a GitHub repo

```bash
cd club_dashboard
git init && git add . && git commit -m "Club dashboard"
```

Then create an empty repo on GitHub and push to it. `.gitignore` already keeps
`secrets.toml` and the local SQLite file out.

### 2. Create the database (Neon, free)

1. Sign up at [neon.tech](https://neon.tech) and create a project.
2. Open **Connect**, **tick "Connection pooling"**, and copy the connection string.
   It must be the **pooled** endpoint — the one with `-pooler` in the host.
3. You do not need to create any tables. The app creates them on first run.

### 3. Deploy on Streamlit Community Cloud

1. At [share.streamlit.io](https://share.streamlit.io), **New app** → pick the repo,
   main file `app.py`.
2. **Advanced settings → Python version → 3.12.** This matters: newer Python
   lacks wheels for parts of the pinned stack and the build fails. `runtime.txt`
   is ignored by Streamlit Cloud, so it has to be set here.
3. **Secrets** — paste:

```toml
DATABASE_URL = "postgresql://user:pass@ep-xxxx-pooler.region.aws.neon.tech/dbname?sslmode=require"
APP_PASSCODE = "pick-something-and-share-it-in-the-club-group"
CLUB_NAME    = "Your Club Name"
```

4. Deploy. Share the resulting `https://<something>.streamlit.app` link with the club.

### 4. First run

Open the app, enter the passcode in the sidebar, and either **Load demo data** to
look around (then **Danger zone → Clear all data**), or go straight to the
**Members** tab and add your roster — everything else keys off it.

---

## How access works

- **Reading is open.** Anyone with the link sees every tab, no login.
- **Writing needs the passcode** from `APP_PASSCODE`, entered once per browser
  session in the sidebar. Share it in the club group.
- Leave `APP_PASSCODE` out entirely and the app becomes fully open to edits — the
  sidebar warns you when that is the case.
- The passcode stops accidents and casual outsiders. It is not real
  authentication: anyone who has it can change anything, and there is no
  per-member audit trail.

---

## Tickers

Enter the **bare NSE code** in the Ticker field — `RELIANCE`, `HDFCBANK`, `INFY`.
The app appends `.NS`, and falls back to `.BO` (BSE) if NSE has nothing.

Some symbols are dead and will not price — `ZOMATO` is now `ETERNAL`, and
`TATAMOTORS` no longer resolves after the demerger. When a call shows no live
price, the app names it and you have two fixes:

- correct the ticker, or
- set a **Fallback price** when editing that call — used whenever Yahoo has
  nothing, so unlisted or odd scrips still show a return.

---

## Things not to change without testing

These are scars from a previous multi-day outage on the sibling `smallcase_dashboard`
app. They are load-bearing.

- **Never add `yfinance`.** It pulls native C-extensions (`curl_cffi`,
  `frozendict`) that segfault (signal 11) on Streamlit Cloud under Yahoo rate
  limiting. `prices.py` uses plain `requests` against Yahoo's public chart JSON
  endpoint instead — pure Python, and it fails gracefully to "no live price".
- **Keep `numpy==1.26.4` and `pandas==2.2.3` pinned.** Unpinned, a rebuild pulls
  numpy 2.x, whose binaries segfault inside pandas.
- **Avoid casually adding C-extension packages** (`lxml`, `xlrd`, `html5lib`).
  They have broken the build before.
- **Use the pooled Neon URL, and never rewrite the host in code.** A previous
  string-replace bug produced invalid hostnames that hung until timeout.
- `prices.py` caps total price-fetch time per page render (`TOTAL_BUDGET_SECONDS`)
  so a slow Yahoo degrades to blank prices instead of hanging the app. A long hang
  trips Streamlit Cloud's health check and shows the blank "Oh no" crash.
- Touch `requirements.txt` whenever `database.py` or `prices.py` gains a new
  function — Streamlit Cloud otherwise serves a cached module and calls to the new
  helper fail with `AttributeError` until the environment rebuilds.

**Debugging a blank "Oh no":** share.streamlit.io → your app → **Manage app**
(bottom right, owner only) → read the log. A blank "Oh no" is a host-level crash
(segfault / OOM / DB hang), not a Python error — read the actual signal before
changing anything.

---

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

With no `DATABASE_URL` secret the app falls back to a local SQLite file
(`club_data.db`) so you can try things offline. The sidebar always says which
storage is in use. For a local passcode, copy `.streamlit/secrets.toml.example`
to `.streamlit/secrets.toml`.

## Changing the look

`ui.py` holds everything visual. The colour palette is the `:root` block at the
top of `CSS` — change those hex values and the whole app follows, because every
rule reads from them. `.streamlit/config.toml` sets the few colours Streamlit
paints itself (widget backgrounds, the sidebar), so keep the two in step.

The data tables are rendered as HTML by `ui.table()` rather than `st.dataframe`,
which cannot be themed. The trade-off is deliberate: you lose click-to-sort, and
gain pills, coloured returns, inline meters and blank cells instead of the literal
"None" that `st.dataframe` prints for empty numbers.

## Files

| File | Role |
|---|---|
| `app.py` | All four tabs, KPIs, forms and the passcode gate |
| `ui.py` | Theme: CSS that replaces Streamlit's chrome, plus the card/table/pill/meter builders |
| `database.py` | Neon Postgres / SQLite layer, schema, CRUD, demo data |
| `prices.py` | Yahoo chart-JSON price fetch with caching and a time budget |
