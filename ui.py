"""Look and feel.

Streamlit's default page chrome is the reason a Streamlit app looks like a
Streamlit app. This module replaces it: it hides the toolbar/footer, sets a
proper type pairing, and renders the data-heavy parts (KPI strip, tables) as
styled HTML instead of st.metric/st.dataframe, which cannot be themed.

Forms stay as native widgets — those look fine and behave better than anything
hand-rolled.
"""

import streamlit as st

# ── palette ────────────────────────────────────────────────────────────────
# Petrol blue accent, deliberately not green, so it never collides with the
# gain/loss red-green that has to carry meaning in a P&L table.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=Public+Sans:wght@400;500;600;700&display=swap');

:root{
  --bg:#0C1317; --surface:#121C22; --surface-2:#18242B; --surface-3:#203038;
  --line:#25353E; --line-strong:#374B56;
  --ink:#E7EEF2; --ink-2:#95A8B4; --ink-3:#728693;
  --accent:#63BDDD; --accent-soft:#15303C; --accent-line:#2A5568;
  --gain:#47C98A; --gain-soft:#12312A;
  --loss:#FF8272; --loss-soft:#3A1E1E;
  --hold:#D6A93F; --hold-soft:#332813;
}

/* strip Streamlit's chrome */
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"]{display:none !important;}
[data-testid="stHeader"]{background:transparent !important; height:0 !important;}
[data-testid="stToolbar"]{right:8px; top:4px;}
[data-testid="stAppViewContainer"]{background:var(--bg);}
.block-container{padding-top:1.6rem !important; padding-bottom:4rem !important; max-width:1320px;}

html, body, [class*="css"], [data-testid="stAppViewContainer"]{
  font-family:"Public Sans","Segoe UI",system-ui,sans-serif;
  color:var(--ink);
}
h1,h2,h3{font-family:"Newsreader",Georgia,serif !important; font-weight:500 !important; letter-spacing:-.01em;}

/* masthead */
.lt-eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
  color:var(--accent);display:flex;align-items:center;gap:8px;margin-bottom:2px;}
.lt-eyebrow::before{content:"";width:18px;height:1px;background:var(--accent-line);}
.lt-title{font-family:"Newsreader",Georgia,serif;font-size:clamp(26px,3.4vw,34px);
  line-height:1.15;margin:0 0 2px;color:var(--ink);}
.lt-sub{color:var(--ink-2);font-size:13.5px;margin-bottom:18px;}

/* KPI strip */
.lt-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:1px;background:var(--line);border:1px solid var(--line);
  border-radius:10px;overflow:hidden;margin:0 0 20px;}
.lt-kpi{background:var(--surface);padding:13px 16px 14px;display:flex;flex-direction:column;gap:3px;min-width:0;}
.lt-k-label{font-size:10.5px;font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--ink-3);}
.lt-k-value{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;
  font-size:23px;font-weight:500;letter-spacing:-.02em;line-height:1.2;}
.lt-k-sub{font-size:12px;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}

/* tabs */
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:2px;border-bottom:1px solid var(--line);background:transparent;}
[data-testid="stTabs"] [data-baseweb="tab"]{
  background:transparent !important;padding:10px 15px 11px !important;
  font-size:14px !important;font-weight:600 !important;color:var(--ink-2) !important;border-radius:0 !important;}
[data-testid="stTabs"] [aria-selected="true"]{color:var(--accent) !important;}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{background:var(--accent) !important;height:2px !important;}
[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none !important;}

/* cards */
.lt-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  overflow:hidden;margin:18px 0 6px;}
.lt-card-head{padding:14px 16px;border-bottom:1px solid var(--line);}
.lt-card-head h2{font-size:18px;margin:0;}
.lt-hint{font-size:12.5px;color:var(--ink-2);margin-top:2px;}
.lt-foot{padding:11px 16px;border-top:1px solid var(--line);background:var(--surface-2);
  font-size:12.5px;color:var(--ink-2);}

/* tables */
.lt-scroll{overflow-x:auto;}
table.lt{border-collapse:collapse;width:100%;font-size:14px;}
table.lt thead th{text-align:left;font-size:10.5px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);padding:9px 12px;
  border-bottom:1px solid var(--line);white-space:nowrap;}
table.lt tbody td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle;}
table.lt tbody tr:last-child td{border-bottom:0;}
table.lt tbody tr:hover{background:var(--surface-2);}
table.lt td.n, table.lt th.n{font-family:"IBM Plex Mono",monospace;
  font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap;}
.lt-strong{font-weight:600;}
.lt-muted{color:var(--ink-2);}
.lt-tiny{font-size:12px;}
.gain{color:var(--gain);} .loss{color:var(--loss);} .flat{color:var(--ink-2);}

/* pills and chips */
.pill{display:inline-flex;align-items:center;padding:2px 9px;border-radius:99px;
  font-size:11.5px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;
  border:1px solid transparent;white-space:nowrap;}
.pill.buy{background:var(--gain-soft);color:var(--gain);border-color:rgba(71,201,138,.3);}
.pill.sell{background:var(--loss-soft);color:var(--loss);border-color:rgba(255,130,114,.3);}
.pill.hold{background:var(--hold-soft);color:var(--hold);border-color:rgba(214,169,63,.32);}
.pill.closed{background:var(--surface-3);color:var(--ink-2);border-color:var(--line-strong);}
.pill.role{background:var(--accent-soft);color:var(--accent);border-color:var(--accent-line);
  text-transform:none;letter-spacing:0;font-weight:600;}
.pill.off{background:var(--surface-3);color:var(--ink-3);border-color:var(--line);
  text-transform:none;letter-spacing:0;font-weight:600;}
.chips{display:flex;flex-wrap:wrap;gap:5px;}
.chip{display:inline-flex;font-size:12.5px;background:var(--surface-2);
  border:1px solid var(--line);border-radius:99px;padding:2px 9px;white-space:nowrap;}
.chip.present{background:var(--gain-soft);border-color:rgba(71,201,138,.25);color:var(--gain);}
.chip.absent{background:var(--loss-soft);border-color:rgba(255,130,114,.25);color:var(--loss);}

/* meter */
.meter{display:flex;align-items:center;gap:9px;justify-content:flex-end;}
.meter .track{width:64px;height:6px;border-radius:99px;background:var(--surface-3);overflow:hidden;flex:none;}
.meter .fill{height:100%;border-radius:99px;}
.meter .val{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;
  font-size:13px;min-width:40px;text-align:right;}

/* alerts */
.lt-alert{border:1px solid var(--line);border-left:3px solid var(--ink-3);
  background:var(--surface-2);border-radius:6px;padding:12px 14px;margin:6px 0 4px;}
.lt-alert h3{font-family:"Public Sans",sans-serif !important;font-size:15px;font-weight:700 !important;margin:0;}
.lt-alert p{margin:3px 0 0;font-size:13px;color:var(--ink-2);}
.lt-alert.warn{border-left-color:var(--loss);background:var(--loss-soft);border-color:rgba(255,130,114,.22);}
.lt-alert.warn p{color:var(--ink);}
.lt-alert.ok{border-left-color:var(--gain);background:var(--gain-soft);border-color:rgba(71,201,138,.22);}
.lt-alert.info{border-left-color:var(--accent);background:var(--accent-soft);border-color:var(--accent-line);}

/* native widgets, tightened up */
.stButton>button{border-radius:6px;font-weight:600;font-size:13.5px;border:1px solid var(--line-strong);}
.stButton>button[kind="primary"]{background:var(--accent);border-color:var(--accent);color:#08171E;}
.stTextInput input, .stNumberInput input, .stDateInput input, .stTextArea textarea{
  background:var(--surface) !important;border-radius:6px !important;}
[data-testid="stExpander"]{border:1px solid var(--line) !important;border-radius:10px !important;
  background:var(--surface) !important;}
[data-testid="stSidebar"]{background:var(--surface) !important;border-right:1px solid var(--line);}
hr{border-color:var(--line) !important;}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def html(markup: str):
    st.markdown(markup, unsafe_allow_html=True)


def esc(s) -> str:
    return (str("" if s is None else s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ── building blocks ────────────────────────────────────────────────────────
def masthead(title: str, subtitle: str):
    html(f'<div class="lt-eyebrow">Ledger &amp; Ticker</div>'
         f'<div class="lt-title">{esc(title)}</div>'
         f'<div class="lt-sub">{esc(subtitle)}</div>')


def kpis(items):
    """items: list of (label, value_html, sub)."""
    cells = "".join(
        f'<div class="lt-kpi"><div class="lt-k-label">{esc(l)}</div>'
        f'<div class="lt-k-value">{v}</div>'
        f'<div class="lt-k-sub">{esc(s)}</div></div>' for l, v, s in items)
    html(f'<div class="lt-kpis">{cells}</div>')


def card(title, hint, table_html, foot=None):
    html(f'<div class="lt-card"><div class="lt-card-head"><h2>{esc(title)}</h2>'
         f'<div class="lt-hint">{esc(hint)}</div></div>'
         f'<div class="lt-scroll">{table_html}</div>'
         + (f'<div class="lt-foot">{foot}</div>' if foot else "") + "</div>")


def table(headers, rows, min_width=760):
    """headers: list of (label, is_numeric). rows: list of lists of raw HTML."""
    head = "".join(f'<th class="{"n" if n else ""}">{esc(h)}</th>' for h, n in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{"n" if headers[i][1] else ""}">{c}</td>'
            for i, c in enumerate(r)) + "</tr>" for r in rows)
    return (f'<table class="lt" style="min-width:{min_width}px">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def alert(kind, title, body_html):
    html(f'<div class="lt-alert {kind}"><h3>{esc(title)}</h3><p>{body_html}</p></div>')


def pill(text, kind=None):
    return f'<span class="pill {kind or str(text).lower()}">{esc(text)}</span>'


def meter(rate):
    if rate is None:
        return '<span class="lt-muted">—</span>'
    v = max(0.0, min(100.0, float(rate)))
    tone = "var(--gain)" if v >= 67 else "var(--hold)" if v >= 34 else "var(--loss)"
    return (f'<div class="meter"><div class="track">'
            f'<div class="fill" style="width:{v:.0f}%;background:{tone}"></div></div>'
            f'<span class="val">{v:.0f}%</span></div>')


def signed(value, text=None):
    """Colour a number by sign, with an arrow so it never reads by colour alone."""
    if value is None:
        return '<span class="lt-muted">—</span>'
    cls = "gain" if value > 0 else "loss" if value < 0 else "flat"
    arrow = "▲ " if value > 0 else "▼ " if value < 0 else ""
    return f'<span class="{cls} lt-strong">{arrow}{text or f"{value:+.2f}%"}</span>'
