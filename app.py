# ╔══════════════════════════════════════════════════════════════╗
# ║  PP DASHBOARD — Streamlit app                                ║
# ║  Spuštění:  streamlit run app.py                             ║
# ╚══════════════════════════════════════════════════════════════╝

import pandas as pd
import streamlit as st

from config import (
    CSS_STYLES, THRESHOLD,
    C_DEFICIT, C_SURPLUS, C_OK, C_WARN, C_NEW, C_TEXT, C_MUTED,
    sparkline_svg,
)
from data.entsoe import (
    fetch_entsoe_data, fetch_dap, fetch_installed_capacity,
    fetch_activation_prices, fetch_wind_solar_forecast, fetch_reserves,
)
from data.ceps import (
    fetch_ceps_imbalance, fetch_ceps_svr, fetch_ceps_imbalance_price, fetch_ceps_all,
)
from data.deltagreen import fetch_deltagreen
from data.entsog import fetch_entsog_flows, load_entsog_history
from charts.gas import (
    fig_gas_flows_bar, fig_gas_point_history, fig_gas_map,
    fig_flow_timeseries, fig_flow_seasonality,
)
from charts.imbalance import (
    parse_imbalance,
    fig_ceps_dashboard, fig_ceps_combined, fig_ceps_svr,
    fig_imbalance, fig_signal, fig_activation_prices,
    balancing_strategy_ema, fig_balancing_strategy,
)
from charts.generation import (
    fig_generation_area, fig_generation_donut,
    fig_wind_solar_forecast, fig_load, render_mix_legend,
    fig_deltagreen,
)
from charts.outages import (
    parse_outages, detect_changes,
    fig_outages_gantt, fig_installed_capacity,
)
from charts.reserves import (
    fig_reserve_volumes, fig_reserve_prices,
    fig_dap, calc_dap_stats, simulate_battery_dap, fig_battery_strategy,
)
from predict import run_prediction, load_latest_predictions, prediction_trace

# ── PAGE CONFIG ─────────────────────────────────────────────────
st.set_page_config(
    page_title="PP Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(CSS_STYLES, unsafe_allow_html=True)

# ── SESSION STATE ────────────────────────────────────────────────
for key, default in [
    ("df_out_prev", None),
    ("dg_api_key", ""),
    ("iteration", 0),
    ("df_pred", None),
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

    # ── Predikce ACE ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🤖 Predikce ACE")
    if st.button("🔮 Spustit predikci D+1", use_container_width=True):
        from data.ceps import ceps
        from data.entsoe import client
        df_pred_result = run_prediction(ceps, client)
        if df_pred_result is not None:
            st.session_state["df_pred"] = df_pred_result
            st.success("Predikce OK")
        else:
            st.error("Predikce selhala — zkontroluj data")

    _df_latest = load_latest_predictions()
    if not _df_latest.empty:
        _last_run = _df_latest["predicted_at"].iloc[0]
        st.caption(
            f"Poslední predikce: {_last_run.strftime('%H:%M %d.%m')}  \n"
            f"Horizont: {_df_latest.index.min().strftime('%H:%M')} → "
            f"{_df_latest.index.max().strftime('%H:%M %d.%m')}"
        )

    st.markdown("---")
    with st.expander("🔋 Nastavení baterie"):
        bat_capacity_kwh = st.number_input(
            "Kapacita baterie [kWh]", value=100, min_value=10, max_value=10000, step=10)
        bat_power_kw = st.number_input(
            "Výkon baterie [kW]", value=50, min_value=5, max_value=5000, step=5)
        max_cycles = st.slider("Max cyklů za den", min_value=1, max_value=5, value=2)
        cycle_cost = st.number_input(
            "Cena cyklování [EUR/MWh]", value=15.0, min_value=0.0, max_value=100.0, step=0.5,
            help="Zahrnuje degradaci baterie + kompenzaci zákazníkovi. Průměr trhu: 12–18 EUR/MWh")
        hold_enabled = st.checkbox(
            "Povolit stav HOLD (drž SoC)", value=False,
            help="Baterie drží aktuální SoC místo nabíjení/vybíjení pokud není jasný cenový signál")

    st.markdown("---")
    commodity = st.radio(
        "Komodita",
        options=["⚡ Elektřina", "🔵 Plyn"],
        horizontal=True,
        key="commodity",
    )
    show_gas = (commodity == "🔵 Plyn")

    st.markdown("---")
    st.markdown("### Zdroje dat")
    if not show_gas:
        st.caption(
            "**ENTSO-E Transparency Platform**  \n"
            "Odchylka · DAP ceny · Generace · Zatížení · Odstávky · Rezervy"
        )
        st.caption(
            "**ČEPS API (SOAP)**  \n"
            "Odchylka real-time · Zatížení · SVR aktivace · Cena odchylky · "
            "Generace · Přeshraniční toky · Frekvence"
        )
        st.caption(
            "**Delta Green API**  \n"
            "Portfolio stav · Disponibilní flexibilita (volitelné)"
        )
    else:
        st.caption(
            "**ENTSO-G Transparency Platform**  \n"
            "Fyzické toky · Hraniční přechody CZ · Denní data"
        )
        st.caption(
            "**GIE AGSI+**  \n"
            "Zásobníky plynu · CZ + EU · Injekce · Těžba · Plnost %"
            "\n*(připraveno, bude přidáno)*"
        )

if auto_refresh:
    st.markdown(
        f'<meta http-equiv="refresh" content="{refresh_min * 60}">',
        unsafe_allow_html=True,
    )

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

with st.spinner("Načítám data rezerv…"):
    try:
        reserves = fetch_reserves()
    except Exception:
        reserves = dict(afrr_d_amt=pd.DataFrame(), afrr_d_pri=pd.DataFrame(),
                        afrr_y_amt=pd.DataFrame(), afrr_y_pri=pd.DataFrame(),
                        mfrr_d_amt=pd.DataFrame(), mfrr_d_pri=pd.DataFrame(),
                        start=now.normalize(), end=now.normalize()+pd.Timedelta(days=10), now=now)

df_act  = fetch_activation_prices()
ws_raw  = fetch_wind_solar_forecast()

last_imbal = float(df_imbal["odchylka_MWh"].iloc[-1]) if not df_imbal.empty else 0.0

if not show_gas:
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
    imbal_col   = C_DEFICIT if last_imbal < -THRESHOLD else (C_SURPLUS if last_imbal > THRESHOLD else C_TEXT)
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

else:
    st.markdown(
        '<div class="banner banner-ok">'
        '<div class="banner-left"><span class="pulse-dot"></span>'
        '<span>🔵 PP DASHBOARD — PLYN</span></div>'
        '<div class="banner-center"></div>'
        '<div class="banner-right">'
        + pd.Timestamp.now(tz="Europe/Prague").strftime("%a %d.%m.%Y · %H:%M:%S") +
        '</div></div>',
        unsafe_allow_html=True,
    )

if not show_gas:
    # ── ZÁLOŽKY ──────────────────────────────────────────────────────
    tab_dash, tab_ceps, tab_out, tab_dap, tab_rezervy, tab_dg, tab_data = st.tabs([
        "📊 Odchylka & Generace",
        "⚡ ČEPS",
        "🔧 Odstávky",
        "💶 DAP Ceny",
        "⚖️ Rezervy",
        "🌿 Delta Green",
        "📋 Data",
    ])

    # ──────────── TAB 1: ODCHYLKA + GENERACE ─────────────────────────
    with tab_dash:
        st.markdown('<div class="section-title">Systémová odchylka + zatížení + cena odchylky — ČEPS</div>',
                    unsafe_allow_html=True)
        df_ceps_imbal, now_ceps = fetch_ceps_imbalance()
        df_ceps_price = fetch_ceps_imbalance_price()
        ceps_d = fetch_ceps_all()
        _load_col = ("Load including pumping [MW]"
                     if "Load including pumping [MW]" in ceps_d["load"].columns
                     else "Load [MW]"
                     if "Load [MW]" in ceps_d["load"].columns
                     else None)
        ceps_load_series = (ceps_d["load"][_load_col]
                            if _load_col else pd.Series(dtype=float))
        st.plotly_chart(
            fig_ceps_combined(df_ceps_imbal, df_ceps_price, ceps_load_series, load_fc, now_ceps),
            use_container_width=True, config={"displayModeBar": False},
        )

        # ── Predikce ACE ─────────────────────────────────────────────
        st.markdown('<div class="section-title">Predikce systémové odchylky — D+1</div>',
                    unsafe_allow_html=True)
        import plotly.graph_objects as go

        df_pred = st.session_state.get("df_pred")
        if df_pred is None:
            df_pred = load_latest_predictions()

        if df_pred is not None and not df_pred.empty:
            fig_pred = go.Figure()

            # Skutečnost posledních 6h
            if not df_ceps_imbal.empty:
                last_6h = df_ceps_imbal[
                    df_ceps_imbal.index >= (now_ceps - pd.Timedelta(hours=6))
                ]
                surplus = last_6h["odchylka_MW"] >= 0
                fig_pred.add_trace(go.Bar(
                    x=last_6h.index[surplus],
                    y=last_6h.loc[surplus, "odchylka_MW"],
                    name="Surplus (skutečnost)",
                    marker_color="#1565C0", opacity=0.7,
                ))
                fig_pred.add_trace(go.Bar(
                    x=last_6h.index[~surplus],
                    y=last_6h.loc[~surplus, "odchylka_MW"],
                    name="Deficit (skutečnost)",
                    marker_color="#C62828", opacity=0.7,
                ))

            # Predikce
            fig_pred.add_trace(prediction_trace(df_pred))
            fig_pred.add_hline(y=0, line_color="#9E9E9E", line_width=0.8)
            fig_pred.add_vline(
                x=now_ceps.isoformat(),
                line_color="#1565C0", line_width=2,
                annotation_text="NOW", annotation_position="top right",
            )
            fig_pred.add_vrect(
                x0=now_ceps.isoformat(),
                x1=df_pred.index.max().isoformat(),
                fillcolor="#F5F5F5", opacity=0.5,
                layer="below", line_width=0,
            )
            fig_pred.update_layout(
                height=320,
                template="plotly_white",
                hovermode="x unified",
                barmode="relative",
                bargap=0.05,
                legend=dict(orientation="h", y=-0.25, x=0, font=dict(size=10)),
                margin=dict(l=60, r=15, t=20, b=60),
                xaxis=dict(type="date", tickformat="%H:%M\n%d.%m", gridcolor="#ECEFF1"),
                yaxis=dict(title_text="MWh / MW", gridcolor="#ECEFF1"),
            )
            st.plotly_chart(fig_pred, use_container_width=True,
                            config={"displayModeBar": False})

            # KPI predikce
            k1, k2, k3 = st.columns(3)
            next_isp = df_pred.iloc[0]["ace_pred_MWh"]
            next_4h  = df_pred[df_pred["h"] <= 16]["ace_pred_MWh"].mean()
            next_24h = df_pred["ace_pred_MWh"].mean()
            k1.metric("Predikce příštích 15min", f"{next_isp:+.0f} MWh")
            k2.metric("Predikce příštích 4h",    f"{next_4h:+.0f} MWh")
            k3.metric("Predikce D+1 průměr",     f"{next_24h:+.0f} MWh")
        else:
            st.info("💡 Spusť predikci tlačítkem vlevo (🔮 Spustit predikci D+1)")

        st.markdown('<div class="section-title">Aktivace SVR v ČR — ČEPS (minutová)</div>',
                    unsafe_allow_html=True)
        df_svr = fetch_ceps_svr()
        st.plotly_chart(fig_ceps_svr(df_svr, now_ceps),
                        use_container_width=True, config={"displayModeBar": False})

        st.markdown('<div class="section-title">Balancing strategie</div>', unsafe_allow_html=True)
        st.info(
            "ℹ️ Data systémové odchylky mají zpoždění ~15 min. "
            "EMA (Exponential Moving Average) dává větší váhu posledním intervalům "
            "a slouží jako proxy pro odhad aktuálního stavu soustavy. "
            "Zákazníci v balancing segmentu pomáhají síti a jsou za to benefitováni."
        )
        st.subheader("⚡ Balancing strategie (EMA predikce)")
        _bc1, _bc2, _bc3 = st.columns(3)
        with _bc1:
            ema_periods = st.slider("EMA okno [ISP]", 1, 8, 4,
                                    help="Počet 5min intervalů pro EMA. 4 = 20 minut.")
        with _bc2:
            threshold_mw = st.slider("Práh zásahu [MWh]", 10, 150, 50,
                                     help="Minimální predikovaná odchylka pro aktivaci signálu. "
                                          "Vyšší = méně zásahů, nižší = agresivnější balancing.")
        with _bc3:
            benefit_eur_mwh = st.number_input("Benefit zákazníka [EUR/MWh]", value=8.0,
                                              help="Kolik EUR/MWh zákazník vydělá za pomoc síti.")

        if not df_ceps_imbal.empty:
            imbal_5min = df_ceps_imbal["odchylka_MW"].resample("5min").mean().dropna()
            _ema, _signal = balancing_strategy_ema(imbal_5min, ema_periods, threshold_mw)
        elif not df_imbal.empty:
            imbal_5min = df_imbal["odchylka_MWh"]
            _ema, _signal = balancing_strategy_ema(imbal_5min, ema_periods, threshold_mw)
        if not df_ceps_imbal.empty or not df_imbal.empty:
            st.plotly_chart(
                fig_balancing_strategy(df_imbal, _ema, _signal, threshold_mw, now),
                use_container_width=True, config={"displayModeBar": False},
            )
            _n_int = int((_signal != "STANDBY").sum())
            _benefit = _n_int * 0.25 * benefit_eur_mwh
            _bm1, _bm2 = st.columns(2)
            _bm1.metric("Počet zásahů dnes", _n_int)
            _bm2.metric("Odhadovaný benefit zákazníka", f"{_benefit:.2f} EUR/den")
        else:
            st.info("Data odchylky nejsou dostupná.")

        st.markdown('<div class="section-title">Ceny aktivace záložních rezerv</div>',
                    unsafe_allow_html=True)
        st.plotly_chart(fig_activation_prices(df_act, now), use_container_width=True,
                        config={"displayModeBar": False})

        st.markdown('<div class="section-title">Zatížení — skutečnost vs. prognóza D+1</div>',
                    unsafe_allow_html=True)
        if load_fc.empty:
            st.info("Data zatížení nejsou dostupná.")
        else:
            st.plotly_chart(
                fig_load(load_fc, ceps_load_series, ceps_d["gen"], now),
                use_container_width=True, config={"displayModeBar": False},
            )

        st.markdown('<div class="section-title">Forecast solární výroby [MW] | D0 + D+1</div>',
                    unsafe_allow_html=True)
        st.plotly_chart(
            fig_wind_solar_forecast(ws_raw, now, gen_raw=gen_raw),
            use_container_width=True, config={"displayModeBar": False},
        )

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

        st.markdown('<div class="section-title">aFRR + mFRR — D0 (objemy a ceny)</div>',
                    unsafe_allow_html=True)
        _d0_start = now.normalize()
        _d0_end   = now.normalize() + pd.Timedelta(days=1)
        rd1, rd2  = st.columns(2)
        with rd1:
            st.plotly_chart(
                fig_reserve_volumes(reserves, now, _d0_start, _d0_end, height=300),
                use_container_width=True, config={"displayModeBar": False},
            )
        with rd2:
            st.plotly_chart(
                fig_reserve_prices(reserves, now, _d0_start, _d0_end, height=300),
                use_container_width=True, config={"displayModeBar": False},
            )

    # ──────────── TAB ČEPS: REAL-TIME DASHBOARD ──────────────────────
    with tab_ceps:
        st.markdown(
            "Zdroj: **ČEPS a.s.** — data jsou anonymní, bez autentizace. "
            "Zpoždění ~1–5 minut. Výroba podle zdroje má granularitu 15 min, "
            "ostatní data jsou minutová."
        )
        with st.spinner("Načítám ČEPS real-time data..."):
            ceps_data = fetch_ceps_all()

        st.plotly_chart(
            fig_ceps_dashboard(ceps_data),
            use_container_width=True,
            config={"displayModeBar": False},
        )

        df_i = ceps_data["imbal"]
        df_f = ceps_data["freq"]
        df_l = ceps_data["load"]
        c1, c2, c3, c4 = st.columns(4)
        if not df_i.empty:
            last_imb = float(df_i.iloc[-1, 0])
            c1.metric("Odchylka", f"{last_imb:+.1f} MW",
                      delta="Surplus" if last_imb >= 0 else "Deficit")
        if not df_f.empty:
            last_hz = float(df_f.iloc[-1, 0])
            c2.metric("Frekvence", f"{last_hz:.3f} Hz",
                      delta=f"{last_hz-50:.3f} Hz")
        if not df_l.empty and "Load [MW]" in df_l.columns:
            last_load = float(df_l["Load [MW]"].iloc[-1])
            c3.metric("Zatížení", f"{last_load:,.0f} MW")
        if not ceps_data["cb"].empty and "Net Export (MW)" in ceps_data["cb"].columns:
            net = float(ceps_data["cb"]["Net Export (MW)"].iloc[-1])
            c4.metric("Net Export", f"{net:+.0f} MW",
                      delta="export" if net >= 0 else "import")

    # ──────────── TAB 2: ODSTÁVKY ─────────────────────────────────────
    with tab_out:
        st.markdown('<div class="section-title">Instalovaná kapacita podle zdroje (14.1.A)</div>',
                    unsafe_allow_html=True)
        cap = fetch_installed_capacity()
        if not cap.empty:
            st.plotly_chart(fig_installed_capacity(cap), use_container_width=True,
                            config={"displayModeBar": False})

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
            rows = [("Base",     stats["base"]),
                    ("Peak 8-20", stats["peak"]),
                    ("Off-peak",  stats["offpeak"]),
                    ("Min",       stats["min"]),
                    ("Max",       stats["max"])]
            st.markdown(f"**{label}**")
            for lbl, val in rows:
                v = f"{val:.2f} EUR" if val is not None else "—"
                st.markdown(f"- {lbl}: **{v}**")

        with c_l:
            _stat_table(calc_dap_stats(s_d0), f"D0 — {now.strftime('%d.%m.%Y')}")
        with c_r:
            _stat_table(calc_dap_stats(s_d1), f"D+1 — {(now+pd.Timedelta(days=1)).strftime('%d.%m.%Y')}")

        st.markdown('<div class="section-title">aFRR + mFRR — D0 + D+1 (objemy a ceny)</div>',
                    unsafe_allow_html=True)
        dap_start = now.normalize()
        dap_end   = now.normalize() + pd.Timedelta(days=2)
        rc1, rc2  = st.columns(2)
        with rc1:
            st.plotly_chart(
                fig_reserve_volumes(reserves, now, dap_start, dap_end, height=320),
                use_container_width=True, config={"displayModeBar": False},
            )
        with rc2:
            st.plotly_chart(
                fig_reserve_prices(reserves, now, dap_start, dap_end, height=320),
                use_container_width=True, config={"displayModeBar": False},
            )

        st.markdown('<div class="section-title">Strategie baterie</div>', unsafe_allow_html=True)
        st.info(
            "ℹ️ Strategie nabíjí baterii při nízkých cenách a vybíjí při vysokých. "
            "Cena cyklování zahrnuje degradaci baterie a kompenzaci zákazníkovi. "
            "Strategie cykluje maximálně N×/den aby chránila životnost baterie."
        )
        _prices_combined = pd.concat([s_d0, s_d1]).sort_index().dropna()
        if not _prices_combined.empty:
            _avg = float(_prices_combined.mean())
            _low = _avg - cycle_cost / 2
            _hig = _avg + cycle_cost / 2
            _df_sim, _cycles_done = simulate_battery_dap(
                _prices_combined, bat_capacity_kwh, bat_power_kw,
                max_cycles, cycle_cost, hold_enabled,
            )
            st.plotly_chart(
                fig_battery_strategy(_df_sim, _low, _hig, _avg, now),
                use_container_width=True, config={"displayModeBar": False},
            )
            _total_rev = float(_df_sim["revenue_eur"].sum())
            m1, m2, m3 = st.columns(3)
            m1.metric("Celkový výnos D0+D+1", f"{_total_rev:.2f} EUR")
            m2.metric("Počet cyklů", f"{_cycles_done:.1f} / {max_cycles}")
            m3.metric("Výnos vs. bez strategie", f"{_total_rev:+.2f} EUR",
                      help="Porovnání s pasivní strategií (baterie nečinná)")
        else:
            st.info("DAP data nejsou dostupná pro simulaci.")

    # ──────────── TAB 4: REZERVY ─────────────────────────────────────
    with tab_rezervy:
        res_start = now.normalize()
        res_end   = now.normalize() + pd.Timedelta(days=7)
        if now.month < 7:
            _a04_label = f"{now.year}-01-01 – {now.year}-07-01"
        else:
            _a04_label = f"{now.year}-07-01 – {now.year + 1}-01-01"

        st.markdown(
            '<div class="section-title">'
            f'aFRR + mFRR — D0 až D+7 &nbsp;·&nbsp; '
            f'Solid = A01 denní &nbsp;·&nbsp; Dash = A04 roční ({_a04_label})'
            '</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            fig_reserve_volumes(reserves, now, res_start, res_end, height=420),
            use_container_width=True, config={"displayModeBar": False},
        )
        st.plotly_chart(
            fig_reserve_prices(reserves, now, res_start, res_end, height=420),
            use_container_width=True, config={"displayModeBar": False},
        )

        with st.expander("📥 Stáhnout surová data rezerv"):
            ec1, ec2, ec3, ec4, ec5, ec6 = st.columns(6)
            for col_obj, df_r, label, fname in [
                (ec1, reserves["afrr_d_amt"], "aFRR denní obj.", "afrr_d_amount.csv"),
                (ec2, reserves["afrr_d_pri"], "aFRR denní ceny", "afrr_d_price.csv"),
                (ec3, reserves["afrr_y_amt"], "aFRR roční obj.", "afrr_y_amount.csv"),
                (ec4, reserves["afrr_y_pri"], "aFRR roční ceny", "afrr_y_price.csv"),
                (ec5, reserves["mfrr_d_amt"], "mFRR denní obj.", "mfrr_d_amount.csv"),
                (ec6, reserves["mfrr_d_pri"], "mFRR denní ceny", "mfrr_d_price.csv"),
            ]:
                with col_obj:
                    if not df_r.empty:
                        st.download_button(f"⬇ {label}", df_r.to_csv().encode(), fname, "text/csv")
                    else:
                        st.caption(f"{label}: —")

    # ──────────── TAB 5: DELTA GREEN ─────────────────────────────────
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

    # ──────────── TAB 6: SUROVÁ DATA ─────────────────────────────────
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
                from config import psr_lookup as _psr_lookup
                display_gen = gen_raw.copy()
                display_gen.columns = [_psr_lookup(c)[0] for c in display_gen.columns]
                st.dataframe(display_gen.iloc[::-1], use_container_width=True)
                st.download_button("⬇ CSV generace", display_gen.to_csv().encode(),
                                   "generace.csv", "text/csv")
            else:
                st.info("Data generace nejsou dostupná.")

st.session_state.iteration += 1

if show_gas:
    st.markdown("---")
    st.markdown("## 🔵 Fyzické toky plynu — CZ")

    with st.spinner("Načítám data ENTSO-G..."):
        pivot_gas = fetch_entsog_flows(days=90)

    if pivot_gas.empty:
        st.warning("ENTSO-G data nejsou dostupná.")
    else:
        tab_map, tab_bar, tab_season, tab_hist = st.tabs(
            ["🗺️ Mapa", "📊 Toky", "📈 Sezonnost", "📈 Historie"]
        )

        with tab_map:
            with st.spinner("Načítám historická data..."):
                df_hist_map = load_entsog_history()
            if df_hist_map.empty:
                st.warning("Data nejsou dostupná.")
            else:
                st.plotly_chart(
                    fig_gas_map(df_hist_map),
                    use_container_width=True,
                    config={"displayModeBar": False},
                )

        with tab_bar:
            df_hist = load_entsog_history()
            if df_hist.empty:
                st.warning("Historická data ENTSO-G nejsou dostupná.")
            else:
                df_hist["date"] = pd.to_datetime(df_hist["date"], utc=True)
                all_countries = sorted(df_hist["countryLabel"].dropna().unique())
                sel_countries = st.multiselect("🌍 Země", all_countries,
                                               default=["Czechia"], key="gas_countries")
                df_f1 = df_hist[df_hist["countryLabel"].isin(sel_countries)] \
                        if sel_countries else df_hist
                all_directions = sorted(df_f1["directionKey"].dropna().unique())
                sel_directions = st.multiselect("↕ Směr", all_directions,
                                                default=all_directions, key="gas_directions")
                df_f2 = df_f1[df_f1["directionKey"].isin(sel_directions)] \
                        if sel_directions else df_f1
                all_systems = sorted(df_f2["adjacentSystemsKey"].dropna().unique())
                sel_systems = st.multiselect("🔧 Systém", all_systems, default=[],
                                             key="gas_systems", help="Prázdný = všechny")
                df_f3 = df_f2[df_f2["adjacentSystemsKey"].isin(sel_systems)] \
                        if sel_systems else df_f2
                all_points = sorted(df_f3["pointsNames"].dropna().unique())
                sel_points = st.multiselect("📍 Bod", all_points, default=[],
                                            key="gas_points", help="Prázdný = všechny")
                df_f4 = df_f3[df_f3["pointsNames"].isin(sel_points)] \
                        if sel_points else df_f3
                col_ct, col_qd = st.columns([1, 2])
                with col_ct:
                    chart_type = st.radio("Typ grafu", ["Linie", "Plocha", "Sloupcový"],
                                          horizontal=True, key="gas_chart_type")
                with col_qd:
                    date_range = st.date_input(
                        "📆 Rozsah (časová osa)",
                        value=(df_hist["date"].dt.tz_localize(None).max() - pd.Timedelta(days=365),
                               df_hist["date"].dt.tz_localize(None).max()),
                        key="gas_daterange",
                    )
                    st.markdown("**Rychlý výběr období:**")
                    qd_cols = st.columns(6)
                    labels  = ["Týden","Měsíc","Kvartál","Půlrok","Rok","Maximum"]
                    deltas  = [7, 30, 90, 182, 365, None]
                    max_date = df_hist["date"].dt.tz_localize(None).max()
                    for i, (lbl, delta) in enumerate(zip(labels, deltas)):
                        if qd_cols[i].button(lbl, key=f"qd_{lbl}"):
                            if delta:
                                st.session_state["gas_daterange"] = (
                                    (max_date - pd.Timedelta(days=delta)).date(), max_date.date())
                            else:
                                min_date = df_hist["date"].dt.tz_localize(None).min()
                                st.session_state["gas_daterange"] = (min_date.date(), max_date.date())
                            st.rerun()
                st.markdown("---")
                if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
                    ts_from  = pd.Timestamp(date_range[0]).tz_localize("UTC")
                    ts_to    = pd.Timestamp(date_range[1]).tz_localize("UTC")
                    df_range = df_f4[(df_f4["date"] >= ts_from) & (df_f4["date"] <= ts_to)]
                else:
                    df_range = df_f4
                st.plotly_chart(fig_flow_timeseries(df_range, [], [], [], [], chart_type),
                                use_container_width=True)

        with tab_season:
            df_hist_s = load_entsog_history()
            if df_hist_s.empty:
                st.warning("Data nejsou dostupná.")
            else:
                df_hist_s["date"] = pd.to_datetime(df_hist_s["date"], utc=True)
                all_countries_s = sorted(df_hist_s["countryLabel"].dropna().unique())
                sel_countries_s = st.multiselect("🌍 Země", all_countries_s,
                                                  default=["Czechia"], key="seas_countries")
                df_s1 = df_hist_s[df_hist_s["countryLabel"].isin(sel_countries_s)] \
                        if sel_countries_s else df_hist_s
                all_dir_s = sorted(df_s1["directionKey"].dropna().unique())
                sel_dir_s = st.multiselect("↕ Směr", all_dir_s, default=all_dir_s, key="seas_dir")
                df_s2 = df_s1[df_s1["directionKey"].isin(sel_dir_s)] if sel_dir_s else df_s1
                all_sys_s = sorted(df_s2["adjacentSystemsKey"].dropna().unique())
                sel_sys_s = st.multiselect("🔧 Systém", all_sys_s, default=[], key="seas_sys",
                                           help="Prázdný = všechny")
                df_s3 = df_s2[df_s2["adjacentSystemsKey"].isin(sel_sys_s)] if sel_sys_s else df_s2
                all_pts_s = sorted(df_s3["pointsNames"].dropna().unique())
                sel_pts_s = st.multiselect("📍 Bod", all_pts_s, default=[], key="seas_pts",
                                           help="Prázdný = všechny")
                df_s4 = df_s3[df_s3["pointsNames"].isin(sel_pts_s)] if sel_pts_s else df_s3
                all_years_s = sorted(df_hist_s["date"].dt.year.unique())
                sel_years_s = st.multiselect("📅 Roky", all_years_s,
                                              default=all_years_s[-5:], key="seas_years")
                chart_type_s = st.radio("Typ grafu", ["Linie", "Plocha", "Sloupcový"],
                                         horizontal=True, key="seas_chart_type")
                st.markdown("---")
                st.plotly_chart(
                    fig_flow_seasonality(df_s4, [], [], [], [], sel_years_s, chart_type_s),
                    use_container_width=True)

        with tab_hist:
            point_sel = st.selectbox(
                "Hraniční přechod",
                options=[c for c in pivot_gas.columns if pivot_gas[c].abs().sum() > 0],
                key="gas_point_sel",
            )
            st.plotly_chart(fig_gas_point_history(pivot_gas, point_sel),
                            use_container_width=True)
