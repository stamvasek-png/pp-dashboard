# ╔══════════════════════════════════════════════════════════════╗
# ║  PP DASHBOARD v1                                             ║
# ║  ENTSO-E: systémová odchylka + DAP ceny D0/D+1              ║
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


# ── BLOK: 10_loop ───────────────────────────────────────────────
# SPUSŤ JAKO POSLEDNÍ — zastaví se přes tlačítko ■ Stop
print("Spoustim monitor (refresh " + str(REFRESH_SEC) + "s). Zastav tlacitkem Stop.")

iteration = 0
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

        clear_output(wait=True)

        plot_entsoe(df_entsoe, now)
        plot_dap(s_d0, s_d1, now)

        if dg_ok:
            plot_deltagreen(df1_dg, df2_dg)
            print_snapshot(df_entsoe, df1_dg, df2_dg)
        else:
            print("Delta Green nedostupny: " + dg_err_msg)
            out = df_entsoe[["imbalance_MWh","situation","price_relevant",
                              "signal","power_pct"]].tail(8).copy()
            out.index = out.index.strftime("%d.%m %H:%M")
            print(out.to_string())

        iteration += 1
        print("\n#" + str(iteration) + " | " + now.strftime("%H:%M:%S") +
              " | D0: " + str(len(s_d0)) + " ISP" +
              " | D+1: " + str(len(s_d1)) + " ISP" +
              " | refresh za " + str(REFRESH_SEC) + "s")
        time.sleep(REFRESH_SEC)

    except KeyboardInterrupt:
        print("Zastaveno.")
        break
    except Exception as e:
        print("Chyba: " + str(e))
        time.sleep(REFRESH_SEC)
# ── KONEC: 10_loop ──────────────────────────────────────────────
