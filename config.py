import os

import pandas as pd
import plotly.graph_objects as go

# ── KONFIGURACE ──────────────────────────────────────────────────
# Token lze přepsat env proměnnou ENTSOE_TOKEN (doporučeno na serveru);
# bez ní se použije výchozí hodnota, takže lokální chování zůstává stejné.
ENTSOE_TOKEN = os.getenv("ENTSOE_TOKEN") or "95fa8cc7-1438-455b-9060-795d7c44d389"
THRESHOLD    = 20          # MWh — práh DEFICIT / SURPLUS
PEAK_HOURS   = set(range(8, 20))
DG_BASE      = "https://api.deltagreen.cz/api/proteus/external/v1"

# Barvy
C_DEFICIT = "#C62828"
C_SURPLUS = "#1565C0"
C_OK      = "#2E7D32"
C_WARN    = "#E65100"
C_NEW     = "#FF6B35"
C_TEXT    = "#263238"
C_MUTED   = "#78909C"
C_GRID    = "#ECEFF1"
C_BG      = "#FFFFFF"

# ── PSR TYPES ────────────────────────────────────────────────────
PSR_TYPES = {
    "B01": ("Biomasa",             "#43A047"),
    "B02": ("Lignit",              "#5D4037"),
    "B03": ("Plyn z uhlí",         "#8D6E63"),
    "B04": ("Zemní plyn",          "#FF7043"),
    "B05": ("Černé uhlí",          "#37474F"),
    "B06": ("Topný olej",          "#FFA000"),
    "B09": ("Geotermální",         "#00695C"),
    "B10": ("Přečerpávací hydro",  "#006064"),
    "B11": ("Průtočná voda",       "#1565C0"),
    "B12": ("Vodní nádrž",         "#0D47A1"),
    "B14": ("Jaderná",             "#7B1FA2"),
    "B15": ("Ostatní OZE",         "#66BB6A"),
    "B16": ("Solární",             "#F9A825"),
    "B17": ("Odpad",               "#78909C"),
    "B18": ("Vítr offshore",       "#0097A7"),
    "B19": ("Vítr onshore",        "#29B6F6"),
    "B20": ("Ostatní",             "#90A4AE"),
}

_FALLBACK_COLORS = [
    "#E53935","#8E24AA","#039BE5","#00897B","#F4511E",
    "#3949AB","#00ACC1","#43A047","#FB8C00","#6D4C41",
]

PSR_NAMES = {
    "Solar":                               ("Solární",              "#F9A825"),
    "Nuclear":                             ("Jaderná",              "#7B1FA2"),
    "Wind Onshore":                        ("Vítr onshore",         "#29B6F6"),
    "Wind Offshore":                       ("Vítr offshore",        "#0097A7"),
    "Hydro Pumped Storage":                ("Přečerpávací hydro",   "#006064"),
    "Hydro Run-of-river and poundage":     ("Průtočná voda",        "#1565C0"),
    "Hydro Water Reservoir":               ("Vodní nádrž",          "#0D47A1"),
    "Fossil Brown coal/Lignite":           ("Lignit",               "#5D4037"),
    "Fossil Hard coal":                    ("Černé uhlí",           "#37474F"),
    "Fossil Gas":                          ("Zemní plyn",           "#FF7043"),
    "Fossil Oil":                          ("Topný olej",           "#FFA000"),
    "Fossil Coal-derived gas":             ("Plyn z uhlí",          "#8D6E63"),
    "Biomass":                             ("Biomasa",              "#43A047"),
    "Waste":                               ("Odpad",                "#78909C"),
    "Other renewable":                     ("Ostatní OZE",          "#66BB6A"),
    "Other":                               ("Ostatní",              "#90A4AE"),
    "Geothermal":                          ("Geotermální",          "#00695C"),
}


def psr_lookup(col) -> tuple:
    psr = str(col[0]) if isinstance(col, tuple) else str(col)
    if psr in PSR_TYPES:
        return PSR_TYPES[psr]
    if psr in PSR_NAMES:
        return PSR_NAMES[psr]
    color = _FALLBACK_COLORS[abs(hash(psr)) % len(_FALLBACK_COLORS)]
    return (psr, color)


GEN_STACK_ORDER = [
    "B14","B02","B05","B04","B06","B08","B10","B11","B12",
    "B01","B17","B16","B19","B18","B15","B03","B20",
]

# Sdílená konstanta pro šířku balancingových barů (15 min v ms)
BAR_W_MS = 900_000

# ── CSS ──────────────────────────────────────────────────────────
CSS_STYLES = """
<style>
html, body, [class*="css"] { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.block-container { padding-top: 0.5rem; padding-bottom: 1.5rem; max-width: 100%; }
header[data-testid="stHeader"] { background: transparent; }
h1,h2,h3,h4 { color: #1A237E; }
[data-baseweb="tab-list"] {
    position: sticky; top: 0; z-index: 100;
    background: #fff; padding-top: 6px; margin-bottom: 4px;
    box-shadow: 0 1px 0 #ECEFF1;
}
.banner {
    display: grid; grid-template-columns: 1fr auto 1fr;
    align-items: center; padding: 12px 20px; border-radius: 10px;
    margin-bottom: 10px; color: #fff; font-weight: 600;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
}
.banner-ok   { background: #2E7D32; }
.banner-warn { background: #E65100; }
.banner-bad  { background: #C62828; }
.banner-left  { display:flex; align-items:center; gap:10px; font-size:1rem; }
.banner-center { text-align:center; font-size:1.4rem; font-weight:700; letter-spacing:.5px; }
.banner-right  { text-align:right; font-size:.85rem; opacity:.95; line-height:1.4; }
.fresh-badge {
    display:inline-block; padding:2px 8px; border-radius:999px;
    background:rgba(255,255,255,.2); font-weight:600; margin-left:6px;
}
.pulse-dot {
    width:10px; height:10px; border-radius:50%;
    background:#fff; box-shadow:0 0 0 0 rgba(255,255,255,.7);
    animation:pulse 1.6s infinite;
}
@keyframes pulse {
    0%   { box-shadow:0 0 0 0 rgba(255,255,255,.7); }
    70%  { box-shadow:0 0 0 8px rgba(255,255,255,0); }
    100% { box-shadow:0 0 0 0 rgba(255,255,255,0); }
}
.kpi-row { display:flex; gap:10px; margin-bottom:10px; }
.kpi-card {
    flex:1; border:1px solid #ECEFF1; border-radius:10px;
    padding:12px 14px; background:#fff;
    border-top:3px solid #1565C0;
}
.kpi-label { font-size:.72rem; color:#78909C; text-transform:uppercase; letter-spacing:.5px; margin-bottom:4px; }
.kpi-value { font-size:1.6rem; font-weight:700; color:#263238; line-height:1.1; }
.kpi-sub   { font-size:.78rem; color:#78909C; margin-top:2px; }
.section-title {
    font-size:.78rem; font-weight:600; color:#78909C;
    text-transform:uppercase; letter-spacing:1px;
    margin:16px 0 6px;
}
.alert-box {
    background:#FFF3E0; border-left:4px solid #FF6B35;
    padding:10px 14px; border-radius:6px; margin:8px 0;
    font-size:.9rem; color:#BF360C;
}
.mix-legend { display:flex; flex-direction:column; gap:3px; font-size:.8rem; padding:4px 0; }
.mix-row { display:flex; align-items:center; gap:6px; }
.mix-dot { width:10px; height:10px; border-radius:2px; flex-shrink:0; }
.mix-name { flex:1; color:#263238; }
.mix-val  { font-weight:600; color:#263238; }
.mix-pct  { color:#78909C; min-width:32px; text-align:right; }
</style>
"""

# ── SDÍLENÉ CHART HELPERY ────────────────────────────────────────

def _base_layout(fig, height=300, margin_l=55):
    fig.update_layout(
        height=height, plot_bgcolor=C_BG, paper_bgcolor=C_BG,
        margin=dict(l=margin_l, r=15, t=20, b=35),
        font=dict(color=C_TEXT, size=11),
        legend=dict(orientation="h", y=-0.22, x=0, xanchor="left",
                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        hoverlabel=dict(bgcolor="white", font_size=11, bordercolor=C_GRID),
    )
    fig.update_xaxes(gridcolor=C_GRID, zerolinecolor=C_GRID)
    fig.update_yaxes(gridcolor=C_GRID, zerolinecolor=C_GRID)
    return fig


def _now_marker(fig, now):
    fig.add_vline(x=now.isoformat(), line_color=C_SURPLUS, line_width=1.5)
    fig.add_annotation(x=now.isoformat(), y=1, yref="paper", yanchor="bottom",
                       text="NOW", showarrow=False, xshift=3,
                       font=dict(size=10, color=C_SURPLUS))


def _weekend_shading(fig, start, end):
    cur = start.normalize()
    while cur < end:
        if cur.weekday() == 5:
            fig.add_vrect(x0=cur, x1=cur + pd.Timedelta(days=2),
                          fillcolor="#90A4AE", opacity=0.06, layer="below", line_width=0)
        cur += pd.Timedelta(days=1)


def sparkline_svg(values, color="#1565C0", width=140, height=28):
    vals = [float(v) for v in values if pd.notna(v)]
    if len(vals) < 2:
        return ""
    vmin, vmax = min(vals), max(vals)
    rng = vmax - vmin or 1.0
    n   = len(vals)
    pts = [f"{i*width/(n-1):.1f},{height-2-(v-vmin)/rng*(height-4):.1f}"
           for i, v in enumerate(vals)]
    lx, ly = pts[-1].split(",")
    return (f'<svg width="100%" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" style="display:block;height:{height}px">'
            f'<path d="M{" L".join(pts)}" stroke="{color}" stroke-width="1.5" '
            f'fill="none" vector-effect="non-scaling-stroke"/>'
            f'<circle cx="{lx}" cy="{ly}" r="2" fill="{color}"/></svg>')
