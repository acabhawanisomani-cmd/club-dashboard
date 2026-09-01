"""Ledger & Ticker — investment club dashboard.

Four linked tabs (Recommendations, Attendance, Fund & Expenses, Members) over a
shared Neon Postgres database, so anyone with the link sees the same live data.
Viewing is open; every write is behind a shared club passcode.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import database as db
import prices
import ui

# ── Page setup ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Ledger & Ticker", page_icon="📈",
                   layout="wide", initial_sidebar_state="collapsed")
ui.inject()

CALLS = ["Buy", "Sell", "Hold"]
ROLES = ["Member", "President", "Secretary", "Treasurer", "Analyst"]
CREDIT_CATS = ["Monthly contribution", "Joining fee", "One-time top-up",
               "Interest / dividend", "Refund", "Other"]
DEBIT_CATS = ["Stock purchase", "Brokerage & charges", "Venue / meeting",
              "Refreshments", "Data subscription", "Stationery", "Other"]
MODES = ["UPI", "Bank transfer", "Cash", "Cheque", "Card"]


def _secret(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return default


CLUB_NAME = _secret("CLUB_NAME", "Investment Club")
PASSCODE = str(_secret("APP_PASSCODE", ""))


# ── Formatting helpers ─────────────────────────────────────────────────────
def inr(n) -> str:
    """Indian-grouped rupee string (12,34,567.89)."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "₹0.00"
    sign = "−" if n < 0 else ""
    whole, frac = divmod(round(abs(n), 2), 1)
    s = str(int(whole))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{sign}₹{s}.{int(round(frac * 100)):02d}"


def as_date(v):
    """Normalise a DB date (date object on PG, string on SQLite) to date."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def fmt_date(v) -> str:
    d = as_date(v)
    return d.strftime("%d %b %Y") if d else "—"


def num(v, default=0.0) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Access control ─────────────────────────────────────────────────────────
def can_edit() -> bool:
    if not PASSCODE:          # no passcode configured → fully open
        return True
    return bool(st.session_state.get("unlocked"))


def gate(action: str = "make changes") -> bool:
    """True if writes are allowed; otherwise explains how to unlock."""
    if can_edit():
        return True
    st.info(f"Enter the club passcode in the sidebar to {action}. "
            "Everything stays readable without it.")
    return False


def sidebar():
    with st.sidebar:
        st.markdown(f"### {CLUB_NAME}")
        if not PASSCODE:
            st.warning("No passcode is set. Anyone with the link can edit or "
                       "delete data. Add `APP_PASSCODE` in Streamlit secrets.")
        elif can_edit():
            st.success("Editing unlocked")
            if st.button("Lock editing", use_container_width=True):
                st.session_state.unlocked = False
                rerun_fresh()
        else:
            st.caption("You can read everything. Editing needs the club passcode.")
            with st.form("unlock", clear_on_submit=True):
                entered = st.text_input("Club passcode", type="password")
                if st.form_submit_button("Unlock editing", use_container_width=True):
                    if entered == PASSCODE:
                        st.session_state.unlocked = True
                        rerun_fresh()
                    else:
                        st.error("That passcode is not right.")

        st.divider()
        st.caption("Storage: " + ("Neon Postgres (shared)" if db.using_postgres()
                                  else "local SQLite (this machine only)"))
        st.caption("Prices: Yahoo Finance, delayed ~15 minutes.")

        if can_edit():
            st.divider()
            with st.expander("Danger zone"):
                st.caption("Deletes every member, call, meeting and ledger entry.")
                confirm = st.text_input("Type ERASE to confirm", key="wipe_confirm")
                if st.button("Clear all data", type="secondary",
                             use_container_width=True):
                    if confirm.strip().upper() == "ERASE":
                        db.wipe_all()
                        st.success("All data cleared.")
                        rerun_fresh()
                    else:
                        st.error("Type ERASE in the box first.")


# ── Derived data ───────────────────────────────────────────────────────────
def reco_return(row, cmp_val):
    """Return %, signed for the call direction. Sell gains when price falls."""
    entry = num(row.get("reco_price"))
    exit_p = num(row.get("exit_price")) if row.get("status") == "closed" else num(cmp_val)
    if not entry or not exit_p:
        return None
    direction = -1 if row.get("action") == "Sell" else 1
    return (exit_p - entry) / entry * 100 * direction


def cmp_for(row, quotes):
    """Live quote if we have one, else the manually entered fallback price."""
    sym = str(row.get("symbol") or "").strip().upper()
    q = quotes.get(sym)
    if q:
        return q["price"], "live"
    manual = row.get("manual_price")
    if manual is not None and not pd.isna(manual) and float(manual) > 0:
        return float(manual), "manual"
    return None, "none"


def eligible(joined_on, meet_date) -> bool:
    j, m = as_date(joined_on), as_date(meet_date)
    if not j or not m:
        return True
    return j <= m


def attendance_stats(members_df, meetings_df, att_df):
    """present/held/rate per member id, counting only post-joining meetings."""
    out = {}
    present_map = {}
    if not att_df.empty:
        for mid, grp in att_df.groupby("meeting_id"):
            present_map[mid] = set(grp["member_id"].tolist())
    for _, m in members_df.iterrows():
        present = held = 0
        for _, mt in meetings_df.iterrows():
            if not eligible(m["joined_on"], mt["meet_date"]):
                continue
            held += 1
            if m["id"] in present_map.get(mt["id"], set()):
                present += 1
        out[m["id"]] = {"present": present, "held": held,
                        "rate": (present / held * 100) if held else None}
    return out, present_map


def watchlist(members_df, meetings_df, present_map):
    """Active members who missed ALL of the latest three meetings."""
    last3 = meetings_df.head(3)
    if len(last3) < 3:
        return [], last3
    oldest = last3.iloc[-1]["meet_date"]
    flagged = []
    for _, m in members_df.iterrows():
        if not m["active"] or not eligible(m["joined_on"], oldest):
            continue
        if all(m["id"] not in present_map.get(mt["id"], set())
               for _, mt in last3.iterrows()):
            flagged.append(m)
    return flagged, last3


# ── Boot ───────────────────────────────────────────────────────────────────
if "schema_ready" not in st.session_state:
    db.init_schema()
    st.session_state.schema_ready = True

if db.INIT_ERROR:
    st.error("Could not reach the database.")
    st.code(db.INIT_ERROR)
    st.markdown(
        "**Fix:** in Streamlit → Settings → Secrets, check `DATABASE_URL` matches "
        "the **pooled** connection string from Neon's Connect dialog. A Neon free-tier "
        "database also takes a few seconds to wake — reload once before digging further."
    )
    st.stop()

# Every Streamlit interaction re-runs this whole script. Uncached, that meant
# eight fresh TLS connections to Neon per click, which is what made the app
# feel slow. One cached call now covers a page render; writes clear it, and the
# short TTL keeps other members' changes appearing quickly.
DATA_TTL = 30  # seconds


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def _load_all():
    return (db.list_members(), db.list_recos(), db.list_meetings(),
            db.list_attendance(), db.list_ledger(), db.list_reco_members(),
            db.is_empty())


def refresh_data():
    """Drop the cached snapshot so the next render reads the database."""
    _load_all.clear()


def rerun_fresh():
    refresh_data()
    st.rerun()


sidebar()

(members_df, recos_df, meetings_df, att_df, ledger_df,
 recomem_df, data_is_empty) = _load_all()

# reco id -> [member ids]; a call may have several authors
authors_by_reco = {}
if not recomem_df.empty:
    for _rid, _grp in recomem_df.groupby("reco_id"):
        authors_by_reco[_rid] = list(_grp["member_id"])


def authors_of(reco_row):
    """Member ids credited with a call, falling back to the legacy single column."""
    ids = authors_by_reco.get(reco_row["id"])
    if ids:
        return ids
    single = reco_row.get("member_id")
    return [single] if single is not None and not pd.isna(single) else []


def author_names(reco_row):
    ids = authors_of(reco_row)
    if not ids:
        return "Former member"
    return ", ".join(name_by_id.get(i, "Former member") for i in ids)

active_members = members_df[members_df["active"]] if not members_df.empty else members_df
name_by_id = dict(zip(members_df["id"], members_df["name"])) if not members_df.empty else {}

# Live quotes for every symbol on the board
# (ticker, company name) pairs — the name is the last-resort way to resolve a
# BSE-only or oddly-coded scrip whose ticker was typed wrong.
if recos_df.empty:
    holdings = ()
else:
    seen, pairs = set(), []
    for _, _r in recos_df.iterrows():
        _sym = str(_r.get("symbol") or "").strip().upper()
        if _sym and _sym not in seen:
            seen.add(_sym)
            pairs.append((_sym, str(_r.get("stock") or "")))
    holdings = tuple(pairs)
quotes = prices.fetch_quotes(holdings) if holdings else {}

stats, present_map = attendance_stats(members_df, meetings_df, att_df) \
    if not members_df.empty else ({}, {})
flagged, last3 = watchlist(members_df, meetings_df, present_map) \
    if not members_df.empty and not meetings_df.empty else ([], meetings_df.head(0))

# ── Header + KPIs ──────────────────────────────────────────────────────────
ui.masthead(CLUB_NAME, "Recommendations, attendance and the club fund — one shared board.")

credit = ledger_df[ledger_df["entry_type"] == "credit"]["amount"].astype(float).sum() \
    if not ledger_df.empty else 0.0
debit = ledger_df[ledger_df["entry_type"] == "debit"]["amount"].astype(float).sum() \
    if not ledger_df.empty else 0.0
balance = credit - debit

open_recos = recos_df[recos_df["status"] != "closed"] if not recos_df.empty else recos_df
returns = []
if not open_recos.empty:
    for _, r in open_recos.iterrows():
        c, _src = cmp_for(r, quotes)
        v = reco_return(r, c)
        if v is not None:
            returns.append(v)
avg_return = sum(returns) / len(returns) if returns else None

n_open = len(open_recos) if not open_recos.empty else 0
ui.kpis([
    ("Fund in hand", inr(balance),
     f"Collected {inr(credit)} · Spent {inr(debit)}"),
    ("Live calls", str(n_open),
     f"{len(returns)} of {n_open} priced"),
    ("Avg return, live calls", ui.signed(avg_return),
     "from the live market price"),
    ("Attendance watch",
     (f'<span class="loss">{len(flagged)}</span>' if flagged else "0")
     if len(meetings_df) >= 3 else "—",
     f"{len(meetings_df)} of 3 meetings logged" if len(meetings_df) < 3
     else ("missed the last 3 meetings" if flagged else "everyone attended recently")),
])

if len(flagged):
    ui.alert("warn", "Missed the last 3 meetings",
             "Absent from all of " + ", ".join(fmt_date(d) for d in last3["meet_date"])
             + '.</p><div class="chips" style="margin-top:9px">'
             + "".join(f'<span class="chip absent">{ui.esc(m["name"])}</span>' for m in flagged)
             + "</div><p>")

if data_is_empty and can_edit():
    with st.container(border=True):
        st.markdown("**Want to see it filled in first?** Load a small demo club — "
                    "four members, four calls, four meetings and a starter ledger.")
        if st.button("Load demo data"):
            db.load_demo()
            rerun_fresh()

tab_r, tab_a, tab_f, tab_m = st.tabs(
    ["📈 Recommendations", "🗓️ Attendance", "💰 Fund & Expenses", "👥 Members"])


# ══════════════════════════════════════════════════════════════════════════
# Recommendations
# ══════════════════════════════════════════════════════════════════════════
with tab_r:
    left, right = st.columns([4, 1])
    if right.button("↻ Refresh prices", use_container_width=True):
        prices.fetch_quote.clear()   # per-ticker cache; fetch_quotes is uncached
        rerun_fresh()

    if members_df.empty:
        st.info("Add your members first — every call is credited to someone on the roster.")
    elif recos_df.empty:
        st.info("No recommendations logged yet.")
    else:
        f1, f2, f3, f4 = st.columns([1.3, 1.3, 1.2, 1.2])
        who_filter = f1.selectbox(
            "Member", ["Everyone"] + list(members_df["name"]), key="rf_member")
        call_filter = f2.selectbox("Call", ["All"] + CALLS, key="rf_call")
        sort_by = f3.selectbox(
            "Sort by", ["Date", "Member", "Stock", "Call", "Return"], key="rf_sort")
        sort_dir = f4.selectbox("Order", ["Newest · highest · Z–A",
                                          "Oldest · lowest · A–Z"], key="rf_dir")
        show_closed = left.checkbox("Include closed positions", value=False)
        rows, missing = [], []
        for _, r in recos_df.iterrows():
            if r["status"] == "closed" and not show_closed:
                continue
            c, src = cmp_for(r, quotes)
            if src == "none" and r["status"] != "closed":
                missing.append(r["stock"])
            ret = reco_return(r, c)
            held = (date.today() - as_date(r["reco_date"])).days \
                if as_date(r["reco_date"]) else None
            rows.append({
                "Date": as_date(r["reco_date"]),
                "Recommended by": author_names(r),
                "_author_ids": authors_of(r),
                "Stock": r["stock"],
                "Ticker": r["symbol"] or "",
                "Call": r["action"],
                "Reco price": num(r["reco_price"]),
                "CMP": c,
                "Source": (quotes.get(str(r["symbol"] or "").strip().upper(), {})
                           .get("symbol") or "live") if src == "live"
                          else ("entered" if src == "manual" else "—"),
                "Return %": ret,
                "Days": held,
                "Status": "Closed" if r["status"] == "closed" else "Live",
            })

        # filter
        if who_filter != "Everyone":
            _wid = next((int(m["id"]) for _, m in members_df.iterrows()
                         if m["name"] == who_filter), None)
            rows = [r for r in rows if _wid in r["_author_ids"]]
        if call_filter != "All":
            rows = [r for r in rows if r["Call"] == call_filter]

        # sort — None returns sort last either way, never above real numbers
        _desc = sort_dir.startswith("Newest")
        _keys = {
            "Date":   lambda r: (r["Date"] is None, r["Date"] or date.min),
            "Member": lambda r: r["Recommended by"].lower(),
            "Stock":  lambda r: str(r["Stock"]).lower(),
            "Call":   lambda r: str(r["Call"]),
            "Return": lambda r: (r["Return %"] is None,
                                 r["Return %"] if r["Return %"] is not None else 0.0),
        }
        rows.sort(key=_keys[sort_by], reverse=_desc)
        # Rows with no date / no price belong at the bottom whichever way the
        # sort runs — a blank is not "the highest value".
        if sort_by in ("Date", "Return"):
            _field = "Date" if sort_by == "Date" else "Return %"
            rows = ([r for r in rows if r[_field] is not None] +
                    [r for r in rows if r[_field] is None])
        # recompute the summary from what is actually on screen
        returns = [r["Return %"] for r in rows if r["Return %"] is not None]

        if not rows:
            ui.alert("info", "Nothing matches those filters",
                     "Try setting Member back to Everyone, or Call back to All.")

        trs = []
        for r in rows:
            trs.append([
                fmt_date(r["Date"]),
                ui.esc(r["Recommended by"]),
                f'<span class="lt-strong">{ui.esc(r["Stock"])}</span>' +
                (f' <span class="lt-muted lt-tiny">{ui.esc(r["Ticker"])}</span>'
                 if r["Ticker"] else ""),
                ui.pill(r["Call"]) + (' ' + ui.pill("Closed", "closed")
                                      if r["Status"] == "Closed" else ""),
                inr(r["Reco price"]) if r["Reco price"] else "—",
                (inr(r["CMP"]) if r["CMP"] else '<span class="lt-muted">—</span>') +
                (f'<div class="lt-muted lt-tiny">{r["Source"]}</div>'
                 if r["CMP"] else ""),
                ui.signed(r["Return %"]),
                f'{r["Days"]}d' if r["Days"] is not None else "—",
            ])
        up = sum(1 for v in returns if v > 0)
        foot = (f'<span class="lt-strong">{len(returns)}</span> priced · '
                f'<span class="gain lt-strong">{up} up</span> · '
                f'<span class="loss lt-strong">{len(returns) - up} down</span>'
                ) if returns else "No market prices yet."
        ui.card("Recommendation tracker",
                "Prices refresh from the market, delayed about 15 minutes. "
                "A Sell call gains when the price falls.",
                ui.table([("Date", False), ("Recommended by", False), ("Stock", False),
                          ("Call", False), ("Reco price", True), ("Market price", True),
                          ("Return", True), ("Held", True)], trs, 880),
                foot)

        if missing:
            ui.alert("warn", f"No live price for {len(missing)} call(s)",
                     ui.esc(", ".join(missing)) +
                     ".<br>Things to try, in order: use the <strong>bare NSE code</strong> "
                     "(RELIANCE, not RELIANCE.NS) &middot; for a <strong>BSE-only</strong> scrip "
                     "use its 6-digit BSE code (e.g. 543931) or add <strong>.BO</strong> "
                     "(TATAINVEST.BO) &middot; some names have changed: <strong>ZOMATO</strong> is "
                     "now <strong>ETERNAL</strong>, and <strong>TATAMOTORS</strong> no longer "
                     "resolves after the demerger. If nothing works, set a "
                     "<strong>Fallback price</strong> when editing the call and the return "
                     "will still be calculated.")

    st.divider()
    if gate("add or change recommendations") and not members_df.empty:
        with st.expander("➕ Add a recommendation", expanded=recos_df.empty):
            with st.form("add_reco", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                d = c1.date_input("Date", value=date.today(), format="DD/MM/YYYY")
                who = c2.multiselect(
                    "Recommended by", list(active_members["id"]),
                    default=list(active_members["id"])[:1],
                    format_func=lambda i: name_by_id.get(i, "?"),
                    help="Pick more than one if several members backed the same call.")
                call = c3.selectbox("Call", CALLS)
                c4, c5, c6 = st.columns(3)
                stock = c4.text_input("Stock name", placeholder="Tata Motors")
                sym = c5.text_input("NSE ticker", placeholder="TATAMOTORS",
                                    help="Bare NSE code. Used to fetch the live price.")
                rp = c6.number_input("Recommended price (₹)", min_value=0.0, step=0.05,
                                     format="%.2f")
                c7, c8 = st.columns(2)
                tgt = c7.number_input("Target price (₹, optional)", min_value=0.0,
                                      step=0.05, format="%.2f")
                fallback = c8.number_input("Fallback price (₹, if not on Yahoo)",
                                           min_value=0.0, step=0.05, format="%.2f")
                notes = st.text_area("Rationale (optional)", height=70)
                if st.form_submit_button("Add recommendation", type="primary"):
                    if not stock.strip():
                        st.error("Enter the stock name.")
                    elif not who:
                        st.error("Pick at least one member who recommended it.")
                    elif rp <= 0:
                        st.error("Enter the recommended price.")
                    else:
                        db.add_reco(d, [int(x) for x in who], stock.strip(),
                                    sym.strip().upper(), call, rp,
                                    tgt or None, notes.strip(), fallback or None)
                        st.success(f"Added {stock.strip()}.")
                        rerun_fresh()

        if not recos_df.empty:
            with st.expander("✏️ Edit or delete a recommendation"):
                labels = {int(r["id"]): f'{fmt_date(r["reco_date"])} · {r["stock"]} '
                                        f'({r["action"]}) · {author_names(r)}'
                          for _, r in recos_df.iterrows()}
                rid = st.selectbox("Pick a recommendation", list(labels),
                                   format_func=lambda i: labels[i], key="edit_reco_pick")
                cur = recos_df[recos_df["id"] == rid].iloc[0]
                with st.form("edit_reco"):
                    c1, c2, c3 = st.columns(3)
                    d = c1.date_input("Date", value=as_date(cur["reco_date"]) or date.today(),
                                      format="DD/MM/YYYY")
                    mem_ids = list(members_df["id"])
                    _cur_authors = [i for i in authors_by_reco.get(rid, [])
                                    if i in mem_ids] or (
                        [cur["member_id"]] if cur["member_id"] in mem_ids else [])
                    who = c2.multiselect(
                        "Recommended by", mem_ids, default=_cur_authors,
                        format_func=lambda i: name_by_id.get(i, "?"))
                    call = c3.selectbox("Call", CALLS, index=CALLS.index(cur["action"])
                                        if cur["action"] in CALLS else 0)
                    c4, c5, c6 = st.columns(3)
                    stock = c4.text_input("Stock name", value=cur["stock"] or "")
                    sym = c5.text_input("NSE ticker", value=cur["symbol"] or "")
                    rp = c6.number_input("Recommended price (₹)", min_value=0.0, step=0.05,
                                         value=num(cur["reco_price"]), format="%.2f")
                    c7, c8, c9 = st.columns(3)
                    tgt = c7.number_input("Target price (₹)", min_value=0.0, step=0.05,
                                          value=num(cur["target_price"]), format="%.2f")
                    fallback = c8.number_input("Fallback price (₹)", min_value=0.0, step=0.05,
                                               value=num(cur["manual_price"]), format="%.2f")
                    status = c9.selectbox("Position", ["open", "closed"],
                                          index=0 if cur["status"] != "closed" else 1,
                                          format_func=lambda s: "Live — still tracking"
                                          if s == "open" else "Closed — exited")
                    c10, c11 = st.columns(2)
                    ex_p = c10.number_input("Exit price (₹)", min_value=0.0, step=0.05,
                                            value=num(cur["exit_price"]), format="%.2f")
                    ex_d = c11.date_input("Exit date",
                                          value=as_date(cur["exit_date"]) or date.today(),
                                          format="DD/MM/YYYY")
                    notes = st.text_area("Rationale", value=cur["notes"] or "", height=70)

                    s1, s2 = st.columns([3, 1])
                    if s1.form_submit_button("Save changes", type="primary"):
                        if not who:
                            st.error("Pick at least one member who recommended it.")
                            st.stop()
                        db.update_reco(int(rid), [int(x) for x in who], d, stock.strip(),
                                       sym.strip().upper(), call, rp, tgt or None,
                                       notes.strip(), status,
                                       ex_p or None,
                                       ex_d if status == "closed" else None,
                                       fallback or None)
                        st.success("Saved.")
                        rerun_fresh()
                    if s2.form_submit_button("Delete"):
                        db.delete_reco(int(rid))
                        st.success("Deleted.")
                        rerun_fresh()


# ══════════════════════════════════════════════════════════════════════════
# Attendance
# ══════════════════════════════════════════════════════════════════════════
with tab_a:
    if members_df.empty:
        st.info("Add your members first — attendance is marked against the roster.")
    elif meetings_df.empty:
        st.info("No meetings logged yet.")
    else:
        rows = []
        for _, mt in meetings_df.iterrows():
            present_ids = present_map.get(mt["id"], set())
            present = [name_by_id[i] for i in present_ids if i in name_by_id]
            absent = [m["name"] for _, m in members_df.iterrows()
                      if m["active"] and eligible(m["joined_on"], mt["meet_date"])
                      and m["id"] not in present_ids]
            rows.append({
                "Date": as_date(mt["meet_date"]),
                "Place": mt["place"] or "—",
                "Agenda": mt["agenda"] or "",
                "Present": ", ".join(sorted(present)) or "—",
                "Absent": ", ".join(sorted(absent)) or "full attendance",
                "#": len(present),
            })
        trs = []
        for r in rows:
            present = [x for x in r["Present"].split(", ") if x and x != "—"]
            absent = [x for x in r["Absent"].split(", ") if x and x != "full attendance"]
            trs.append([
                fmt_date(r["Date"]) + (f'<div class="lt-muted lt-tiny">{ui.esc(r["Agenda"])}</div>'
                                       if r["Agenda"] else ""),
                ui.esc(r["Place"]),
                '<div class="chips">' + ("".join(
                    f'<span class="chip present">{ui.esc(n)}</span>' for n in present)
                    or '<span class="lt-muted lt-tiny">nobody marked present</span>') + "</div>",
                '<div class="chips">' + ("".join(
                    f'<span class="chip absent">{ui.esc(n)}</span>' for n in absent)
                    or '<span class="chip present">full attendance</span>') + "</div>",
            ])
        ui.card("Attendance register",
                "Everyone on the active roster who is not marked present is recorded absent.",
                ui.table([("Date", False), ("Place", False), ("Present", False),
                          ("Absent", False)], trs, 820))

        if len(meetings_df) < 3:
            ui.alert("info", "Absence watch starts after 3 meetings",
                     f"{len(meetings_df)} logged so far. Once there are three, anyone who "
                     "missed all three is flagged at the top.")

        arows = [[ui.esc(m["name"]) + ("" if m["active"] else " " + ui.pill("Inactive", "off")),
                  str(stats[m["id"]]["present"]), str(stats[m["id"]]["held"]),
                  ui.meter(stats[m["id"]]["rate"])]
                 for _, m in members_df.iterrows()]
        ui.card("Attendance by member",
                "Counted only from meetings held after each member joined.",
                ui.table([("Member", False), ("Present", True), ("Meetings", True),
                          ("Attendance", True)], arows, 520))

    st.divider()
    if gate("log or edit meetings") and not members_df.empty:
        with st.expander("➕ Log a meeting", expanded=meetings_df.empty):
            with st.form("add_meeting", clear_on_submit=True):
                c1, c2 = st.columns(2)
                d = c1.date_input("Date", value=date.today(), format="DD/MM/YYYY")
                place = c2.text_input("Place of meeting",
                                      placeholder="Cafe Coffee Day, MG Road")
                agenda = st.text_input("Agenda / notes (optional)")
                present = st.multiselect(
                    "Members present", list(active_members["id"]),
                    default=list(active_members["id"]),
                    format_func=lambda i: name_by_id.get(i, "?"))
                if st.form_submit_button("Log meeting", type="primary"):
                    if not place.strip():
                        st.error("Enter where you met.")
                    else:
                        db.add_meeting(d, place.strip(), agenda.strip(), present)
                        st.success("Meeting logged.")
                        rerun_fresh()

        if not meetings_df.empty:
            with st.expander("✏️ Edit or delete a meeting"):
                labels = {int(m["id"]): f'{fmt_date(m["meet_date"])} · {m["place"] or "—"}'
                          for _, m in meetings_df.iterrows()}
                mid = st.selectbox("Pick a meeting", list(labels),
                                   format_func=lambda i: labels[i], key="edit_meet_pick")
                cur = meetings_df[meetings_df["id"] == mid].iloc[0]
                cur_present = [i for i in present_map.get(mid, set())]
                with st.form("edit_meeting"):
                    c1, c2 = st.columns(2)
                    d = c1.date_input("Date", value=as_date(cur["meet_date"]) or date.today(),
                                      format="DD/MM/YYYY")
                    place = c2.text_input("Place of meeting", value=cur["place"] or "")
                    agenda = st.text_input("Agenda / notes", value=cur["agenda"] or "")
                    opts = list(members_df["id"])
                    present = st.multiselect(
                        "Members present", opts,
                        default=[i for i in cur_present if i in opts],
                        format_func=lambda i: name_by_id.get(i, "?"))
                    s1, s2 = st.columns([3, 1])
                    if s1.form_submit_button("Save changes", type="primary"):
                        db.update_meeting(int(mid), d, place.strip(), agenda.strip(), present)
                        st.success("Saved.")
                        rerun_fresh()
                    if s2.form_submit_button("Delete"):
                        db.delete_meeting(int(mid))
                        st.success("Deleted.")
                        rerun_fresh()


# ══════════════════════════════════════════════════════════════════════════
# Fund & Expenses
# ══════════════════════════════════════════════════════════════════════════
with tab_f:
    ui.kpis([("Total collected", f'<span class="gain">{inr(credit)}</span>', "all credit entries"),
             ("Total spent", f'<span class="loss">{inr(debit)}</span>', "all debit entries"),
             ("Balance in hand", inr(balance),
              "the club is overdrawn" if balance < 0 else "available to deploy")])

    if ledger_df.empty:
        st.info("The ledger is empty. Start with the contributions members have paid in.")
    else:
        running, rows = 0.0, []
        for _, l in ledger_df.iterrows():
            amt = num(l["amount"])
            is_credit = l["entry_type"] == "credit"
            running += amt if is_credit else -amt
            rows.append({
                "Date": as_date(l["entry_date"]),
                "Particulars": l["category"] or ("Contribution" if is_credit else "Expense"),
                "Description": l["description"] or "",
                "Member": l["member_name"] or "—",
                "Mode": l["mode"] or "—",
                # Pre-formatted strings, not numbers: Streamlit's NumberColumn
                # renders an empty cell as the literal "None", which would sit in
                # every unused side of the ledger. Strings also give proper
                # Indian grouping (₹1,20,000.00) that NumberColumn cannot.
                "Credit": inr(amt) if is_credit else "",
                "Debit": "" if is_credit else inr(amt),
                "Balance": inr(running),
            })
        trs = [[
            fmt_date(r["Date"]),
            f'<span class="lt-strong">{ui.esc(r["Particulars"])}</span>' +
            (f'<div class="lt-muted lt-tiny">{ui.esc(r["Description"])}</div>'
             if r["Description"] else ""),
            ui.esc(r["Member"]),
            f'<span class="lt-muted">{ui.esc(r["Mode"])}</span>',
            f'<span class="gain lt-strong">{r["Credit"]}</span>' if r["Credit"] else "",
            f'<span class="loss lt-strong">{r["Debit"]}</span>' if r["Debit"] else "",
            f'<span class="lt-strong">{r["Balance"]}</span>',
        ] for r in rows[::-1]]
        ui.card("Fund & expense ledger",
                "Credit is money into the club. Debit is money out.",
                ui.table([("Date", False), ("Particulars", False), ("Member", False),
                          ("Mode", False), ("Credit", True), ("Debit", True),
                          ("Balance", True)], trs, 900),
                "Newest first; the balance column is the running total in date order.")

        if not members_df.empty:
            crows = []
            for _, m in members_df.iterrows():
                paid = ledger_df[(ledger_df["member_id"] == m["id"]) &
                                 (ledger_df["entry_type"] == "credit")]["amount"].astype(float).sum()
                spent = ledger_df[(ledger_df["member_id"] == m["id"]) &
                                  (ledger_df["entry_type"] == "debit")]["amount"].astype(float).sum()
                crows.append([ui.esc(m["name"]),
                              f'<span class="lt-strong">{inr(paid)}</span>',
                              ui.meter((paid / credit * 100) if credit else None),
                              f'<span class="lt-muted">{inr(spent) if spent else "—"}</span>'])
            ui.card("Contribution by member",
                    "What each member has paid in, and anything they spent for the club.",
                    ui.table([("Member", False), ("Contributed", True),
                              ("Share of fund", True), ("Spent on club", True)], crows, 560))

    st.divider()
    if gate("record money in or out") and not members_df.empty:
        add_c, add_d = st.columns(2)
        with add_c:
            with st.expander("➕ Record a contribution (credit)", expanded=ledger_df.empty):
                with st.form("add_credit", clear_on_submit=True):
                    d = st.date_input("Date", value=date.today(), format="DD/MM/YYYY",
                                      key="cr_date")
                    amt = st.number_input("Amount (₹)", min_value=0.0, step=100.0,
                                          format="%.2f", key="cr_amt")
                    cat = st.selectbox("Category", CREDIT_CATS, key="cr_cat")
                    who = st.selectbox("Paid by", list(members_df["id"]),
                                       format_func=lambda i: name_by_id.get(i, "?"),
                                       key="cr_who")
                    mode = st.selectbox("Mode", MODES, key="cr_mode")
                    desc = st.text_input("Description (optional)",
                                         placeholder="September contribution", key="cr_desc")
                    if st.form_submit_button("Add to fund", type="primary"):
                        if amt <= 0:
                            st.error("Enter an amount greater than zero.")
                        else:
                            db.add_entry(d, "credit", amt, cat, int(who), mode, desc.strip())
                            st.success("Contribution recorded.")
                            rerun_fresh()
        with add_d:
            with st.expander("➖ Record an expense (debit)"):
                with st.form("add_debit", clear_on_submit=True):
                    d = st.date_input("Date", value=date.today(), format="DD/MM/YYYY",
                                      key="db_date")
                    amt = st.number_input("Amount (₹)", min_value=0.0, step=100.0,
                                          format="%.2f", key="db_amt")
                    cat = st.selectbox("Expense head", DEBIT_CATS, key="db_cat")
                    who = st.selectbox("Paid by (optional)", [None] + list(members_df["id"]),
                                       format_func=lambda i: "Club fund — no member"
                                       if i is None else name_by_id.get(i, "?"),
                                       key="db_who")
                    mode = st.selectbox("Mode", MODES, key="db_mode")
                    desc = st.text_input("Description (optional)",
                                         placeholder="Zerodha charges for October", key="db_desc")
                    if st.form_submit_button("Record expense", type="primary"):
                        if amt <= 0:
                            st.error("Enter an amount greater than zero.")
                        else:
                            db.add_entry(d, "debit", amt, cat,
                                         int(who) if who else None, mode, desc.strip())
                            st.success("Expense recorded.")
                            rerun_fresh()

        if not ledger_df.empty:
            with st.expander("✏️ Edit or delete an entry"):
                labels = {int(l["id"]): f'{fmt_date(l["entry_date"])} · '
                                        f'{"Credit" if l["entry_type"] == "credit" else "Debit"} '
                                        f'{inr(l["amount"])} · {l["category"] or "—"}'
                          for _, l in ledger_df.iterrows()}
                eid = st.selectbox("Pick an entry", list(labels),
                                   format_func=lambda i: labels[i], key="edit_entry_pick")
                cur = ledger_df[ledger_df["id"] == eid].iloc[0]
                with st.form("edit_entry"):
                    c1, c2, c3 = st.columns(3)
                    d = c1.date_input("Date", value=as_date(cur["entry_date"]) or date.today(),
                                      format="DD/MM/YYYY")
                    etype = c2.selectbox("Type", ["credit", "debit"],
                                         index=0 if cur["entry_type"] == "credit" else 1,
                                         format_func=str.title)
                    amt = c3.number_input("Amount (₹)", min_value=0.0, step=100.0,
                                          value=num(cur["amount"]), format="%.2f")
                    c4, c5, c6 = st.columns(3)
                    cats = CREDIT_CATS if etype == "credit" else DEBIT_CATS
                    cat = c4.selectbox("Category", cats,
                                       index=cats.index(cur["category"])
                                       if cur["category"] in cats else 0)
                    opts = [None] + list(members_df["id"])
                    who = c5.selectbox("Member", opts,
                                       index=opts.index(cur["member_id"])
                                       if cur["member_id"] in opts else 0,
                                       format_func=lambda i: "—" if i is None
                                       else name_by_id.get(i, "?"))
                    mode = c6.selectbox("Mode", MODES,
                                        index=MODES.index(cur["mode"])
                                        if cur["mode"] in MODES else 0)
                    desc = st.text_input("Description", value=cur["description"] or "")
                    s1, s2 = st.columns([3, 1])
                    if s1.form_submit_button("Save changes", type="primary"):
                        db.update_entry(int(eid), d, etype, amt, cat,
                                        int(who) if who else None, mode, desc.strip())
                        st.success("Saved.")
                        rerun_fresh()
                    if s2.form_submit_button("Delete"):
                        db.delete_entry(int(eid))
                        st.success("Deleted.")
                        rerun_fresh()


# ══════════════════════════════════════════════════════════════════════════
# Members
# ══════════════════════════════════════════════════════════════════════════
with tab_m:
    if members_df.empty:
        st.info("Build the roster — add everyone in the club once, then pick "
                "names from a list everywhere else.")
    else:
        flagged_ids = {m["id"] for m in flagged}
        rows = []
        for _, m in members_df.iterrows():
            # counts co-authored calls too, not just the primary recommender
            n_calls = 0
            if not recos_df.empty:
                for _, _r in recos_df.iterrows():
                    if m["id"] in authors_of(_r):
                        n_calls += 1
            paid = ledger_df[(ledger_df["member_id"] == m["id"]) &
                             (ledger_df["entry_type"] == "credit")]["amount"].astype(float).sum() \
                if not ledger_df.empty else 0.0
            rows.append({
                "Member": m["name"],
                "Role": m["role"] or "Member",
                "Status": "Active" if m["active"] else "Inactive",
                "Watchlist": "⚠️" if m["id"] in flagged_ids else "",
                "Calls": n_calls,
                "Attendance": (stats[m["id"]]["rate"] or 0),
                "Contributed": paid,
            })
        trs = [[
            f'<span class="lt-strong">{ui.esc(r["Member"])}</span>' +
            ("" if r["Status"] == "Active" else " " + ui.pill("Inactive", "off")) +
            (" " + ui.pill("Watchlist", "sell") if r["Watchlist"] else ""),
            ui.pill(r["Role"], "role"),
            str(r["Calls"]),
            ui.meter(r["Attendance"]),
            f'<span class="lt-strong">{inr(r["Contributed"])}</span>',
        ] for r in rows]
        ui.card("Member master",
                "The roster every other tab reads from — calls, attendance and money all point here.",
                ui.table([("Member", False), ("Role", False), ("Calls", True),
                          ("Attendance", True),
                          ("Contributed", True)], trs, 820),
                f'{len(members_df)} member(s) · {inr(credit)} collected in total.')

    st.divider()
    if gate("add or change members"):
        with st.expander("➕ Add a member", expanded=members_df.empty):
            with st.form("add_member", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                name = c1.text_input("Full name", placeholder="Priya Nair")
                role = c2.selectbox("Role", ROLES)
                joined = c3.date_input("Joined on", value=date.today(), format="DD/MM/YYYY")
                st.caption("Attendance counts only meetings held on or after the "
                           "joining date, so a new member is never unfairly flagged.")
                if st.form_submit_button("Add member", type="primary"):
                    if not name.strip():
                        st.error("Enter the member's name.")
                    else:
                        db.add_member(name.strip(), role, joined, True)
                        st.success(f"Added {name.strip()}.")
                        rerun_fresh()

        if not members_df.empty:
            with st.expander("✏️ Edit or remove a member"):
                labels = {int(m["id"]): m["name"] for _, m in members_df.iterrows()}
                pid = st.selectbox("Pick a member", list(labels),
                                   format_func=lambda i: labels[i], key="edit_member_pick")
                cur = members_df[members_df["id"] == pid].iloc[0]
                n_recos = int((recos_df["member_id"] == pid).sum()) if not recos_df.empty else 0
                n_led = int((ledger_df["member_id"] == pid).sum()) if not ledger_df.empty else 0
                n_meet = sum(1 for s in present_map.values() if pid in s)

                with st.form("edit_member"):
                    c1, c2, c3, c4 = st.columns(4)
                    name = c1.text_input("Full name", value=cur["name"])
                    role = c2.selectbox("Role", ROLES,
                                        index=ROLES.index(cur["role"])
                                        if cur["role"] in ROLES else 0)
                    joined = c3.date_input("Joined on",
                                           value=as_date(cur["joined_on"]) or date.today(),
                                           format="DD/MM/YYYY")
                    active = c4.selectbox("Status", [True, False],
                                          index=0 if cur["active"] else 1,
                                          format_func=lambda b: "Active" if b
                                          else "Inactive — skip in attendance")
                    if n_recos or n_led or n_meet:
                        st.caption(
                            f"Linked to {n_recos} recommendation(s), {n_led} ledger "
                            f"entry(ies) and {n_meet} meeting(s). Removing keeps those "
                            "records but drops the name. Marking inactive is usually better.")
                    s1, s2 = st.columns([3, 1])
                    if s1.form_submit_button("Save changes", type="primary"):
                        db.update_member(int(pid), name.strip(), role, joined, active)
                        st.success("Saved.")
                        rerun_fresh()
                    if s2.form_submit_button("Remove"):
                        db.delete_member(int(pid))
                        st.success("Removed.")
                        rerun_fresh()
