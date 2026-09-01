"""Database layer for the Investment Club Dashboard.

Neon PostgreSQL in production (connection string in Streamlit secrets as
DATABASE_URL), SQLite fallback for local runs so the app works offline.

Deliberately mirrors the connection strategy proven in smallcase_dashboard:
short connect timeout + a few quick retries, because Neon free-tier databases
auto-suspend and a long hang trips Streamlit Cloud's health check ("Oh no").
"""

import os
import time
import sqlite3
from datetime import date

import pandas as pd

# ── Connection setup ───────────────────────────────────────────────────────
_USE_PG = False
_DATABASE_URL = None
INIT_ERROR = None  # surfaced in the UI instead of crashing the app

try:
    import streamlit as st
    if "DATABASE_URL" in st.secrets:
        _DATABASE_URL = st.secrets["DATABASE_URL"]
        import psycopg2  # noqa: F401
        _USE_PG = True
except Exception:
    pass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "club_data.db")


def using_postgres() -> bool:
    return _USE_PG


def get_connection():
    """Open a connection. Never rewrites the Neon host — paste the POOLED URL."""
    if _USE_PG:
        import psycopg2
        last_err = None
        for attempt in range(4):
            try:
                conn = psycopg2.connect(
                    _DATABASE_URL, connect_timeout=15,
                    keepalives=1, keepalives_idle=30,
                    keepalives_interval=10, keepalives_count=5,
                )
                conn.autocommit = False
                return conn
            except Exception as e:
                last_err = e
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s
                else:
                    raise last_err
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ph() -> str:
    """Placeholder token — %s for PostgreSQL, ? for SQLite."""
    return "%s" if _USE_PG else "?"


def _q(sql: str) -> str:
    """Translate '?' placeholders in a literal query to the active dialect."""
    return sql.replace("?", "%s") if _USE_PG else sql


# ── Schema ─────────────────────────────────────────────────────────────────
_PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS members (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT DEFAULT 'Member',
        joined_on DATE,
        active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS recos (
        id SERIAL PRIMARY KEY,
        reco_date DATE NOT NULL,
        member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
        stock TEXT NOT NULL,
        symbol TEXT,
        action TEXT NOT NULL DEFAULT 'Buy',
        reco_price NUMERIC,
        manual_price NUMERIC,
        target_price NUMERIC,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        exit_price NUMERIC,
        exit_date DATE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS meetings (
        id SERIAL PRIMARY KEY,
        meet_date DATE NOT NULL,
        place TEXT,
        agenda TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS attendance (
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
        PRIMARY KEY (meeting_id, member_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ledger (
        id SERIAL PRIMARY KEY,
        entry_date DATE NOT NULL,
        entry_type TEXT NOT NULL,
        amount NUMERIC NOT NULL,
        category TEXT,
        member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
        mode TEXT,
        description TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
]

_SQLITE_SCHEMA = [
    s.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
     .replace("TIMESTAMPTZ DEFAULT NOW()", "TEXT DEFAULT (datetime('now','localtime'))")
     .replace("BOOLEAN DEFAULT TRUE", "INTEGER DEFAULT 1")
     .replace("NUMERIC", "REAL")
    for s in _PG_SCHEMA
]


def init_schema():
    """Create tables if absent. Records failure in INIT_ERROR rather than raising."""
    global INIT_ERROR
    try:
        conn = get_connection()
        cur = conn.cursor()
        for stmt in (_PG_SCHEMA if _USE_PG else _SQLITE_SCHEMA):
            cur.execute(stmt)
        conn.commit()
        conn.close()
        INIT_ERROR = None
    except Exception as e:
        INIT_ERROR = f"{type(e).__name__}: {e}"
    return INIT_ERROR


# ── Generic helpers ────────────────────────────────────────────────────────
def _read(sql: str, params=()) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql_query(_q(sql), conn, params=params)
    finally:
        conn.close()


def _write(sql: str, params=()) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(_q(sql), params)
        conn.commit()
    finally:
        conn.close()


def _write_returning_id(sql: str, params=()) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        if _USE_PG:
            cur.execute(_q(sql) + " RETURNING id", params)
            new_id = cur.fetchone()[0]
        else:
            cur.execute(sql, params)
            new_id = cur.lastrowid
        conn.commit()
        return int(new_id)
    finally:
        conn.close()


# ── Members ────────────────────────────────────────────────────────────────
def list_members(include_inactive: bool = True) -> pd.DataFrame:
    sql = "SELECT id, name, role, joined_on, active FROM members"
    if not include_inactive:
        sql += " WHERE active = " + ("TRUE" if _USE_PG else "1")
    sql += " ORDER BY name"
    df = _read(sql)
    if not df.empty:
        df["active"] = df["active"].astype(bool)
    return df


def add_member(name, role, joined_on, active=True) -> int:
    return _write_returning_id(
        "INSERT INTO members (name, role, joined_on, active) VALUES (?, ?, ?, ?)",
        (name, role, joined_on, bool(active)),
    )


def update_member(mid, name, role, joined_on, active) -> None:
    _write("UPDATE members SET name=?, role=?, joined_on=?, active=? WHERE id=?",
           (name, role, joined_on, bool(active), mid))


def delete_member(mid) -> None:
    _write("DELETE FROM members WHERE id=?", (mid,))


# ── Recommendations ────────────────────────────────────────────────────────
def list_recos() -> pd.DataFrame:
    return _read("""
        SELECT r.id, r.reco_date, r.member_id, m.name AS member_name, r.stock,
               r.symbol, r.action, r.reco_price, r.manual_price, r.target_price,
               r.notes, r.status, r.exit_price, r.exit_date
        FROM recos r LEFT JOIN members m ON m.id = r.member_id
        ORDER BY r.reco_date DESC, r.id DESC
    """)


def add_reco(reco_date, member_id, stock, symbol, action, reco_price,
             target_price, notes, manual_price=None) -> int:
    return _write_returning_id(
        """INSERT INTO recos (reco_date, member_id, stock, symbol, action,
           reco_price, target_price, notes, manual_price, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
        (reco_date, member_id, stock, symbol, action, reco_price,
         target_price, notes, manual_price),
    )


def update_reco(rid, reco_date, member_id, stock, symbol, action, reco_price,
                target_price, notes, status, exit_price, exit_date,
                manual_price=None) -> None:
    _write("""UPDATE recos SET reco_date=?, member_id=?, stock=?, symbol=?,
              action=?, reco_price=?, target_price=?, notes=?, status=?,
              exit_price=?, exit_date=?, manual_price=? WHERE id=?""",
           (reco_date, member_id, stock, symbol, action, reco_price,
            target_price, notes, status, exit_price, exit_date,
            manual_price, rid))


def set_manual_price(rid, price) -> None:
    _write("UPDATE recos SET manual_price=? WHERE id=?", (price, rid))


def delete_reco(rid) -> None:
    _write("DELETE FROM recos WHERE id=?", (rid,))


# ── Meetings & attendance ──────────────────────────────────────────────────
def list_meetings() -> pd.DataFrame:
    return _read("""SELECT id, meet_date, place, agenda FROM meetings
                    ORDER BY meet_date DESC, id DESC""")


def list_attendance() -> pd.DataFrame:
    return _read("SELECT meeting_id, member_id FROM attendance")


def add_meeting(meet_date, place, agenda, present_ids) -> int:
    mid = _write_returning_id(
        "INSERT INTO meetings (meet_date, place, agenda) VALUES (?, ?, ?)",
        (meet_date, place, agenda),
    )
    _set_attendance(mid, present_ids)
    return mid


def update_meeting(mid, meet_date, place, agenda, present_ids) -> None:
    _write("UPDATE meetings SET meet_date=?, place=?, agenda=? WHERE id=?",
           (meet_date, place, agenda, mid))
    _set_attendance(mid, present_ids)


def _set_attendance(meeting_id, present_ids) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(_q("DELETE FROM attendance WHERE meeting_id=?"), (meeting_id,))
        for pid in present_ids:
            cur.execute(_q("INSERT INTO attendance (meeting_id, member_id) VALUES (?, ?)"),
                        (meeting_id, int(pid)))
        conn.commit()
    finally:
        conn.close()


def delete_meeting(mid) -> None:
    _write("DELETE FROM attendance WHERE meeting_id=?", (mid,))
    _write("DELETE FROM meetings WHERE id=?", (mid,))


# ── Ledger ─────────────────────────────────────────────────────────────────
def list_ledger() -> pd.DataFrame:
    return _read("""
        SELECT l.id, l.entry_date, l.entry_type, l.amount, l.category,
               l.member_id, m.name AS member_name, l.mode, l.description
        FROM ledger l LEFT JOIN members m ON m.id = l.member_id
        ORDER BY l.entry_date ASC, l.id ASC
    """)


def add_entry(entry_date, entry_type, amount, category, member_id, mode, description) -> int:
    return _write_returning_id(
        """INSERT INTO ledger (entry_date, entry_type, amount, category,
           member_id, mode, description) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entry_date, entry_type, amount, category, member_id, mode, description),
    )


def update_entry(eid, entry_date, entry_type, amount, category, member_id, mode, description) -> None:
    _write("""UPDATE ledger SET entry_date=?, entry_type=?, amount=?, category=?,
              member_id=?, mode=?, description=? WHERE id=?""",
           (entry_date, entry_type, amount, category, member_id, mode, description, eid))


def delete_entry(eid) -> None:
    _write("DELETE FROM ledger WHERE id=?", (eid,))


# ── Demo data ──────────────────────────────────────────────────────────────
def is_empty() -> bool:
    for t in ("members", "recos", "meetings", "ledger"):
        if int(_read(f"SELECT COUNT(*) AS n FROM {t}")["n"].iloc[0]) > 0:
            return False
    return True


def wipe_all() -> None:
    for t in ("attendance", "recos", "ledger", "meetings", "members"):
        _write(f"DELETE FROM {t}")


def load_demo() -> None:
    """A small, obviously-fake club so the tabs can be judged before real entry."""
    from datetime import timedelta
    today = date.today()

    people = [("Aarav Mehta", "President"), ("Priya Nair", "Treasurer"),
              ("Rohan Iyer", "Secretary"), ("Sneha Kulkarni", "Analyst")]
    ids = [add_member(n, r, today - timedelta(days=120), True) for n, r in people]

    # Tickers chosen because they resolve on Yahoo today. ZOMATO and
    # TATAMOTORS deliberately avoided — both are dead symbols now (renamed to
    # ETERNAL / demerged) and would show as unpriced.
    for stock, sym, act, price, who, ago in [
        ("Reliance Industries", "RELIANCE", "Buy", 1180.00, 0, 60),
        ("HDFC Bank", "HDFCBANK", "Buy", 640.00, 1, 45),
        ("Infosys", "INFY", "Buy", 1290.00, 2, 30),
        ("ITC", "ITC", "Sell", 292.00, 3, 22),
    ]:
        add_reco(today - timedelta(days=ago), ids[who], stock, sym, act,
                 price, None, "Demo entry — replace with your own.")

    for ago, place, present in [
        (42, "Nehru Place, Delhi", [0, 1, 2, 3]),
        (28, "Cafe Coffee Day, CP", [0, 1, 2]),
        (14, "Aarav's residence", [0, 1]),
        (3, "Nehru Place, Delhi", [0, 1, 3]),
    ]:
        add_meeting(today - timedelta(days=ago), place,
                    "Portfolio review and new ideas", [ids[i] for i in present])

    for i in ids:
        add_entry(today - timedelta(days=45), "credit", 5000, "Joining fee",
                  i, "UPI", "Initial corpus")
    add_entry(today - timedelta(days=20), "debit", 1240, "Brokerage & charges",
              ids[1], "Bank transfer", "Q3 brokerage and DP charges")
    add_entry(today - timedelta(days=3), "debit", 860, "Refreshments",
              ids[0], "Cash", "Meeting refreshments")
