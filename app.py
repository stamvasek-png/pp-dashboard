# ╔══════════════════════════════════════════════════════════════╗
# ║  PP DASHBOARD — Streamlit app                                ║
# ║  ENTSO-E: odchylka · generace · zatížení · odstávky         ║
# ║  Delta Green: portfolio · flexibilita                        ║
# ║                                                              ║
# ║  Spuštění:  streamlit run app.py                             ║
# ╚══════════════════════════════════════════════════════════════╝

import time
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from entsoe import EntsoePandasClient

# ── PAGE CONFIG ─────────────────────────────────────────────────
st.set_page_config(
    page_title="PP Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── KONSTANTY ───────────────────────────────────────────────────
ENTSOE_TOKEN   = "95fa8cc7-1438-455b-9060-795d7c44d389"
THRESHOLD      = 20          # MWh — práh DEFICIT / SURPLUS
PEAK_HOURS     = set(range(8, 20))
DG_BASE        = "https://api.deltagreen.cz/api/proteus/external/v1"

# Barvy
C_DEFICIT  = "#C62828"
C_SURPLUS  = "#1565C0"
C_OK       = "#2E7D32"
C_WARN     = "#E65100"
C_NEW      = "#FF6B35"
C_TEXT     = "#263238"
C_MUTED    = "#78909C"
C_GRID     = "#ECEFF1"
C_BG       = "#FFFFFF"

# ── PSR TYPES — SPRÁVNÉ ENTSO-E KÓDY ────────────────────────────
# (B10 = Hydro Pumped, B14 = Nuclear — ne jak bylo špatně v orig.)
PSR_TYPES = {
    "B01": ("Biomasa",             "#43A047"),   # zelená
    "B02": ("Lignit",              "#5D4037"),   # tmavě hnědá
    "B03": ("Plyn z uhlí",         "#8D6E63"),   # hnědá
    "B04": ("Zemní plyn",          "#FF7043"),   # oranžová
    "B05": ("Černé uhlí",          "#37474F"),   # tmavě šedá
    "B06": ("Topný olej",          "#FFA000"),   # jantarová
    "B07": ("Ropné břidlice",      "#BF360C"),   # tmavě oranžová
    "B08": ("Rašelina",            "#795548"),   # hnědá
    "B09": ("Geotermální",         "#00695C"),   # tmavozelená
    "B10": ("Přečerpávací hydro",  "#29B6F6"),   # světle modrá
    "B11": ("Průtočná voda",       "#1E88E5"),   # modrá
    "B12": ("Vodní nádrž",         "#1565C0"),   # tmavě modrá
    "B13": ("Mořská",              "#006064"),   # teal
    "B14": ("Jaderná",             "#7B1FA2"),   # fialová
    "B15": ("Ostatní OZE",         "#66BB6A"),   # světle zelená
    "B16": ("Solární",             "#F9A825"),   # žlutá
    "B17": ("Odpad",               "#78909C"),   # modro-šedá
    "B18": ("Vítr offshore",       "#00838F"),   # tmavý cyan
    "B19": ("Vítr onshore",        "#00ACC1"),   # cyan
    "B20": ("Ostatní",             "#90A4AE"),   # světle šedá
}

# Zásobník barev pro neznámé kódy (vygenerované z HSL palety)
_FALLBACK_COLORS = [
    "#E53935","#8E24AA","#039BE5","#00897B","#F4511E",
    "#3949AB","#00ACC1","#43A047","#FB8C00","#6D4C41",
]

def psr_lookup(col) -> tuple:
    """Vrátí (název, barva) pro daný PSR sloupec (kód nebo tuple)."""
    psr = str(col[0]) if isinstance(col, tuple) else str(col)
    if psr in PSR_TYPES:
        return PSR_TYPES[psr]
    # Fallback — deterministická barva z hashe
    color = _FALLBACK_COLORS[abs(hash(psr)) % len(_FALLBACK_COLORS)]
    return (psr, color)

# Stack order — základní zátěž → špičkové → intermitentní
GEN_STACK_ORDER = [
    "B14","B02","B05","B04","B06","B08","B10","B11","B12",
    "B01","B17","B16","B19","B18","B15","B03","B20",
]

# ── CSS ─────────────────────────────────────────────────────────
st.markdown("""
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
""", unsafe_allow_html=True)

# ── ENTSO-E CLIENT ───────────────────────────────────────────────
@st.cache_resource
def _get_client():
    return EntsoePandasClient(api_key=ENTSOE_TOKEN)

client = _get_client()

# ── SESSION STATE ────────────────────────────────────────────────
for key, default in [
    ("df_out_prev", None),
    ("dg_api_key", ""),
    ("iteration", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── SIDEBAR ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Nastavení")

    st.session_state.dg_api_key = st.text_input(
        "Delta Green API klíč",
        value=st.session_state.dg_api_key,
        type="password",
        placeholder="Vložte klíč…",
    )

    refresh_min = st.slider("Auto-refresh (min)", 5, 120, 30, step=5)
    auto_refresh = st.checkbox("Auto refresh", value=False)

    if st.button("🔄 Obnovit data", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### Zdroje dat")
    st.caption(
        "**ENTSO-E Transparency Platform**  \n"
        "Odchylka · Ceny · Generace · Zatížení · Odstávky"
    )
    st.caption(
        "**Delta Green API**  \n"
        "Portfolio · Flexibilita (volitelné)"
    )

# Auto-refresh pomocí meta tagu (nejjednodušší bez extra balíků)
if auto_refresh:
    st.markdown(
        f'<meta http-equiv="refresh" content="{refresh_min * 60}">',
        unsafe_allow_html=True,
    )

# ── FETCH FUNKCE (s cache) ───────────────────────────────────────
@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_entsoe_data():
    now        = pd.Timestamp.now(tz="Europe/Prague")
    start_day  = now.normalize()
    end_imbal  = now + pd.Timedelta(hours=1)
    end_load   = start_day + pd.Timedelta(days=2)
    end_out    = start_day + pd.Timedelta(days=7)

    # Odchylka
    vol   = client.query_imbalance_volumes("CZ", start=start_day, end=end_imbal)
    imbal = (vol.rename("odchylka_MWh").to_frame()
             if isinstance(vol, pd.Series)
             else vol.select_dtypes("number").sum(axis=1).rename("odchylka_MWh").to_frame())
    try:
        pri = client.query_imbalance_prices("CZ", start=start_day, end=end_imbal)
        imbal["price_Short"] = pri["Short"]
        imbal["price_Long"]  = pri["Long"]
    except Exception:
        imbal["price_Short"] = float("nan")
        imbal["price_Long"]  = float("nan")

    # Generace
    try:
        gen = client.query_generation("CZ", start=start_day, end=end_imbal, psr_type=None)
        if isinstance(gen.columns, pd.MultiIndex):
            lvls = gen.columns.get_level_values(1)
            gen_actual = (gen.xs("Actual Aggregated", level=1, axis=1)
                          if "Actual Aggregated" in lvls
                          else gen.xs(lvls[0], level=1, axis=1))
        else:
            gen_actual = gen
    except Exception:
        gen_actual = pd.DataFrame()

    # Zatížení
    try:
        load_actual = client.query_load("CZ", start=start_day, end=end_load)
        if isinstance(load_actual, pd.DataFrame):
            load_actual = load_actual.iloc[:, 0]
        load_actual = load_actual.rename("actual_MW")
    except Exception:
        load_actual = pd.Series(dtype="float64", name="actual_MW")
    try:
        load_fc = client.query_load_forecast("CZ", start=start_day, end=end_load)
        if isinstance(load_fc, pd.DataFrame):
            load_fc = load_fc.iloc[:, 0]
        load_fc = load_fc.rename("forecast_MW")
    except Exception:
        load_fc = pd.Series(dtype="float64", name="forecast_MW")

    # Odstávky PU + GU
    out_frames = []
    for level, fn in [
        ("PU", client.query_unavailability_of_production_units),
        ("GU", client.query_unavailability_of_generation_units),
    ]:
        try:
            raw = fn("CZ", start=start_day, end=end_out)
            if raw is not None and not raw.empty:
                raw = raw.copy()
                raw["unit_level"] = level
                out_frames.append(raw)
        except Exception:
            pass
    raw_out = pd.concat(out_frames, ignore_index=True) if out_frames else pd.DataFrame()

    return imbal, gen_actual, load_actual, load_fc, raw_out, now


@st.cache_data(ttl=60 * 15, show_spinner=False)
def fetch_dap(day_offset: int = 0):
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize() + pd.Timedelta(days=day_offset)
    if start.tzinfo is None:
        start = start.tz_localize("Europe/Prague")
    end = start + pd.Timedelta(days=1)
    try:
        raw = client.query_day_ahead_prices("CZ", start=start, end=end)
    except Exception:
        return pd.Series(dtype=float, name="dap_EUR_MWh")
    if raw is None or len(raw) == 0:
        return pd.Series(dtype=float, name="dap_EUR_MWh")
    raw = raw.tz_convert("Europe/Prague")
    raw.name = "dap_EUR_MWh"
    if len(raw) <= 25:
        idx_15 = pd.date_range(start=start, periods=96, freq="15min", tz="Europe/Prague")
        raw = raw.reindex(idx_15, method="ffill")
    return raw.dropna()


def fetch_deltagreen(api_key: str):
    headers = {"x-api-key": api_key, "accept": "application/json"}
    r1 = requests.get(f"{DG_BASE}/copilot/portfolio-state",
                      headers=headers, params={"granularity": "15s"}, timeout=15)
    r2 = requests.get(f"{DG_BASE}/copilot/available-flexibility",
                      headers=headers, timeout=15)
    r1.raise_for_status()
    r2.raise_for_status()
    df1 = pd.DataFrame(r1.json()["records"])
    df1["time"] = pd.to_datetime(df1["time"]).dt.tz_convert("Europe/Prague")
    for col in ["batteryPowerKW","gridPowerKW","consumptionPowerKW","photovoltaicPowerKW"]:
        if col not in df1.columns:
            df1[col] = None
    df2 = pd.DataFrame(r2.json()["records"])
    df2["time"] = pd.to_datetime(df2["time"]).dt.tz_convert("Europe/Prague")
    for col in ["upPowerKW","downBatteryPowerKW","downSolarCurtailmentPowerKW"]:
        if col not in df2.columns:
            df2[col] = None
    return df1, df2


# ── PARSE FUNKCE ────────────────────────────────────────────────
def parse_imbalance(imbal: pd.DataFrame) -> pd.DataFrame:
    df = imbal.copy().dropna(subset=["odchylka_MWh"])
    df["signal"] = df["odchylka_MWh"].apply(
        lambda v: "DISCHARGE" if v < -THRESHOLD else ("CHARGE" if v > THRESHOLD else "STANDBY")
    )
    df["power_pct"] = df["odchylka_MWh"].apply(
        lambda v: min(100, int(abs(v) / 150 * 100)) if abs(v) > THRESHOLD else 0
    )
    return df


def parse_outages(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    df = raw.copy().rename(columns={
        "start":                    "outage_start",
        "end":                      "outage_end",
        "nominal_power":            "installed_MW",
        "avail_qty":                "available_MW",
        "businesstype":             "outage_type",
        "production_resource_name": "unit_raw",
        "production_resource_id":   "eic_code",
    })
    df["unit_name"]    = df["unit_raw"].apply(lambda x: str(x).replace("_", " ").strip()
                                               if isinstance(x, str) else str(x))
    df["installed_MW"] = pd.to_numeric(df["installed_MW"], errors="coerce")
    df["available_MW"] = pd.to_numeric(df["available_MW"], errors="coerce")
    df["unavailable_MW"] = df["installed_MW"] - df["available_MW"]
    df["available_pct"]  = (df["available_MW"] / df["installed_MW"] * 100).round(1)
    for col in ["outage_start", "outage_end"]:
        if col in df.columns:
            if df[col].dt.tz is None:
                df[col] = df[col].dt.tz_localize("UTC").dt.tz_convert("Europe/Prague")
            else:
                df[col] = df[col].dt.tz_convert("Europe/Prague")
    df = (df.drop_duplicates(subset=["unit_raw", "outage_start", "outage_end"])
            .sort_values(["unit_level", "unavailable_MW"], ascending=[True, False])
            .reset_index(drop=True))
    keep = ["unit_raw","eic_code","unit_name","unit_level",
            "outage_start","outage_end","installed_MW","available_MW",
            "unavailable_MW","available_pct","outage_type","mrid"]
    return df[[c for c in keep if c in df.columns]]


def detect_changes(df_prev, df_curr):
    key = ["unit_raw", "outage_start", "outage_end"]
    empty = {"new": set(), "ended": set(), "changed_mw": pd.DataFrame()}
    if df_prev is None or df_prev.empty or df_curr.empty:
        return empty
    prev_keys = set(df_prev[key].apply(tuple, axis=1))
    curr_keys = set(df_curr[key].apply(tuple, axis=1))
    merged = df_curr.merge(
        df_prev[key + ["available_MW"]].rename(columns={"available_MW": "prev_MW"}),
        on=key, how="inner",
    )
    changed = merged[abs(merged["available_MW"] - merged["prev_MW"]) > 0.5].copy()
    changed["delta_MW"] = changed["available_MW"] - changed["prev_MW"]
    return {
        "new":        curr_keys - prev_keys,
        "ended":      prev_keys - curr_keys,
        "changed_mw": changed[["unit_name","prev_MW","available_MW","delta_MW"]]
                      if not changed.empty else pd.DataFrame(),
    }


# ── HELPERY PRO GRAFY ────────────────────────────────────────────
def pct_to_color(available_pct):
    r = max(0.0, min(1.0, 1.0 - (available_pct or 0) / 100))
    if r < 0.5:
        t = r / 0.5
        return f"rgb({int(46 + (255-46)*t)},{int(125 + (143-125)*t)},{int(50 + (0-50)*t)})"
    else:
        t = (r - 0.5) / 0.5
        return f"rgb({int(255 + (198-255)*t)},{int(143 + (40-143)*t)},0)"


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


# ── GRAFY ────────────────────────────────────────────────────────
def fig_imbalance(df, now, height=290):
    fig = go.Figure()
    if df.empty:
        return _base_layout(fig, height=height)
    surplus = df["odchylka_MWh"] >= 0
    fig.add_trace(go.Bar(x=df.index[surplus], y=df.loc[surplus,"odchylka_MWh"],
                         marker_color=C_SURPLUS, name="Surplus",
                         hovertemplate="<b>%{x|%a %d.%m %H:%M}</b><br>+%{y:.1f} MWh<extra></extra>"))
    fig.add_trace(go.Bar(x=df.index[~surplus], y=df.loc[~surplus,"odchylka_MWh"],
                         marker_color=C_DEFICIT, name="Deficit",
                         hovertemplate="<b>%{x|%a %d.%m %H:%M}</b><br>%{y:.1f} MWh<extra></extra>"))
    if len(df) >= 4:
        ma = df["odchylka_MWh"].rolling(4, min_periods=1).mean()
        fig.add_trace(go.Scatter(x=df.index, y=ma, mode="lines", name="1h avg",
                                 line=dict(color="#212121", width=1.5), hoverinfo="skip", opacity=.7))
    fig.add_hline(y=0, line_color="#9E9E9E", line_width=.8)
    fig.add_hline(y=THRESHOLD,  line_color="#9E9E9E", line_width=.4, line_dash="dot")
    fig.add_hline(y=-THRESHOLD, line_color="#9E9E9E", line_width=.4, line_dash="dot")
    last = df["odchylka_MWh"].iloc[-1]
    fig.add_annotation(x=df.index[-1], y=last, text=f"<b>{last:+.1f}</b>",
                       showarrow=False, yshift=14 if last >= 0 else -14,
                       font=dict(size=12, color=C_SURPLUS if last >= 0 else C_DEFICIT),
                       bgcolor="rgba(255,255,255,.85)", borderpad=2)
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_layout(barmode="relative", bargap=.15)
    fig.update_xaxes(tickformat="%H:%M\n%d.%m")
    fig.update_yaxes(title_text="MWh / 15 min")
    return fig


def fig_generation_area(df_gen, now, height=320):
    fig = go.Figure()
    if df_gen.empty:
        return _base_layout(fig, height=height)
    cols = list(df_gen.columns)
    def _key(c):
        psr = str(c[0]) if isinstance(c, tuple) else str(c)
        return GEN_STACK_ORDER.index(psr) if psr in GEN_STACK_ORDER else 999
    for col in sorted(cols, key=_key):
        name, color = psr_lookup(col)
        series = df_gen[col].fillna(0)
        if series.sum() < 1:
            continue
        # rgba fill — odvozeno z hex barvy
        if color.startswith("#") and len(color) == 7:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            fill_color = f"rgba({r},{g},{b},0.78)"
        else:
            fill_color = color
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, stackgroup="gen", name=name,
            line=dict(width=0, color=color), fillcolor=fill_color,
            hovertemplate=f"{name}: %{{y:.0f}} MW<extra></extra>",
        ))
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_xaxes(tickformat="%H:%M\n%d.%m")
    fig.update_yaxes(title_text="MW")
    fig.update_layout(hovermode="x unified")
    return fig


def fig_generation_donut(df_gen, height=280):
    fig = go.Figure()
    if df_gen.empty:
        return _base_layout(fig, height=height)
    last = df_gen.dropna(how="all").tail(1)
    if last.empty:
        return _base_layout(fig, height=height)
    items = []
    for col, val in last.iloc[0].items():
        name, color = psr_lookup(col)
        if pd.notna(val) and float(val) > 0:
            items.append((float(val), name, color))
    items.sort(reverse=True)
    total = sum(v for v,_,_ in items) or 1
    fig.add_trace(go.Pie(
        labels=[n for _,n,_ in items], values=[v for v,_,_ in items],
        hole=0.68,
        marker=dict(colors=[c for _,_,c in items], line=dict(color="#fff", width=2)),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>%{value:.0f} MW · %{percent}<extra></extra>",
        sort=False, direction="clockwise",
    ))
    fig.update_layout(
        height=height, margin=dict(l=5, r=5, t=10, b=10),
        paper_bgcolor=C_BG, plot_bgcolor=C_BG, font=dict(color=C_TEXT, size=10),
        annotations=[dict(
            text=(f"<b style='font-size:22px;color:{C_TEXT}'>{total:,.0f}</b>"
                  f"<br><span style='font-size:9px;color:{C_MUTED};letter-spacing:1px'>MW CELKEM</span>"),
            x=0.5, y=0.5, showarrow=False,
        )],
        showlegend=False,
    )
    return fig


def render_mix_legend(df_gen) -> str:
    if df_gen.empty:
        return "<div class='mix-legend'><em style='color:#888'>—</em></div>"
    last = df_gen.dropna(how="all").tail(1)
    if last.empty:
        return "<div class='mix-legend'><em style='color:#888'>—</em></div>"
    items = []
    for col, val in last.iloc[0].items():
        name, color = psr_lookup(col)
        if pd.notna(val) and float(val) > 0:
            items.append((float(val), name, color))
    items.sort(reverse=True)
    total = sum(v for v,_,_ in items) or 1
    rows = [
        f'<div class="mix-row">'
        f'<span class="mix-dot" style="background:{c}"></span>'
        f'<span class="mix-name">{n}</span>'
        f'<span class="mix-val">{v:,.0f} MW</span>'
        f'<span class="mix-pct">{v/total*100:.0f}%</span></div>'
        for v, n, c in items
    ]
    return f'<div class="mix-legend">{"".join(rows)}</div>'


def fig_load(load_actual, load_fc, now, height=280):
    fig = go.Figure()
    if not load_fc.empty:
        fig.add_trace(go.Scatter(
            x=load_fc.index, y=load_fc.values, mode="lines",
            name="Prognóza (D+1)", line=dict(color="#26A69A", width=2, shape="hv"),
            hovertemplate="<b>%{x|%a %d.%m %H:%M}</b><br>Prognóza: %{y:,.0f} MW<extra></extra>",
        ))
    if not load_actual.empty:
        fig.add_trace(go.Scatter(
            x=load_actual.index, y=load_actual.values, mode="lines",
            name="Skutečnost", line=dict(color="#E91E63", width=2, shape="hv"),
            hovertemplate="<b>%{x|%a %d.%m %H:%M}</b><br>Skutečnost: %{y:,.0f} MW<extra></extra>",
        ))
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_xaxes(tickformat="%H:%M\n%d.%m")
    fig.update_yaxes(title_text="Zatížení (MW)")
    fig.update_layout(hovermode="x unified")
    return fig


def fig_outages_gantt(df_out, level, now, changes=None, height_per_unit=32):
    fig = go.Figure()
    sub = df_out[df_out["unit_level"] == level].copy() if not df_out.empty else pd.DataFrame()
    if sub.empty:
        fig.add_annotation(text=f"Žádné odstávky — {level}",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(size=12, color=C_MUTED))
        return _base_layout(fig, height=160, margin_l=180)
    impact = (sub.groupby("unit_name")["unavailable_MW"]
                 .max().sort_values(ascending=True))
    sub["unit_name"] = pd.Categorical(sub["unit_name"], categories=impact.index, ordered=True)
    n_units = max(1, sub["unit_name"].nunique())
    height  = max(180, n_units * height_per_unit + 80)
    xstart  = now - pd.Timedelta(days=1)
    xend    = now.normalize() + pd.Timedelta(days=7)
    _weekend_shading(fig, xstart, xend)
    fig.add_vrect(x0=now, x1=now + pd.Timedelta(hours=24),
                  fillcolor=C_SURPLUS, opacity=0.04, layer="below", line_width=0)
    new_keys = (changes or {}).get("new", set())
    for _, r in sub.iterrows():
        key      = (r["unit_raw"], r["outage_start"], r["outage_end"])
        is_new   = key in new_keys
        bar_col  = C_NEW if is_new else pct_to_color(r.get("available_pct", 0))
        border   = dict(width=2, color=C_NEW) if is_new \
                   else dict(width=0.5, color="rgba(0,0,0,0.15)")
        y_lbl    = f"{r['unit_name']}  ({r['installed_MW']:.0f} MW)"
        hover    = (
            f"<b>{r['unit_name']}</b> [{level}]<br>"
            f"Typ: {r['outage_type']}<br>"
            f"Instalovaný: {r['installed_MW']:.0f} MW | Dostupný: {r['available_MW']:.0f} MW<br>"
            f"<b>Výpadek: {r['unavailable_MW']:.0f} MW ({100-r['available_pct']:.0f} %)</b><br>"
            f"Od: {r['outage_start'].strftime('%a %d.%m %H:%M')}  →  "
            f"Do: {r['outage_end'].strftime('%a %d.%m %H:%M')}"
            + ("  🆕 NOVÁ" if is_new else "")
        )
        fig.add_trace(go.Bar(
            x=[(r["outage_end"] - r["outage_start"]).total_seconds() * 1000],
            y=[y_lbl], base=[r["outage_start"].timestamp() * 1000],
            orientation="h", marker_color=bar_col, marker_line=border,
            hovertext=hover, hoverinfo="text", showlegend=False, width=0.65,
        ))
    _now_marker(fig, now)
    _base_layout(fig, height=height, margin_l=200)
    fig.update_xaxes(type="date", tickformat="%a %d.%m\n%H:%M",
                     range=[xstart.isoformat(), xend.isoformat()])
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    fig.update_layout(barmode="overlay", margin=dict(l=200, r=15, t=10, b=35))
    return fig


def fig_deltagreen(df1, df2, height=560):

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=("Výkon portfolia [kW]", "Disponibilní flexibilita [kW]"))
    for col, name, color in [
        ("batteryPowerKW",      "Baterie",      "#4A90D9"),
        ("consumptionPowerKW",  "Spotřeba",     "#E8744A"),
        ("photovoltaicPowerKW", "Fotovoltaika", "#F5C518"),
        ("gridPowerKW",         "Síť",          "#2ECC71"),
    ]:
        fig.add_trace(go.Scatter(x=df1["time"], y=df1[col], name=name,
                                 line=dict(color=color, width=1.5),
                                 hovertemplate=f"<b>{name}</b><br>%{{x|%H:%M:%S}}<br>%{{y:.0f}} kW<extra></extra>"),
                      row=1, col=1)
    for col, name, fc, lc in [
        ("upPowerKW",                   "Max UP",         "rgba(0,191,166,.4)",  "#00BFA6"),
        ("downBatteryPowerKW",          "Nabíjení bat.",  "rgba(74,144,217,.4)", "#4A90D9"),
        ("downSolarCurtailmentPowerKW", "Zákaz přetoku",  "rgba(245,197,24,.4)", "#F5C518"),
    ]:
        fig.add_trace(go.Scatter(x=df2["time"], y=df2[col], name=name,
                                 fill="tozeroy", fillcolor=fc, line=dict(color=lc, width=1.5),
                                 hovertemplate=f"<b>{name}</b><br>%{{x|%H:%M:%S}}<br>%{{y:.0f}} kW<extra></extra>"),
                      row=2, col=1)
    last2 = df2.dropna(subset=["upPowerKW","downBatteryPowerKW","downSolarCurtailmentPowerKW"]).iloc[-1]
    total_down = float(last2["downBatteryPowerKW"]) + float(last2["downSolarCurtailmentPowerKW"])
    fig.update_layout(
        height=height,
        title_text=(f"Delta Green Portfolio  |  UP: {round(float(last2['upPowerKW']))} kW  |  "
                    f"DOWN: {round(total_down)} kW"),
        template="plotly_white", hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", y=-0.08, x=0),
        margin=dict(l=60, r=15, t=50, b=40),
    )
    for row in [1, 2]:
        fig.update_xaxes(tickformat="%H:%M", gridcolor=C_GRID, row=row, col=1)
        fig.update_yaxes(title_text="kW", gridcolor=C_GRID, zeroline=True,
                         zerolinecolor="#cccccc", row=row, col=1)
    return fig


def fig_dap(s_d0, s_d1, now, height=320):

    start_d0 = now.normalize()
    start_d1 = start_d0 + pd.Timedelta(days=1)
    label0   = f"D0 — {now.strftime('%d.%m.%Y')}"
    label1   = f"D+1 — {(now + pd.Timedelta(days=1)).strftime('%d.%m.%Y')}"
    fig = make_subplots(rows=1, cols=2, subplot_titles=[label0, label1],
                        column_widths=[0.5, 0.5], horizontal_spacing=0.08)
    def _add_dap(series, row, col, color, peak_col, name):
        if series.empty:
            return
        fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines",
                                 name=name, line=dict(color=color, width=2, shape="hv"),
                                 fill="tozeroy",
                                 fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},.08)",
                                 hovertemplate=f"%{{x|%H:%M}}  %{{y:.2f}} EUR/MWh<extra>{name}</extra>"),
                      row=row, col=col)
        peak = series[series.index.hour.isin(PEAK_HOURS)]
        if not peak.empty:
            fig.add_trace(go.Scatter(x=peak.index, y=peak.values, mode="lines",
                                     name=f"{name} Peak", line=dict(color=peak_col, width=2.5, shape="hv"),
                                     hovertemplate=f"%{{x|%H:%M}}  %{{y:.2f}} EUR/MWh<extra>Peak</extra>"),
                          row=row, col=col)
        avg = float(series.mean())
        fig.add_hline(y=avg, line_dash="dot", line_color=color, line_width=1, row=row, col=col)
    _add_dap(s_d0, 1, 1, "#1565C0", "#F57F17", "D0")
    _add_dap(s_d1, 1, 2, "#2E7D32", "#E65100", "D+1")
    if s_d1.empty:
        fig.add_annotation(text="D+1 zatím nedostupné (aukce po 13:00)",
                           x=0.5, y=0.5, xref="x2 domain", yref="y2 domain",
                           showarrow=False, font=dict(size=11, color="#888"))
    fig.add_vline(x=now.isoformat(), line_dash="dot", line_color=C_DEFICIT, line_width=1.5)
    fig.update_layout(
        height=height, template="plotly_white", hovermode="x", showlegend=True,
        legend=dict(orientation="h", y=-0.15, x=0),
        margin=dict(l=50, r=15, t=50, b=40),
        xaxis =dict(type="date", tickformat="%H:%M",
                    range=[start_d0.isoformat(), (start_d0+pd.Timedelta(days=1)).isoformat()]),
        xaxis2=dict(type="date", tickformat="%H:%M",
                    range=[start_d1.isoformat(), (start_d1+pd.Timedelta(days=1)).isoformat()]),
    )
    fig.update_yaxes(title_text="EUR/MWh")
    return fig


def calc_dap_stats(s: pd.Series) -> dict:
    if s.empty:
        return {"base": None, "peak": None, "offpeak": None, "min": None, "max": None}
    pm = s.index.hour.isin(PEAK_HOURS)
    def _avg(x): return round(float(x.mean()), 2) if len(x) else None
    return {"base": _avg(s), "peak": _avg(s[pm]), "offpeak": _avg(s[~pm]),
            "min": round(float(s.min()), 2), "max": round(float(s.max()), 2)}


# ── NAČTENÍ DAT ──────────────────────────────────────────────────
with st.spinner("Načítám data z ENTSO-E…"):
    try:
        imbal_raw, gen_raw, load_actual, load_fc, out_raw, now = fetch_entsoe_data()
    except Exception as e:
        st.error(f"Chyba při načítání ENTSO-E dat: {e}")
        st.stop()

df_imbal = parse_imbalance(imbal_raw)
df_out   = parse_outages(out_raw)
changes  = detect_changes(st.session_state.df_out_prev, df_out)
st.session_state.df_out_prev = df_out.copy() if not df_out.empty else None

last_imbal = float(df_imbal["odchylka_MWh"].iloc[-1]) if not df_imbal.empty else 0.0

# ── BANNER ───────────────────────────────────────────────────────
if last_imbal < -THRESHOLD:
    bcls, bstate = "banner-bad",  f"DEFICIT &nbsp; {last_imbal:+.1f} MWh"
elif last_imbal > THRESHOLD:
    bcls, bstate = "banner-warn", f"SURPLUS &nbsp; {last_imbal:+.1f} MWh"
else:
    bcls, bstate = "banner-ok",   f"VYVÁŽENO &nbsp; {last_imbal:+.1f} MWh"

data_age = (pd.Timestamp.now(tz="Europe/Prague") - now).total_seconds() / 60
fresh    = f"{data_age:.0f} min" if data_age < 60 else f"{data_age/60:.1f} h ⚠"

st.markdown(
    f'<div class="banner {bcls}">'
    f'<div class="banner-left"><span class="pulse-dot"></span><span>⚡ PP DASHBOARD</span></div>'
    f'<div class="banner-center">{bstate}</div>'
    f'<div class="banner-right">{now.strftime("%a %d.%m.%Y · %H:%M:%S")}'
    f'<span class="fresh-badge">{fresh}</span></div></div>',
    unsafe_allow_html=True,
)

# ── KPI STRIP ────────────────────────────────────────────────────
n_pu  = int((df_out["unit_level"] == "PU").sum()) if not df_out.empty else 0
n_gu  = int((df_out["unit_level"] == "GU").sum()) if not df_out.empty else 0
n_new = len(changes["new"])
total_unavail = float(df_out["unavailable_MW"].sum()) if not df_out.empty else 0.0
total_install = float(df_out["installed_MW"].sum())   if not df_out.empty else 0.0
unavail_pct   = total_unavail / total_install * 100   if total_install else 0.0
cur_gen = (float(gen_raw.dropna(how="all").tail(1).iloc[0].sum(skipna=True))
           if not gen_raw.empty and not gen_raw.dropna(how="all").empty else 0.0)
last_short = (float(df_imbal["price_Short"].dropna().iloc[-1])
              if "price_Short" in df_imbal and df_imbal["price_Short"].notna().any() else None)
spark_i = sparkline_svg(df_imbal["odchylka_MWh"].tail(96).tolist(),
                        C_DEFICIT if last_imbal < 0 else C_SURPLUS)
spark_g = (sparkline_svg(gen_raw.fillna(0).sum(axis=1).tail(96).tolist(), C_OK)
           if not gen_raw.empty else "")
imbal_col = C_DEFICIT if last_imbal < -THRESHOLD else (C_SURPLUS if last_imbal > THRESHOLD else C_TEXT)
unavail_col = C_DEFICIT if unavail_pct > 30 else (C_WARN if unavail_pct > 15 else C_OK)

kpi_html = f"""
<div class="kpi-row">
  <div class="kpi-card" style="border-top-color:{imbal_col}">
    <div class="kpi-label">Systémová odchylka</div>
    <div class="kpi-value" style="color:{imbal_col}">{last_imbal:+.1f}<span style="font-size:.9rem;color:{C_MUTED}"> MWh</span></div>
    <div class="kpi-sub">práh ±{THRESHOLD} MWh</div>
    <div>{spark_i}</div>
  </div>
  <div class="kpi-card" style="border-top-color:{C_OK}">
    <div class="kpi-label">Aktuální výroba</div>
    <div class="kpi-value">{cur_gen:,.0f}<span style="font-size:.9rem;color:{C_MUTED}"> MW</span></div>
    <div class="kpi-sub">posledních 24 h</div>
    <div>{spark_g}</div>
  </div>
  <div class="kpi-card" style="border-top-color:{unavail_col}">
    <div class="kpi-label">Výpadek kapacit</div>
    <div class="kpi-value" style="color:{unavail_col}">{total_unavail:,.0f}<span style="font-size:.9rem;color:{C_MUTED}"> MW</span></div>
    <div class="kpi-sub">z {total_install:,.0f} MW instalovaných · {unavail_pct:.0f}%</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Aktivní odstávky</div>
    <div class="kpi-value">{n_pu + n_gu}</div>
    <div class="kpi-sub">PU {n_pu} · GU {n_gu}{f' · <span style="color:{C_NEW};font-weight:600">+{n_new} nových</span>' if n_new else ''}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Cena odchylky (Short)</div>
    <div class="kpi-value">{f'{last_short:,.0f}' if last_short is not None else '—'}<span style="font-size:.9rem;color:{C_MUTED}"> €/MWh</span></div>
    <div class="kpi-sub">CZ imbalance price</div>
  </div>
</div>
"""
st.markdown(kpi_html, unsafe_allow_html=True)

# Alert na nové odstávky
if n_new or len(changes.get("ended", set())) or not changes["changed_mw"].empty:
    n_ended = len(changes.get("ended", set()))
    n_chmw  = len(changes["changed_mw"])
    parts   = []
    if n_new:
        parts.append(f"🆕 <strong>{n_new} nových</strong> odstávek")
    if n_ended:
        parts.append(f"✅ <strong>{n_ended} ukončených</strong>")
    if n_chmw:
        parts.append(f"⚡ <strong>{n_chmw} změn MW</strong>")
    st.markdown(f'<div class="alert-box">{"  ·  ".join(parts)}</div>',
                unsafe_allow_html=True)

# ── ZÁLOŽKY ──────────────────────────────────────────────────────
tab_dash, tab_out, tab_dap, tab_dg, tab_data = st.tabs([
    "📊 Odchylka & Generace",
    "🔧 Odstávky",
    "💶 DAP Ceny",
    "🌿 Delta Green",
    "📋 Data",
])

# ──────────── TAB 1: ODCHYLKA + GENERACE ─────────────────────────
with tab_dash:
    st.markdown('<div class="section-title">Systémová odchylka</div>', unsafe_allow_html=True)
    st.plotly_chart(fig_imbalance(df_imbal, now), use_container_width=True,
                    config={"displayModeBar": False})

    st.markdown('<div class="section-title">Zatížení — skutečnost vs. prognóza D+1</div>',
                unsafe_allow_html=True)
    if load_actual.empty and load_fc.empty:
        st.info("Data zatížení nejsou dostupná.")
    else:
        st.plotly_chart(fig_load(load_actual, load_fc, now), use_container_width=True,
                        config={"displayModeBar": False})

    st.markdown('<div class="section-title">Generace podle zdroje · Aktuální mix</div>',
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 1.2, 1.2])
    with c1:
        if gen_raw.empty:
            st.info("Data generace nejsou dostupná.")
        else:
            st.plotly_chart(fig_generation_area(gen_raw, now), use_container_width=True,
                            config={"displayModeBar": False})
    with c2:
        st.plotly_chart(fig_generation_donut(gen_raw), use_container_width=True,
                        config={"displayModeBar": False})
    with c3:
        st.markdown('<div class="section-title">Mix</div>', unsafe_allow_html=True)
        st.markdown(render_mix_legend(gen_raw), unsafe_allow_html=True)

# ──────────── TAB 2: ODSTÁVKY ─────────────────────────────────────
with tab_out:
    st.markdown(f'<div class="section-title">Výrobní jednotky (PU) — {n_pu} aktivních</div>',
                unsafe_allow_html=True)
    st.plotly_chart(fig_outages_gantt(df_out, "PU", now, changes),
                    use_container_width=True, config={"displayModeBar": False})

    st.markdown(f'<div class="section-title">Generační jednotky (GU) — {n_gu} aktivních</div>',
                unsafe_allow_html=True)
    st.plotly_chart(fig_outages_gantt(df_out, "GU", now, changes),
                    use_container_width=True, config={"displayModeBar": False})

    n_ended_tab = len(changes.get("ended", set()))
    n_chmw_tab  = len(changes["changed_mw"])
    with st.expander(f"📋 Detail změn  ·  {n_new} nových · {n_ended_tab} ukončených · {n_chmw_tab} změn MW",
                     expanded=bool(n_new or n_ended_tab or n_chmw_tab)):
        if not (n_new or n_ended_tab or n_chmw_tab):
            st.markdown("<em style='color:#888'>Žádné změny od posledního obnovení.</em>",
                        unsafe_allow_html=True)
        else:
            if n_new and not df_out.empty:
                new_df = (df_out[df_out[["unit_raw","outage_start","outage_end"]]
                                 .apply(tuple, axis=1).isin(changes["new"])]
                          .sort_values("unavailable_MW", ascending=False))
                st.markdown("**🆕 Nové odstávky**")
                st.dataframe(
                    new_df[["unit_name","unit_level","outage_start","outage_end",
                             "installed_MW","unavailable_MW","outage_type"]],
                    use_container_width=True, hide_index=True,
                )
            if not changes["changed_mw"].empty:
                st.markdown("**⚡ Změny výkonu**")
                st.dataframe(changes["changed_mw"], use_container_width=True, hide_index=True)

# ──────────── TAB 3: DAP CENY ────────────────────────────────────
with tab_dap:
    s_d0 = fetch_dap(0)
    s_d1 = fetch_dap(1)
    st.plotly_chart(fig_dap(s_d0, s_d1, now), use_container_width=True,
                    config={"displayModeBar": False})
    c_l, c_r = st.columns(2)
    def _stat_table(stats, label):
        rows = [("Base",    stats["base"]),
                ("Peak 8-20",stats["peak"]),
                ("Off-peak", stats["offpeak"]),
                ("Min",      stats["min"]),
                ("Max",      stats["max"])]
        st.markdown(f"**{label}**")
        for lbl, val in rows:
            v = f"{val:.2f} EUR" if val is not None else "—"
            st.markdown(f"- {lbl}: **{v}**")
    with c_l:
        _stat_table(calc_dap_stats(s_d0), f"D0 — {now.strftime('%d.%m.%Y')}")
    with c_r:
        _stat_table(calc_dap_stats(s_d1), f"D+1 — {(now+pd.Timedelta(days=1)).strftime('%d.%m.%Y')}")

# ──────────── TAB 4: DELTA GREEN ─────────────────────────────────
with tab_dg:
    dg_key = st.session_state.dg_api_key.strip()
    if not dg_key:
        st.info("Zadejte Delta Green API klíč v levém panelu (⚙️ Nastavení).")
    else:
        with st.spinner("Načítám Delta Green…"):
            try:
                df1_dg, df2_dg = fetch_deltagreen(dg_key)
                st.plotly_chart(fig_deltagreen(df1_dg, df2_dg), use_container_width=True,
                                config={"displayModeBar": False})
                last2 = df2_dg.dropna(subset=["upPowerKW","downBatteryPowerKW",
                                               "downSolarCurtailmentPowerKW"]).iloc[-1]
                last1 = df1_dg.dropna(subset=["batteryPowerKW","consumptionPowerKW",
                                               "photovoltaicPowerKW","gridPowerKW"]).iloc[-1]
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Baterie",      f"{float(last1['batteryPowerKW']):+.0f} kW")
                k2.metric("Fotovoltaika", f"{float(last1['photovoltaicPowerKW']):.0f} kW")
                k3.metric("Max UP",       f"{float(last2['upPowerKW']):.0f} kW")
                total_down = float(last2["downBatteryPowerKW"]) + float(last2["downSolarCurtailmentPowerKW"])
                k4.metric("Max DOWN",     f"{total_down:.0f} kW")
            except Exception as e:
                st.error(f"Delta Green nedostupný: {e}")

# ──────────── TAB 5: SUROVÁ DATA ─────────────────────────────────
with tab_data:
    t1, t2, t3, t4 = st.tabs(["Odchylka", "Odstávky PU", "Odstávky GU", "Generace"])

    with t1:
        if not df_imbal.empty:
            st.dataframe(df_imbal.iloc[::-1], use_container_width=True)
            st.download_button("⬇ CSV odchylka", df_imbal.to_csv().encode(),
                               "odchylka.csv", "text/csv")

    def _out_tab(lvl):
        sub = df_out[df_out["unit_level"] == lvl] if not df_out.empty else pd.DataFrame()
        if sub.empty:
            st.info(f"Žádné odstávky {lvl}.")
            return
        cols = ["unit_name","outage_start","outage_end","installed_MW",
                "available_MW","unavailable_MW","available_pct","outage_type"]
        st.dataframe(sub[[c for c in cols if c in sub.columns]],
                     use_container_width=True, hide_index=True)
        st.download_button(f"⬇ CSV {lvl}", sub.to_csv(index=False).encode(),
                           f"outages_{lvl}.csv", "text/csv")

    with t2: _out_tab("PU")
    with t3: _out_tab("GU")

    with t4:
        if not gen_raw.empty:
            # Přejmenuj sloupce na čitelné názvy
            display_gen = gen_raw.copy()
            display_gen.columns = [psr_lookup(c)[0] for c in display_gen.columns]
            st.dataframe(display_gen.iloc[::-1], use_container_width=True)
            st.download_button("⬇ CSV generace", display_gen.to_csv().encode(),
                               "generace.csv", "text/csv")
        else:
            st.info("Data generace nejsou dostupná.")

st.session_state.iteration += 1
