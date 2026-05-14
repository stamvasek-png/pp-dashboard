# ╔══════════════════════════════════════════════════════════════╗
# ║  PP DASHBOARD v2                                             ║
# ║  ENTSO-E: systémová odchylka + DAP ceny D0/D+1              ║
# ║           odstávky (PU + GU) + detekce změn                 ║
# ║  Delta Green: portfolio + flexibilita                        ║
# ║                                                              ║
# ║  STRUKTURA BLOKŮ — každý blok = jedna buňka v Colabu:       ║
# ║  01_setup          → instalace + importy + konstanty         ║
# ║  02_fetch_entsoe   → systémová odchylka + ceny               ║
# ║  03_fetch_dap      → day-ahead prices D0/D+1                 ║
# ║  04_fetch_dg       → Delta Green portfolio + flexibilita     ║
# ║  05_stats          → výpočet base/peak/offpeak               ║
# ║  06_plot_entsoe    → graf odchylky + signál                  ║
# ║  07_plot_dap       → graf DAP D0 + D+1 + tabulka            ║
# ║  08_plot_dg        → graf Delta Green                        ║
# ║  09_snapshot       → textový výpis aktuálního stavu          ║
# ║  10_loop           → živá smyčka                             ║
# ║  11_fetch_outages  → odstávky PU + GU (ENTSO-E)              ║
# ║  12_plot_outages   → Gantt chart + detekce změn              ║
# ║  13_fetch_generation → výroba podle zdroje                   ║
# ║  14_fetch_load       → zatížení skutečnost vs. prognóza      ║
# ║  15_fetch_reserves   → aFRR + mFRR rezervy a ceny            ║
# ╚══════════════════════════════════════════════════════════════╝


# ── BLOK: 01_setup ──────────────────────────────────────────────
!pip install entsoe-py -q

import getpass
import requests
import pandas as pd
import time
from IPython.display import clear_output
from entsoe import EntsoePandasClient
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Konstanty — uprav dle potřeby
ENTSOE_TOKEN  = "95fa8cc7-1438-455b-9060-795d7c44d389"
THRESHOLD_MWh = 50          # práh odchylky pro signál [MWh/15min]
REFRESH_SEC   = 60           # interval refresh [sekundy]
PEAK_HOURS    = set(range(8, 20))  # 08:00–20:00 všechny dny

# Delta Green — klíč se zadá bezpečně při spuštění
DG_API_KEY = getpass.getpass("Delta Green API klic: ")
DG_BASE    = "https://api.deltagreen.cz/api/proteus/external/v1"
DG_HEADERS = {"x-api-key": DG_API_KEY, "accept": "application/json"}

client = EntsoePandasClient(api_key=ENTSOE_TOKEN)

# Mapování kódů ENTSO-E typů zdrojů → čitelné názvy + barvy
# Dle ENTSO-E standardu: B10=Hydro pumped, B14=Nuclear (ne B10!)
PSR_TYPES = {
    "B01": ("Biomasa",              "#43A047"),   # zelená
    "B02": ("Lignit",               "#5D4037"),   # tmavě hnědá
    "B03": ("Plyn z uhlí",          "#8D6E63"),   # hnědá
    "B04": ("Zemní plyn",           "#FF7043"),   # oranžová
    "B05": ("Černé uhlí",           "#37474F"),   # tmavě šedá
    "B06": ("Topný olej",           "#FFA000"),   # jantarová
    "B07": ("Ropné břidlice",       "#BF360C"),   # tmavě oranžová
    "B08": ("Rašelina",             "#795548"),   # hnědá
    "B09": ("Geotermální",          "#00695C"),   # tmavozelená
    "B10": ("Přečerpávací hydro",   "#29B6F6"),   # světle modrá
    "B11": ("Průtočná voda",        "#1E88E5"),   # modrá
    "B12": ("Vodní nádrž",          "#1565C0"),   # tmavě modrá
    "B13": ("Mořská",               "#006064"),   # teal
    "B14": ("Jaderná",              "#7B1FA2"),   # fialová  ← Nuclear!
    "B15": ("Ostatní OZE",          "#66BB6A"),   # světle zelená
    "B16": ("Solární",              "#F9A825"),   # žlutá
    "B17": ("Odpad",                "#78909C"),   # modro-šedá
    "B18": ("Vítr offshore",        "#00838F"),   # tmavý cyan
    "B19": ("Vítr onshore",         "#00ACC1"),   # cyan
    "B20": ("Ostatní",              "#90A4AE"),   # světle šedá
}

_FALLBACK_COLORS = [
    "#E53935","#8E24AA","#039BE5","#00897B","#F4511E",
    "#3949AB","#00ACC1","#43A047","#FB8C00","#6D4C41",
]

GEN_STACK_ORDER = [
    "B14","B02","B05","B04","B06","B08","B10","B11","B12",
    "B01","B17","B16","B19","B18","B15","B03","B20",
]

def psr_lookup(col) -> tuple:
    """Vrátí (název, barva) pro daný PSR sloupec (kód nebo tuple)."""
    psr = str(col[0]) if isinstance(col, tuple) else str(col)
    if psr in PSR_TYPES:
        return PSR_TYPES[psr]
    color = _FALLBACK_COLORS[abs(hash(psr)) % len(_FALLBACK_COLORS)]
    return (psr, color)

# Mapování čtyřznakového prefixu EIC kódu výrobní jednotky → čitelný název
UNIT_NAMES = {
    "15W0": "Elektrárna",
    "27W0": "Teplárna",
    "CZE0": "CZ",
}

def unit_display_name(raw: str) -> str:
    """Převede syrový kód výrobní jednotky na čitelnější název."""
    if not isinstance(raw, str):
        return str(raw)
    return raw.replace("_", " ").strip()

def pct_to_color(available_pct):
    """Barva od zelené (100 % dostupnosti) po červenou (0 %)."""
    r = max(0.0, min(1.0, 1.0 - (available_pct or 0) / 100))
    if r < 0.5:
        t = r / 0.5
        return f"rgb({int(46 + (255 - 46) * t)},{int(125 + (143 - 125) * t)},{int(50 + (0 - 50) * t)})"
    else:
        t = (r - 0.5) / 0.5
        return f"rgb({int(255 + (198 - 255) * t)},{int(143 + (40 - 143) * t)},0)"

print("Setup OK")
# ── KONEC: 01_setup ─────────────────────────────────────────────


# ── BLOK: 02_fetch_entsoe ───────────────────────────────────────
def fetch_entsoe():
    """Stáhne systémovou odchylku + ceny z ENTSO-E pro dnešní den."""
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    end   = now + pd.Timedelta(hours=1)

    vol = client.query_imbalance_volumes('CZ', start=start, end=end)
    df  = (vol.rename("imbalance_MWh").to_frame()
           if isinstance(vol, pd.Series)
           else vol.select_dtypes("number").sum(axis=1).rename("imbalance_MWh").to_frame())

    try:
        pri = client.query_imbalance_prices('CZ', start=start, end=end)
        df["price_Long_CZK"]  = pri["Long"]
        df["price_Short_CZK"] = pri["Short"]
    except Exception:
        df["price_Long_CZK"]  = float("nan")
        df["price_Short_CZK"] = float("nan")

    df["situation"] = df["imbalance_MWh"].apply(
        lambda v: "Surplus" if v > 0 else ("Deficit" if v < 0 else "Balanced")
    )
    df["price_relevant"] = df.apply(
        lambda r: r["price_Short_CZK"] if r["situation"] == "Deficit"
                  else r["price_Long_CZK"], axis=1
    )

    def signal(row):
        v = row["imbalance_MWh"]
        if v < -THRESHOLD_MWh: return "DISCHARGE"
        if v >  THRESHOLD_MWh: return "CHARGE"
        return "STANDBY"

    df["signal"]    = df.apply(signal, axis=1)
    df["power_pct"] = df["imbalance_MWh"].apply(
        lambda v: min(100, int(abs(v) / 150 * 100)) if abs(v) > THRESHOLD_MWh else 0
    )
    return df, now
# ── KONEC: 02_fetch_entsoe ──────────────────────────────────────


# ── BLOK: 03_fetch_dap ──────────────────────────────────────────
def fetch_dap(day: pd.Timestamp) -> pd.Series:
    """
    Stáhne Day-Ahead Prices pro daný den (CZ, EUR/MWh).
    Vrací prázdnou Series pokud data nejsou dostupná.
    ENTSO-E vrací 15min data přímo — není třeba resample.
    """
    start = day.normalize()
    if start.tzinfo is None:
        start = start.tz_localize("Europe/Prague")
    else:
        start = start.tz_convert("Europe/Prague")
    end = start + pd.Timedelta(days=1)

    try:
        raw = client.query_day_ahead_prices('CZ', start=start, end=end)
    except Exception:
        return pd.Series(dtype=float, name="dap_EUR_MWh")

    if raw is None or len(raw) == 0:
        return pd.Series(dtype=float, name="dap_EUR_MWh")

    raw = raw.tz_convert("Europe/Prague")
    raw.name = "dap_EUR_MWh"

    # Pokud jsou hodinová data (≤25 bodů) → forward-fill na 15min
    if len(raw) <= 25:
        idx_15 = pd.date_range(start=start, periods=96, freq="15min", tz="Europe/Prague")
        raw = raw.reindex(idx_15, method="ffill")

    return raw.dropna()
# ── KONEC: 03_fetch_dap ─────────────────────────────────────────


# ── BLOK: 04_fetch_dg ───────────────────────────────────────────
def fetch_deltagreen():
    """Stáhne portfolio state + available flexibility z Delta Green."""
    r1 = requests.get(
        f"{DG_BASE}/copilot/portfolio-state",
        headers=DG_HEADERS,
        params={"granularity": "15s"},
        timeout=15,
    )
    r2 = requests.get(
        f"{DG_BASE}/copilot/available-flexibility",
        headers=DG_HEADERS,
        timeout=15,
    )
    if r1.status_code != 200:
        raise RuntimeError(f"portfolio-state {r1.status_code}: {r1.text[:200]}")
    if r2.status_code != 200:
        raise RuntimeError(f"available-flexibility {r2.status_code}: {r2.text[:200]}")

    df1 = pd.DataFrame(r1.json()["records"])
    df1["time"] = pd.to_datetime(df1["time"]).dt.tz_convert("Europe/Prague")
    for col in ["batteryPowerKW", "gridPowerKW", "consumptionPowerKW", "photovoltaicPowerKW"]:
        if col not in df1.columns:
            df1[col] = None

    df2 = pd.DataFrame(r2.json()["records"])
    df2["time"] = pd.to_datetime(df2["time"]).dt.tz_convert("Europe/Prague")
    for col in ["upPowerKW", "downBatteryPowerKW", "downSolarCurtailmentPowerKW"]:
        if col not in df2.columns:
            df2[col] = None

    return df1, df2
# ── KONEC: 04_fetch_dg ──────────────────────────────────────────


# ── BLOK: 05_stats ──────────────────────────────────────────────
def calc_price_stats(series: pd.Series) -> dict:
    """Spočítá base / peak (08-20) / offpeak průměry + min/max."""
    if series.empty:
        return {"base": None, "peak": None, "offpeak": None,
                "min": None, "max": None, "n": 0}

    peak_mask    = series.index.hour.isin(PEAK_HOURS)
    offpeak_mask = ~peak_mask

    def avg(s):
        return round(float(s.mean()), 2) if len(s) > 0 else None

    return {
        "base":    avg(series),
        "peak":    avg(series[peak_mask]),
        "offpeak": avg(series[offpeak_mask]),
        "min":     round(float(series.min()), 2),
        "max":     round(float(series.max()), 2),
        "n":       int(len(series)),
    }
# ── KONEC: 05_stats ─────────────────────────────────────────────


# ── BLOK: 06_plot_entsoe ────────────────────────────────────────
def plot_entsoe(df, now):
    """Graf systémové odchylky (3 panely): MWh / cena / signál."""
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=(
            "Total Imbalance (17.1.H) [MWh/15min]",
            "Imbalance Price [CZK/MWh]",
            "Signal Delta Green API",
        )
    )
    t = df.index

    # Panel 1 — odchylka
    surplus = df[df["imbalance_MWh"] >= 0]
    deficit = df[df["imbalance_MWh"] <  0]
    fig.add_trace(go.Bar(x=surplus.index, y=surplus["imbalance_MWh"],
        name="Surplus", marker_color="#1565C0",
        hovertemplate="%{x|%H:%M}  %{y:.2f} MWh<extra>Surplus</extra>",
    ), row=1, col=1)
    fig.add_trace(go.Bar(x=deficit.index, y=deficit["imbalance_MWh"],
        name="Deficit", marker_color="#C62828",
        hovertemplate="%{x|%H:%M}  %{y:.2f} MWh<extra>Deficit</extra>",
    ), row=1, col=1)
    fig.add_hline(y= THRESHOLD_MWh, line_dash="dash", line_color="blue", opacity=0.3, row=1, col=1)
    fig.add_hline(y=-THRESHOLD_MWh, line_dash="dash", line_color="red",  opacity=0.3, row=1, col=1)

    # Panel 2 — cena
    fig.add_trace(go.Scatter(
        x=t, y=df["price_relevant"].fillna(0),
        mode="lines+markers", name="Imbalance Price",
        line=dict(color="#6A1B9A", shape="hv"),
        fill="tozeroy",
        hovertemplate="%{x|%H:%M}  %{y:.0f} CZK/MWh<extra>Cena</extra>",
    ), row=2, col=1)

    # Panel 3 — signál (přesný match)
    signal_cfg = [
        ("DISCHARGE", "#E65100", -1),
        ("CHARGE",    "#2E7D32",  1),
        ("STANDBY",   "#9E9E9E",  0.1),
    ]
    for sig_key, color, val in signal_cfg:
        mask = df["signal"] == sig_key
        if mask.any():
            fig.add_trace(go.Bar(
                x=df.index[mask],
                y=[val] * int(mask.sum()),
                name=sig_key,
                marker_color=color,
                hovertemplate="%{x|%H:%M}  " + sig_key + "<extra></extra>",
            ), row=3, col=1)

    fig.update_layout(
        height=820,
        title_text="LIVE — Systemova Odchylka CZ  (" + now.strftime("%d.%m.%Y %H:%M:%S") + ")",
        showlegend=True, template="plotly_white", hovermode="x unified",
        margin=dict(l=60, r=20, t=80, b=40),
    )
    fig.update_yaxes(title_text="MWh", row=1, col=1)
    fig.update_yaxes(title_text="CZK/MWh", row=2, col=1)
    fig.update_yaxes(tickvals=[-1, 0.1, 1], ticktext=["DISCHARGE", "STANDBY", "CHARGE"], row=3, col=1)
    fig.show()
# ── KONEC: 06_plot_entsoe ───────────────────────────────────────


# ── BLOK: 07_plot_dap ───────────────────────────────────────────
def plot_dap(s_d0: pd.Series, s_d1: pd.Series, now: pd.Timestamp):
    """Graf Day-Ahead Prices D0 + D+1 vedle sebe + tabulka statistik."""
    stats0 = calc_price_stats(s_d0)
    stats1 = calc_price_stats(s_d1)

    label0 = "D0 — " + now.strftime("%d.%m.%Y")
    label1 = "D+1 — " + (now + pd.Timedelta(days=1)).strftime("%d.%m.%Y")

    # Explicitní hranice x-os
    start_d0 = now.normalize()
    end_d0   = start_d0 + pd.Timedelta(days=1)
    start_d1 = end_d0
    end_d1   = start_d1 + pd.Timedelta(days=1)

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.5, 0.5],
        subplot_titles=[label0, label1],
        horizontal_spacing=0.08,
    )

    # D0
    if not s_d0.empty:
        fig.add_trace(go.Scatter(
            x=s_d0.index, y=s_d0.values,
            mode="lines", name="D0",
            line=dict(color="#1565C0", width=2, shape="hv"),
            fill="tozeroy", fillcolor="rgba(21,101,192,0.10)",
            hovertemplate="%{x|%H:%M}  %{y:.2f} EUR/MWh<extra>D0</extra>",
        ), row=1, col=1)

        s_peak0 = s_d0[s_d0.index.hour.isin(PEAK_HOURS)]
        if not s_peak0.empty:
            fig.add_trace(go.Scatter(
                x=s_peak0.index, y=s_peak0.values,
                mode="lines", name="D0 Peak (08-20)",
                line=dict(color="#F57F17", width=3, shape="hv"),
                hovertemplate="%{x|%H:%M}  %{y:.2f} EUR/MWh<extra>D0 Peak</extra>",
            ), row=1, col=1)

        if stats0["base"] is not None:
            fig.add_hline(y=stats0["base"], line_dash="dot",
                          line_color="#1565C0", line_width=1, row=1, col=1)
        if stats0["peak"] is not None:
            fig.add_hline(y=stats0["peak"], line_dash="dot",
                          line_color="#F57F17", line_width=1, row=1, col=1)
        fig.add_vline(x=now.isoformat(), line_dash="dot",
                      line_color="red", line_width=2, row=1, col=1)

    # D+1
    if not s_d1.empty:
        fig.add_trace(go.Scatter(
            x=s_d1.index, y=s_d1.values,
            mode="lines", name="D+1",
            line=dict(color="#2E7D32", width=2, shape="hv"),
            fill="tozeroy", fillcolor="rgba(46,125,50,0.10)",
            hovertemplate="%{x|%H:%M}  %{y:.2f} EUR/MWh<extra>D+1</extra>",
        ), row=1, col=2)

        s_peak1 = s_d1[s_d1.index.hour.isin(PEAK_HOURS)]
        if not s_peak1.empty:
            fig.add_trace(go.Scatter(
                x=s_peak1.index, y=s_peak1.values,
                mode="lines", name="D+1 Peak (08-20)",
                line=dict(color="#E65100", width=3, shape="hv"),
                hovertemplate="%{x|%H:%M}  %{y:.2f} EUR/MWh<extra>D+1 Peak</extra>",
            ), row=1, col=2)

        if stats1["base"] is not None:
            fig.add_hline(y=stats1["base"], line_dash="dot",
                          line_color="#2E7D32", line_width=1, row=1, col=2)
        if stats1["peak"] is not None:
            fig.add_hline(y=stats1["peak"], line_dash="dot",
                          line_color="#E65100", line_width=1, row=1, col=2)
    else:
        fig.add_annotation(
            text="D+1 zatim nedostupne (aukce obvykle po 13:00)",
            x=0.5, y=0.5, xref="x2 domain", yref="y2 domain",
            showarrow=False, font=dict(size=12, color="#888"),
        )

    fig.update_layout(
        height=350,
        title_text="Day-Ahead Energy Prices CZ (12.1.D) — EUR/MWh | Peak = 08-20",
        template="plotly_white", hovermode="x", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=50, t=70, b=40),
        # Klíč: explicitní range pro každý subplot zvlášť
        xaxis=dict(type="date", tickformat="%H:%M",
                   range=[start_d0.isoformat(), end_d0.isoformat()],
                   showgrid=True, gridcolor="#f0f0f0"),
        xaxis2=dict(type="date", tickformat="%H:%M",
                    range=[start_d1.isoformat(), end_d1.isoformat()],
                    showgrid=True, gridcolor="#f0f0f0"),
    )
    fig.update_yaxes(title_text="EUR/MWh", showgrid=True, gridcolor="#f0f0f0")
    fig.show()

    # Tabulka statistik pod grafem
    def fmt(v):
        return str(round(v, 2)) + " EUR" if v is not None else "—"

    col_w = 22
    print("\n" + "─" * 68)
    print("  " + " " * 18 + "  " + label0.rjust(col_w) + "  " + label1.rjust(col_w))
    print("─" * 68)
    for lbl, k in [("Base", "base"), ("Peak 08-20", "peak"),
                   ("Offpeak", "offpeak"), ("Min", "min"), ("Max", "max")]:
        print("  " + lbl.ljust(18) + "  " +
              fmt(stats0[k]).rjust(col_w) + "  " + fmt(stats1[k]).rjust(col_w))
    n0 = str(stats0["n"]) + " x 15min" if stats0["n"] else "—"
    n1 = str(stats1["n"]) + " x 15min" if stats1["n"] else "—"
    print("  " + "ISP".ljust(18) + "  " + n0.rjust(col_w) + "  " + n1.rjust(col_w))
    print("─" * 68)
# ── KONEC: 07_plot_dap ──────────────────────────────────────────


# ── BLOK: 08_plot_dg ────────────────────────────────────────────
def plot_deltagreen(df1, df2):
    """Graf Delta Green: výkon portfolia + disponibilní flexibilita."""
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Vykon portfolia [kW]", "Disponibilni flexibilita [kW]"),
        vertical_spacing=0.12,
        shared_xaxes=True,
    )

    for col, name, color in [
        ("batteryPowerKW",      "Baterie",      "#4A90D9"),
        ("consumptionPowerKW",  "Spotreba",     "#E8744A"),
        ("photovoltaicPowerKW", "Fotovoltaika", "#F5C518"),
        ("gridPowerKW",         "Sit",          "#2ECC71"),
    ]:
        fig.add_trace(go.Scatter(
            x=df1["time"], y=df1[col], name=name,
            line=dict(color=color, width=1.5),
            hovertemplate="<b>" + name + "</b><br>%{x|%H:%M:%S}<br>%{y:.2f} kW<extra></extra>",
            legendgroup="g1",
        ), row=1, col=1)

    for col, name, fc, lc in [
        ("upPowerKW",                   "Maximum UP",       "rgba(0,191,166,0.4)",  "#00BFA6"),
        ("downBatteryPowerKW",          "Nabijeni bat.",    "rgba(74,144,217,0.4)", "#4A90D9"),
        ("downSolarCurtailmentPowerKW", "Zakaz pretoku FVE","rgba(245,197,24,0.4)", "#F5C518"),
    ]:
        fig.add_trace(go.Scatter(
            x=df2["time"], y=df2[col], name=name,
            fill="tozeroy", fillcolor=fc, line=dict(color=lc, width=1.5),
            hovertemplate="<b>" + name + "</b><br>%{x|%H:%M:%S}<br>%{y:.2f} kW<extra></extra>",
            legendgroup="g2",
        ), row=2, col=1)

    last2 = df2.dropna(subset=["upPowerKW","downBatteryPowerKW",
                                "downSolarCurtailmentPowerKW"]).iloc[-1]
    total_down = float(last2["downBatteryPowerKW"]) + float(last2["downSolarCurtailmentPowerKW"])

    fig.update_layout(
        height=600,
        title_text=(
            "Delta Green Portfolio  |  UP: " + str(round(float(last2["upPowerKW"]))) + " kW  |  "
            "DOWN: " + str(round(total_down)) + " kW"
        ),
        template="plotly_white", hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=20, t=80, b=40),
    )
    for row in [1, 2]:
        fig.update_xaxes(tickformat="%H:%M", showgrid=True, gridcolor="#f0f0f0", row=row, col=1)
        fig.update_yaxes(title_text="kW", showgrid=True, gridcolor="#f0f0f0",
                         zeroline=True, zerolinecolor="#cccccc", row=row, col=1)
    fig.show()
# ── KONEC: 08_plot_dg ───────────────────────────────────────────


# ── BLOK: 09_snapshot ───────────────────────────────────────────
def print_snapshot(df_e, df1, df2):
    """Textový výpis posledního stavu — ENTSO-E + Delta Green."""
    last_e = df_e.iloc[-1]
    last1  = df1.dropna(subset=["batteryPowerKW","consumptionPowerKW",
                                 "photovoltaicPowerKW","gridPowerKW"]).iloc[-1]
    last2  = df2.dropna(subset=["upPowerKW","downBatteryPowerKW",
                                 "downSolarCurtailmentPowerKW"]).iloc[-1]

    sep = "=" * 65
    print("\n" + sep)
    print("  Odchylka: " + str(round(float(last_e["imbalance_MWh"]), 2)) +
          " MWh  |  " + str(last_e["situation"]) +
          "  |  Signal: " + str(last_e["signal"]) +
          "  |  Power: " + str(last_e["power_pct"]) + "%")
    print("  Imb.Price: " + str(round(float(last_e["price_relevant"]), 0)) + " CZK/MWh")
    print(sep)
    print("  Baterie:      " + str(round(float(last1["batteryPowerKW"]), 2)) +
          " kW       UP:           " + str(round(float(last2["upPowerKW"]), 2)) + " kW")
    print("  Fotovoltaika: " + str(round(float(last1["photovoltaicPowerKW"]), 2)) +
          " kW       Nabijeni bat: " + str(round(float(last2["downBatteryPowerKW"]), 2)) + " kW")
    print("  Spotreba:     " + str(round(float(last1["consumptionPowerKW"]), 2)) +
          " kW       Zakaz FVE:    " + str(round(float(last2["downSolarCurtailmentPowerKW"]), 2)) + " kW")
    print("  Sit:          " + str(round(float(last1["gridPowerKW"]), 2)) + " kW")
    print(sep)
# ── KONEC: 09_snapshot ──────────────────────────────────────────


# ── BLOK: 13_fetch_generation ───────────────────────────────────
def fetch_generation(now: pd.Timestamp) -> pd.DataFrame:
    """
    Stáhne data výroby podle typu zdroje (CZ) za dnešní den.
    Vrací DataFrame s PSR kódy jako sloupci a výkonem v MW.
    """
    start = now.normalize()
    end   = now + pd.Timedelta(hours=1)
    try:
        gen = client.query_generation("CZ", start=start, end=end, psr_type=None)
    except Exception as e:
        print(f"  [WARN] Generace: {e}")
        return pd.DataFrame()
    if gen is None or gen.empty:
        return pd.DataFrame()
    # Rozbalení MultiIndex: ponecháme pouze "Actual Aggregated"
    if isinstance(gen.columns, pd.MultiIndex):
        lvls = gen.columns.get_level_values(1)
        gen = (gen.xs("Actual Aggregated", level=1, axis=1)
               if "Actual Aggregated" in lvls
               else gen.xs(lvls[0], level=1, axis=1))
    return gen.tz_convert("Europe/Prague") if gen.index.tz else gen


def plot_generation(df_gen: pd.DataFrame, now: pd.Timestamp):
    """
    Stacked area chart výroby podle zdroje + aktuální celkový výkon.
    Barvy dle opravených PSR kódů ENTSO-E.
    """
    if df_gen.empty:
        print("  [INFO] Žádná data generace.")
        return

    fig = go.Figure()

    def _key(c):
        psr = str(c[0]) if isinstance(c, tuple) else str(c)
        return GEN_STACK_ORDER.index(psr) if psr in GEN_STACK_ORDER else 999

    total = pd.Series(0.0, index=df_gen.index)
    for col in sorted(df_gen.columns, key=_key):
        name, color = psr_lookup(col)
        series = df_gen[col].fillna(0)
        if series.sum() < 1:
            continue
        total = total + series
        # rgba fill z hex barvy
        if color.startswith("#") and len(color) == 7:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            fill_color = f"rgba({r},{g},{b},0.78)"
        else:
            fill_color = color
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values,
            stackgroup="gen", name=name,
            line=dict(width=0, color=color),
            fillcolor=fill_color,
            hovertemplate=f"{name}: %{{y:.0f}} MW<extra></extra>",
        ))

    # Celkový výkon — tmavá linka nahoře
    fig.add_trace(go.Scatter(
        x=total.index, y=total.values, mode="lines", name="Celkem",
        line=dict(color="#212121", width=1.5),
        hovertemplate="<b>Celkem: %{y:.0f} MW</b><extra></extra>",
    ))

    # Čára NOW
    fig.add_vline(x=now.isoformat(), line_color="#1565C0", line_width=1.5)

    cur_total = float(total.iloc[-1]) if not total.empty else 0.0
    fig.update_layout(
        height=380,
        title_text=(f"Výroba podle zdroje — {now.strftime('%d.%m.%Y %H:%M')}  |  "
                    f"Aktuálně: {cur_total:,.0f} MW"),
        template="plotly_white", hovermode="x unified",
        legend=dict(orientation="h", y=-0.2, x=0, font=dict(size=10)),
        margin=dict(l=60, r=15, t=50, b=60),
    )
    fig.update_xaxes(tickformat="%H:%M", title_text="Čas")
    fig.update_yaxes(title_text="MW")
    fig.show()

    # Textový výpis aktuálního mixu
    last_row = df_gen.dropna(how="all").iloc[-1]
    items = []
    for col, val in last_row.items():
        name, _ = psr_lookup(col)
        if pd.notna(val) and float(val) > 0:
            items.append((float(val), name))
    items.sort(reverse=True)
    sep = "─" * 52
    print(sep)
    print(f"  GENERACE — aktuální mix ({now.strftime('%H:%M')})")
    print(sep)
    for val, name in items:
        pct = val / cur_total * 100 if cur_total else 0
        bar = "█" * int(pct / 5)
        print(f"  {name:<22s}  {val:>6.0f} MW  {pct:>4.0f}%  {bar}")
    print(f"  {'CELKEM':<22s}  {cur_total:>6.0f} MW")
    print(sep)

# ── KONEC: 13_fetch_generation ──────────────────────────────────


# ── BLOK: 14_fetch_load ─────────────────────────────────────────
def fetch_load(now: pd.Timestamp) -> tuple:
    """
    Stáhne skutečné zatížení + prognózu D+1 pro CZ.
    Vrací (load_actual, load_forecast) jako pd.Series.
    """
    start = now.normalize()
    end   = start + pd.Timedelta(days=2)
    try:
        actual = client.query_load("CZ", start=start, end=end)
        if isinstance(actual, pd.DataFrame):
            actual = actual.iloc[:, 0]
        actual = actual.rename("actual_MW").tz_convert("Europe/Prague")
    except Exception as e:
        print(f"  [WARN] Zatížení actual: {e}")
        actual = pd.Series(dtype="float64", name="actual_MW")
    try:
        forecast = client.query_load_forecast("CZ", start=start, end=end)
        if isinstance(forecast, pd.DataFrame):
            forecast = forecast.iloc[:, 0]
        forecast = forecast.rename("forecast_MW").tz_convert("Europe/Prague")
    except Exception as e:
        print(f"  [WARN] Zatížení forecast: {e}")
        forecast = pd.Series(dtype="float64", name="forecast_MW")
    return actual, forecast


def plot_load(load_actual: pd.Series, load_fc: pd.Series, now: pd.Timestamp):
    """Graf zatížení: skutečnost (červená) vs. prognóza D+1 (zelená)."""
    if load_actual.empty and load_fc.empty:
        print("  [INFO] Žádná data zatížení.")
        return
    fig = go.Figure()
    if not load_fc.empty:
        fig.add_trace(go.Scatter(
            x=load_fc.index, y=load_fc.values, mode="lines",
            name="Prognóza D+1", line=dict(color="#26A69A", width=2, shape="hv"),
            hovertemplate="<b>%{x|%a %H:%M}</b><br>Prognóza: %{y:,.0f} MW<extra></extra>",
        ))
    if not load_actual.empty:
        fig.add_trace(go.Scatter(
            x=load_actual.index, y=load_actual.values, mode="lines",
            name="Skutečnost", line=dict(color="#E91E63", width=2, shape="hv"),
            hovertemplate="<b>%{x|%a %H:%M}</b><br>Skutečnost: %{y:,.0f} MW<extra></extra>",
        ))
    fig.add_vline(x=now.isoformat(), line_color="#1565C0", line_width=1.5)
    fig.update_layout(
        height=300,
        title_text=f"Zatížení CZ — skutečnost vs. prognóza D+1  ({now.strftime('%d.%m.%Y')})",
        template="plotly_white", hovermode="x unified",
        legend=dict(orientation="h", y=-0.2, x=0),
        margin=dict(l=60, r=15, t=50, b=50),
    )
    fig.update_xaxes(tickformat="%H:%M\n%d.%m", title_text="Čas")
    fig.update_yaxes(title_text="MW")
    fig.show()

# ── KONEC: 14_fetch_load ────────────────────────────────────────


# ── BLOK: 15_fetch_reserves ─────────────────────────────────────
def fetch_reserves(now: pd.Timestamp) -> dict:
    """
    Stáhne contracted reserve amounts + prices pro CZ:
      A01 (denní)  — D-1 až D+7
      A04 (roční)  — ode dneška + 12 měsíců (rovná čára)
    Vrací dict s klíči a01_amount, a01_price, a04_amount, a04_price.
    """
    start    = now.normalize() - pd.Timedelta(days=1)
    end      = now.normalize() + pd.Timedelta(days=8)
    start_yr = now.normalize()
    end_yr   = now.normalize() + pd.Timedelta(days=366)

    def _q(fn, *a, **kw):
        try:
            r = fn(*a, **kw)
            return r if r is not None else pd.DataFrame()
        except Exception as e:
            print(f"  [WARN] rezervy: {e}")
            return pd.DataFrame()

    return dict(
        a01_amount = _q(client.query_contracted_reserve_amount, "CZ", start, end, "A01"),
        a01_price  = _q(client.query_contracted_reserve_prices,  "CZ", start, end, "A01"),
        a04_amount = _q(client.query_contracted_reserve_amount, "CZ", start_yr, end_yr, "A04"),
        a04_price  = _q(client.query_contracted_reserve_prices,  "CZ", start_yr, end_yr, "A04"),
        start = start,
        end   = end,
    )


def _rseries(df, *keywords) -> pd.Series:
    """Najde první sloupec df odpovídající klíčovému slovu (case-insensitive)."""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for kw in keywords:
        hits = [c for c in df.columns if kw.lower() in str(c).lower()]
        if hits:
            s = df[hits[0]].dropna()
            if hasattr(s.index, "tz") and s.index.tz is not None:
                s = s.tz_convert("Europe/Prague")
            return s
    num = df.select_dtypes("number").columns.tolist()
    if num:
        s = df[num[0]].dropna()
        if hasattr(s.index, "tz") and s.index.tz is not None:
            s = s.tz_convert("Europe/Prague")
        return s
    return pd.Series(dtype=float)


def plot_reserves(reserves: dict, now: pd.Timestamp):
    """
    Graf 1 — aFRR (A01 denní + A04 roční): Up/Down množství (MW) + ceny (EUR/MW)
    Graf 2 — mFRR (A01 denní): Symmetric množství (MW) + cena (EUR/MW)
    Levá osa = MW, pravá osa = EUR/MW.
    """
    start = reserves["start"]
    end   = reserves["end"]

    # ── Graf 1: aFRR ──────────────────────────────────────────────
    a01_up_mw    = _rseries(reserves["a01_amount"], "up",   "Up")
    a01_down_mw  = _rseries(reserves["a01_amount"], "down", "Down")
    a04_up_mw    = _rseries(reserves["a04_amount"], "up",   "Up")
    a04_down_mw  = _rseries(reserves["a04_amount"], "down", "Down")
    a01_up_eur   = _rseries(reserves["a01_price"],  "up",   "Up")
    a01_down_eur = _rseries(reserves["a01_price"],  "down", "Down")
    a04_up_eur   = _rseries(reserves["a04_price"],  "up",   "Up")
    a04_down_eur = _rseries(reserves["a04_price"],  "down", "Down")

    from plotly.subplots import make_subplots as _msp
    fig1 = _msp(specs=[[{"secondary_y": True}]])

    CU, CD, CU2, CD2 = "#1565C0", "#C62828", "#42A5F5", "#EF5350"

    for series, name, color, dash, sec, width in [
        (a01_up_mw,    "A01 Up ↑ [MW]",     CU,  "solid",   False, 2.2),
        (a01_down_mw,  "A01 Down ↓ [MW]",   CD,  "solid",   False, 2.2),
        (a04_up_mw,    "A04 Up ↑ [MW]",     CU,  "dash",    False, 2.0),
        (a04_down_mw,  "A04 Down ↓ [MW]",   CD,  "dash",    False, 2.0),
        (a01_up_eur,   "A01 Up ↑ [€/MW]",   CU2, "dot",     True,  1.5),
        (a01_down_eur, "A01 Down ↓ [€/MW]", CD2, "dot",     True,  1.5),
        (a04_up_eur,   "A04 Up ↑ [€/MW]",   CU2, "dashdot", True,  1.5),
        (a04_down_eur, "A04 Down ↓ [€/MW]", CD2, "dashdot", True,  1.5),
    ]:
        if series.empty:
            continue
        fig1.add_trace(go.Scatter(
            x=series.index, y=series.values, name=name, mode="lines",
            line=dict(color=color, width=width, dash=dash),
            hovertemplate=f"{name}: %{{y:,.1f}}<extra></extra>",
        ), secondary_y=sec)

    fig1.add_vline(x=now.isoformat(), line_color="#1565C0", line_width=1.5)
    fig1.update_layout(
        height=420,
        title_text=f"aFRR — rezervy a ceny CZ ({now.strftime('%d.%m.%Y %H:%M')})",
        template="plotly_white", hovermode="x unified",
        legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=10)),
        margin=dict(l=65, r=70, t=45, b=75),
        xaxis=dict(
            type="date",
            tickformat="%a %d.%m\n%H:%M",
            range=[start.isoformat(), end.isoformat()],
        ),
    )
    fig1.update_yaxes(title_text="MW",     secondary_y=False)
    fig1.update_yaxes(title_text="EUR/MW", secondary_y=True, showgrid=False)
    fig1.show()

    # ── Graf 2: mFRR ──────────────────────────────────────────────
    sym_mw  = _rseries(reserves["a01_amount"], "sym", "Symm", "Symmetric")
    sym_eur = _rseries(reserves["a01_price"],  "sym", "Symm", "Symmetric")

    fig2 = _msp(specs=[[{"secondary_y": True}]])
    CS, CS2 = "#2E7D32", "#66BB6A"

    for series, name, color, dash, sec, width in [
        (sym_mw,  "A01 Symmetric [MW]",   CS,  "solid", False, 2.2),
        (sym_eur, "A01 Symmetric [€/MW]", CS2, "dot",   True,  1.5),
    ]:
        if series.empty:
            continue
        fig2.add_trace(go.Scatter(
            x=series.index, y=series.values, name=name, mode="lines",
            line=dict(color=color, width=width, dash=dash),
            hovertemplate=f"{name}: %{{y:,.1f}}<extra></extra>",
        ), secondary_y=sec)

    fig2.add_vline(x=now.isoformat(), line_color="#1565C0", line_width=1.5)
    fig2.update_layout(
        height=380,
        title_text=f"mFRR — rezervy a ceny CZ ({now.strftime('%d.%m.%Y %H:%M')})",
        template="plotly_white", hovermode="x unified",
        legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=10)),
        margin=dict(l=65, r=70, t=45, b=75),
        xaxis=dict(
            type="date",
            tickformat="%a %d.%m\n%H:%M",
            range=[start.isoformat(), end.isoformat()],
        ),
    )
    fig2.update_yaxes(title_text="MW",     secondary_y=False)
    fig2.update_yaxes(title_text="EUR/MW", secondary_y=True, showgrid=False)
    fig2.show()

    # Textový výpis aktuálních hodnot
    sep = "─" * 56
    print(sep)
    print(f"  REZERVY — aktuální hodnoty ({now.strftime('%H:%M')})")
    print(sep)
    for label, s in [
        ("aFRR A01 Up MW",     a01_up_mw),
        ("aFRR A01 Down MW",   a01_down_mw),
        ("aFRR A04 Up MW",     a04_up_mw),
        ("aFRR A04 Down MW",   a04_down_mw),
        ("aFRR A01 Up €/MW",   a01_up_eur),
        ("aFRR A01 Down €/MW", a01_down_eur),
        ("mFRR A01 Sym MW",    sym_mw),
        ("mFRR A01 Sym €/MW",  sym_eur),
    ]:
        val = f"{s.iloc[-1]:,.1f}" if not s.empty else "—"
        print(f"  {label:<22s}  {val:>10s}")
    print(sep)

# ── KONEC: 15_fetch_reserves ────────────────────────────────────


# ── BLOK: 11_fetch_outages ──────────────────────────────────────
def fetch_outages(now: pd.Timestamp, days_ahead: int = 7) -> pd.DataFrame:
    """
    Stáhne odstávky výrobních jednotek (PU) a generačních jednotek (GU)
    z ENTSO-E pro CZ, od dnešního dne do days_ahead.
    Vrací prázdný DataFrame pokud data nejsou dostupná.
    """
    start = now.normalize()
    end   = start + pd.Timedelta(days=days_ahead)
    frames = []
    for level, fn in [
        ("PU", client.query_unavailability_of_production_units),
        ("GU", client.query_unavailability_of_generation_units),
    ]:
        try:
            raw = fn("CZ", start=start, end=end)
            if raw is not None and not raw.empty:
                raw = raw.copy()
                raw["unit_level"] = level
                frames.append(raw)
        except Exception as e:
            print(f"  [WARN] {level} odstávky: {e}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def parse_outages(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalizuje surová data odstávek — přejmenuje sloupce, dopočítá MW."""
    if raw.empty:
        return pd.DataFrame()
    df = raw.copy().rename(columns={
        "start":                     "outage_start",
        "end":                       "outage_end",
        "nominal_power":             "installed_MW",
        "avail_qty":                 "available_MW",
        "businesstype":              "outage_type",
        "production_resource_name":  "unit_raw",
        "production_resource_id":    "eic_code",
    })
    df["unit_name"]    = df["unit_raw"].apply(unit_display_name)
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
    keep = ["unit_raw", "eic_code", "unit_name", "unit_level",
            "outage_start", "outage_end",
            "installed_MW", "available_MW", "unavailable_MW", "available_pct",
            "outage_type", "mrid"]
    return df[[c for c in keep if c in df.columns]]


def detect_changes(df_prev: pd.DataFrame, df_curr: pd.DataFrame) -> dict:
    """
    Porovná dvě verze tabulky odstávek a vrátí:
      new        — sety klic-tuplů nových odstávek
      ended      — sety klic-tuplů ukončených odstávek
      changed_mw — DataFrame řádků se změněnou available_MW
    """
    key = ["unit_raw", "outage_start", "outage_end"]
    if df_prev is None or df_prev.empty:
        return {"new": set(), "ended": set(), "changed_mw": pd.DataFrame()}
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
        "changed_mw": changed[["unit_name", "prev_MW", "available_MW", "delta_MW"]]
                      if not changed.empty else pd.DataFrame(),
    }

# ── KONEC: 11_fetch_outages ─────────────────────────────────────


# ── BLOK: 12_plot_outages ───────────────────────────────────────
def plot_outages(df_out: pd.DataFrame, now: pd.Timestamp,
                 level: str = "PU", changes: dict = None):
    """
    Gantt chart odstávek pro danou úroveň (PU nebo GU).
    Nové odstávky (z changes) jsou zvýrazněny oranžově.
    """
    COLOR_NEW     = "#FF6B35"
    COLOR_SURPLUS = "#1565C0"

    fig = go.Figure()
    sub = df_out[df_out["unit_level"] == level].copy() if not df_out.empty else pd.DataFrame()

    if sub.empty:
        fig.add_annotation(
            text=f"Žádné odstávky na úrovni {level}",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=13, color="#9E9E9E"),
        )
        fig.update_layout(
            height=180,
            title_text=f"Odstávky {level} — žádná data",
            template="plotly_white",
        )
        fig.show()
        return

    # Řazení: největší výpadek nahoře
    impact_order = (sub.groupby("unit_name")["unavailable_MW"]
                       .max()
                       .sort_values(ascending=True).index.tolist())
    sub["unit_name"] = pd.Categorical(sub["unit_name"], categories=impact_order, ordered=True)

    n_units = max(1, sub["unit_name"].nunique())
    height  = max(200, n_units * 32 + 100)

    xstart = now - pd.Timedelta(days=1)
    xend   = now.normalize() + pd.Timedelta(days=7)

    # Šedé pruhy víkendů
    cur = xstart.normalize()
    while cur < xend:
        if cur.weekday() == 5:
            fig.add_vrect(
                x0=cur, x1=cur + pd.Timedelta(days=2),
                fillcolor="#90A4AE", opacity=0.07, layer="below", line_width=0,
            )
        cur += pd.Timedelta(days=1)

    # Modré podbarvení příštích 24 hodin
    fig.add_vrect(
        x0=now, x1=now + pd.Timedelta(hours=24),
        fillcolor=COLOR_SURPLUS, opacity=0.04, layer="below", line_width=0,
    )

    new_keys = (changes or {}).get("new", set())

    for _, r in sub.iterrows():
        key = (r["unit_raw"], r["outage_start"], r["outage_end"])
        is_new   = key in new_keys
        bar_color = COLOR_NEW if is_new else pct_to_color(r.get("available_pct", 0))
        border    = dict(width=2, color=COLOR_NEW) if is_new \
                    else dict(width=0.5, color="rgba(0,0,0,0.15)")
        y_label  = f"{r['unit_name']}  ({r['installed_MW']:.0f} MW)"
        hover    = (
            f"<b>{r['unit_name']}</b> [{level}]<br>"
            f"Typ: {r['outage_type']}<br>"
            f"Instalovaný: {r['installed_MW']:.0f} MW  |  "
            f"Dostupný: {r['available_MW']:.0f} MW<br>"
            f"<b>Výpadek: {r['unavailable_MW']:.0f} MW "
            f"({100 - r['available_pct']:.0f} %)</b><br>"
            f"Od: {r['outage_start'].strftime('%a %d.%m %H:%M')}  →  "
            f"Do: {r['outage_end'].strftime('%a %d.%m %H:%M')}"
            + ("  🆕 NOVÁ" if is_new else "")
        )
        duration_ms = (r["outage_end"] - r["outage_start"]).total_seconds() * 1000
        fig.add_trace(go.Bar(
            x=[duration_ms],
            y=[y_label],
            base=[r["outage_start"].timestamp() * 1000],
            orientation="h",
            marker_color=bar_color,
            marker_line=border,
            hovertext=hover, hoverinfo="text",
            showlegend=False,
            width=0.65,
        ))

    # Čára NOW
    now_iso = now.isoformat()
    fig.add_vline(x=now_iso, line_color=COLOR_SURPLUS, line_width=1.5)
    fig.add_annotation(
        x=now_iso, y=1, yref="paper", yanchor="bottom",
        text="NOW", showarrow=False, xshift=4,
        font=dict(size=10, color=COLOR_SURPLUS),
    )

    n_new = len(new_keys & set(sub[["unit_raw", "outage_start", "outage_end"]]
                                .apply(tuple, axis=1)))
    title = (
        f"Odstávky {level} — {now.strftime('%d.%m.%Y %H:%M')}  |  "
        f"Celkem: {n_units} jednotek  |  "
        f"Výpadek: {sub['unavailable_MW'].sum():.0f} MW celkem"
        + (f"  |  🆕 {n_new} nových" if n_new else "")
    )

    fig.update_layout(
        height=height,
        title_text=title,
        barmode="overlay",
        template="plotly_white",
        hovermode="closest",
        margin=dict(l=200, r=20, t=50, b=40),
        xaxis=dict(
            type="date",
            tickformat="%a %d.%m\n%H:%M",
            range=[xstart.isoformat(), xend.isoformat()],
        ),
        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
    )
    fig.show()


def print_outage_summary(df_out: pd.DataFrame, changes: dict = None):
    """Textový výpis největších odstávek + případné změny od posledního refreshe."""
    if df_out.empty:
        print("  Žádné odstávky.\n")
        return
    sep = "─" * 72
    print(sep)
    total_mw = df_out["unavailable_MW"].sum()
    print(f"  ODSTÁVKY — celkem výpadek: {total_mw:.0f} MW  "
          f"({len(df_out)} záznamů, {df_out['unit_name'].nunique()} jednotek)")
    print(sep)
    top = df_out.nlargest(8, "unavailable_MW")
    for _, r in top.iterrows():
        print(
            f"  [{r['unit_level']}] {r['unit_name']:<30s}  "
            f"{r['unavailable_MW']:>6.0f} MW výpadek  "
            f"({r['available_pct']:.0f}% dostupné)  "
            f"do {r['outage_end'].strftime('%d.%m %H:%M')}"
        )
    if changes:
        n_new    = len(changes.get("new", set()))
        n_ended  = len(changes.get("ended", set()))
        changed  = changes.get("changed_mw", pd.DataFrame())
        if n_new or n_ended or not changed.empty:
            print(sep)
            print(f"  ZMĚNY: 🆕 {n_new} nových  |  ✅ {n_ended} ukončených  "
                  f"|  ✏️ {len(changed)} se změněným výkonem")
            if not changed.empty:
                for _, row in changed.iterrows():
                    print(f"    {row['unit_name']}: {row['prev_MW']:.0f} → "
                          f"{row['available_MW']:.0f} MW  (Δ {row['delta_MW']:+.0f} MW)")
    print(sep)

# ── KONEC: 12_plot_outages ──────────────────────────────────────


# ── BLOK: 10_loop ───────────────────────────────────────────────
# SPUSŤ JAKO POSLEDNÍ — zastaví se přes tlačítko ■ Stop
#
# Přepínače — uprav dle potřeby:
SHOW_GENERATION  = True   # stacked area generace
SHOW_LOAD        = True   # zatížení vs. prognóza
SHOW_OUTAGES     = True   # Gantt odstávky (pomalejší API)
SHOW_RESERVES    = True   # aFRR + mFRR rezervy a ceny
#
# Pomalejší zdroje se refreshují méně často:
SLOW_REFRESH_EVERY = 5    # každých N iterací (generace, zatížení, odstávky)

print("Spoustim monitor (refresh " + str(REFRESH_SEC) + "s). Zastav tlacitkem Stop.")

iteration    = 0
df_out_prev  = None
df_out_cache   = pd.DataFrame()
df_gen_cache   = pd.DataFrame()
reserves_cache = {}
load_act_cache = pd.Series(dtype="float64", name="actual_MW")
load_fc_cache  = pd.Series(dtype="float64", name="forecast_MW")

while True:
    try:
        df_entsoe, now = fetch_entsoe()
        s_d0 = fetch_dap(now)
        s_d1 = fetch_dap(now + pd.Timedelta(days=1))

        dg_ok, dg_err_msg = False, ""
        try:
            df1_dg, df2_dg = fetch_deltagreen()
            dg_ok = True
        except Exception as e:
            dg_err_msg = str(e)

        # Pomalejší zdroje — refresh každých SLOW_REFRESH_EVERY iterací
        if iteration % SLOW_REFRESH_EVERY == 0:
            if SHOW_GENERATION:
                try:
                    df_gen_cache = fetch_generation(now)
                except Exception as e:
                    print(f"  [WARN] Generace: {e}")

            if SHOW_LOAD:
                try:
                    load_act_cache, load_fc_cache = fetch_load(now)
                except Exception as e:
                    print(f"  [WARN] Zatížení: {e}")

            if SHOW_RESERVES:
                try:
                    reserves_cache = fetch_reserves(now)
                except Exception as e:
                    print(f"  [WARN] Rezervy: {e}")

            if SHOW_OUTAGES:
                try:
                    raw_out    = fetch_outages(now)
                    df_out_new = parse_outages(raw_out)
                    if not df_out_new.empty:
                        changes      = detect_changes(df_out_prev, df_out_new)
                        df_out_prev  = df_out_cache.copy() if not df_out_cache.empty else None
                        df_out_cache = df_out_new
                    else:
                        changes = None
                except Exception as e:
                    print(f"  [WARN] Odstávky: {e}")
                    changes = None
            else:
                changes = None

        clear_output(wait=True)

        # ── Grafy odchylka + DAP ──
        plot_entsoe(df_entsoe, now)
        plot_dap(s_d0, s_d1, now)

        # ── Generace ──
        if SHOW_GENERATION and not df_gen_cache.empty:
            plot_generation(df_gen_cache, now)

        # ── Zatížení ──
        if SHOW_LOAD and (not load_act_cache.empty or not load_fc_cache.empty):
            plot_load(load_act_cache, load_fc_cache, now)

        # ── Delta Green ──
        if dg_ok:
            plot_deltagreen(df1_dg, df2_dg)
            print_snapshot(df_entsoe, df1_dg, df2_dg)
        else:
            print("Delta Green nedostupny: " + dg_err_msg)
            out = df_entsoe[["imbalance_MWh","situation","price_relevant",
                              "signal","power_pct"]].tail(8).copy()
            out.index = out.index.strftime("%d.%m %H:%M")
            print(out.to_string())

        # ── Rezervy ──
        if SHOW_RESERVES and reserves_cache:
            plot_reserves(reserves_cache, now)

        # ── Odstávky ──
        if SHOW_OUTAGES and not df_out_cache.empty:
            plot_outages(df_out_cache, now, level="PU", changes=changes)
            plot_outages(df_out_cache, now, level="GU", changes=changes)
            print_outage_summary(df_out_cache, changes)

        iteration += 1
        slow_next = SLOW_REFRESH_EVERY - (iteration % SLOW_REFRESH_EVERY)
        print(
            "\n#" + str(iteration) + " | " + now.strftime("%H:%M:%S") +
            " | D0: " + str(len(s_d0)) + " ISP" +
            " | D+1: " + str(len(s_d1)) + " ISP" +
            f" | pomalý refresh za {slow_next} it." +
            " | refresh za " + str(REFRESH_SEC) + "s"
        )
        time.sleep(REFRESH_SEC)

    except KeyboardInterrupt:
        print("Zastaveno.")
        break
    except Exception as e:
        print("Chyba: " + str(e))
        time.sleep(REFRESH_SEC)
# ── KONEC: 10_loop ──────────────────────────────────────────────
