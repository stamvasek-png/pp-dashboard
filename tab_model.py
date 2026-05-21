# ══════════════════════════════════════════════════════════════════
# tab_model.py  —  vložit do app.py jako nová záložka za tab_data
# ══════════════════════════════════════════════════════════════════
#
# POUŽITÍ v app.py:
#
#   1) Přidej do tabs:
#      tab_dash, tab_ceps, tab_out, tab_dap, tab_rezervy, tab_dg, tab_data, tab_model = st.tabs([
#          "📊 Odchylka & Generace", "⚡ ČEPS", "🔧 Odstávky",
#          "💶 DAP Ceny", "⚖️ Rezervy", "🌿 Delta Green", "📋 Data", "🤖 Model",
#      ])
#
#   2) Přidej blok with tab_model: (viz níže)
#
# ══════════════════════════════════════════════════════════════════

# ── Vlož tento blok do app.py za with tab_data: ─────────────────

"""
    with tab_model:
        from tab_model import render_model_tab
        render_model_tab(ceps, client)
"""

# ── Samotný modul ────────────────────────────────────────────────

import pandas as pd
import numpy as np
import streamlit as st
from zeep import Client as SoapClient

CEPS_NS = "https://www.ceps.cz/CepsData/StructuredData/1.0"
TZ      = "Europe/Prague"


# ── Pomocné parsery ──────────────────────────────────────────────

def _tz_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TZ)
    else:
        df.index = df.index.tz_convert(TZ)
    return df


def _parse_ceps(result, rename_map: dict | None = None) -> pd.DataFrame:
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        a = item.attrib
        try:
            ts = pd.Timestamp(a["date"])
            ts = ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)
        except Exception:
            continue
        row = {"time": ts}
        for k, v in a.items():
            if k.startswith("value") and k != "value15":
                try:
                    row[k] = float(v)
                except (ValueError, TypeError):
                    pass
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("time").sort_index()
    if rename_map:
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    return df


# ── Fetch funkce ─────────────────────────────────────────────────

def _fetch_ace(ceps_client, now: pd.Timestamp) -> pd.DataFrame:
    start = now - pd.Timedelta(days=7)
    try:
        r = ceps_client.service.AktualniSystemovaOdchylkaCR(
            dateFrom=start.replace(tzinfo=None),
            dateTo=now.replace(tzinfo=None),
            agregation="QH",
            function="AVG",
        )
        df = _parse_ceps(r, {"value1": "ace_MWh"})
        df["ace_MW"] = df["ace_MWh"] * 4
        return df
    except Exception as e:
        st.warning(f"ACE: {e}")
        return pd.DataFrame()


def _fetch_svr(ceps_client, now: pd.Timestamp) -> pd.DataFrame:
    start = now - pd.Timedelta(days=2)
    try:
        r = ceps_client.service.AktivaceSVRvCR(
            dateFrom=start.replace(tzinfo=None),
            dateTo=now.replace(tzinfo=None),
            agregation="QH",
            function="AVG",
            param1="all",
        )
        return _parse_ceps(r, {
            "value1": "aFRR_up_MW", "value2": "aFRR_dn_MW",
            "value3": "mFRR_up_MW", "value4": "mFRR_dn_MW",
            "value7": "mFRR5_MW",
        })
    except Exception as e:
        st.warning(f"SVR: {e}")
        return pd.DataFrame()


def _fetch_load(ceps_client, now: pd.Timestamp) -> pd.DataFrame:
    start = now - pd.Timedelta(days=2)
    try:
        r = ceps_client.service.Load(
            dateFrom=start.replace(tzinfo=None),
            dateTo=now.replace(tzinfo=None),
            agregation="QH",
            function="AVG",
            version="RT",
        )
        return _parse_ceps(r, {
            "value1": "load_actual_pumping_MW",
            "value2": "load_actual_MW",
        })
    except Exception as e:
        st.warning(f"Load: {e}")
        return pd.DataFrame()


def _fetch_dap(entsoe_client, now: pd.Timestamp) -> pd.DataFrame:
    today    = now.normalize()
    tomorrow = today + pd.Timedelta(days=1)
    frames   = []
    for day in [today, tomorrow]:
        try:
            raw = entsoe_client.query_day_ahead_prices(
                "CZ", start=day, end=day + pd.Timedelta(days=1)
            )
            if raw is not None and not raw.empty:
                raw = (_tz_df(raw.to_frame()) if isinstance(raw, pd.Series)
                       else _tz_df(raw))
                raw = raw.resample("15min").last().ffill()
                raw.columns = ["dap_EUR_MWh"]
                frames.append(raw)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _fetch_load_fc(entsoe_client, now: pd.Timestamp) -> pd.DataFrame:
    today = now.normalize()
    end   = today + pd.Timedelta(days=2)
    try:
        raw = entsoe_client.query_load_forecast("CZ", start=today, end=end)
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return pd.DataFrame()
        df = _tz_df(raw.to_frame() if isinstance(raw, pd.Series) else raw)
        df = df.resample("15min").last().ffill()
        df.columns = ["load_fc_MW"]
        return df
    except Exception as e:
        st.warning(f"Load fc: {e}")
        return pd.DataFrame()


def _fetch_wind_solar(entsoe_client, now: pd.Timestamp) -> pd.DataFrame:
    today = now.normalize()
    end   = today + pd.Timedelta(days=2)
    try:
        raw = entsoe_client.query_wind_and_solar_forecast(
            "CZ", start=today, end=end, psr_type=None
        )
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return pd.DataFrame()
        df = _tz_df(raw)
        if isinstance(df.columns, pd.MultiIndex):
            lvls = df.columns.get_level_values(1)
            df = (df.xs("Actual Aggregated", level=1, axis=1)
                  if "Actual Aggregated" in lvls
                  else df.xs(lvls[0], level=1, axis=1))
        df = df.resample("15min").last().ffill()
        df.columns = [f"fc_{c.lower().replace(' ', '_')}_MW" for c in df.columns]
        return df
    except Exception as e:
        st.warning(f"Wind/solar: {e}")
        return pd.DataFrame()


# ── Hlavní render funkce ─────────────────────────────────────────

def render_model_tab(ceps_client, entsoe_client):
    """Vykreslí záložku Model — stahování a zobrazení vstupních dat pro predikci."""

    st.markdown(
        '<div class="section-title">🤖 Vstupní data predikčního modelu ACE</div>',
        unsafe_allow_html=True,
    )
    st.info(
        "Tato záložka stahuje a zobrazuje všechna data, která model XGBoost "
        "potřebuje pro predikci systémové odchylky ACE CZ na +15 až +120 minut. "
        "Data jsou seřazena sestupně — nejnovější nahoře."
    )

    if st.button("⬇️ Stáhnout data pro model", type="primary", use_container_width=True):
        now = pd.Timestamp.now(tz=TZ)

        sources: dict[str, pd.DataFrame] = {}
        errors:  dict[str, str]          = {}

        with st.spinner("Stahuji ACE (7 dní)..."):
            df = _fetch_ace(ceps_client, now)
            sources["ACE"] = df
            if df.empty:
                errors["ACE"] = "prázdná odpověď"

        with st.spinner("Stahuji SVR aktivace..."):
            df = _fetch_svr(ceps_client, now)
            sources["SVR"] = df
            if df.empty:
                errors["SVR"] = "prázdná odpověď"

        with st.spinner("Stahuji zatížení RT..."):
            df = _fetch_load(ceps_client, now)
            sources["Zatížení RT"] = df
            if df.empty:
                errors["Zatížení RT"] = "prázdná odpověď"

        with st.spinner("Stahuji DAP ceny..."):
            df = _fetch_dap(entsoe_client, now)
            sources["DAP ceny"] = df
            if df.empty:
                errors["DAP ceny"] = "prázdná odpověď"

        with st.spinner("Stahuji load forecast..."):
            df = _fetch_load_fc(entsoe_client, now)
            sources["Load forecast"] = df
            if df.empty:
                errors["Load forecast"] = "prázdná odpověď"

        with st.spinner("Stahuji wind/solar forecast..."):
            df = _fetch_wind_solar(entsoe_client, now)
            sources["Wind/Solar fc"] = df
            if df.empty:
                errors["Wind/Solar fc"] = "prázdná odpověď"

        st.session_state["model_data"]  = sources
        st.session_state["model_now"]   = now
        st.session_state["model_errors"] = errors
        st.success("✅ Data stažena — zobrazuji níže")

    # ── Zobrazení dat ────────────────────────────────────────────
    if "model_data" not in st.session_state:
        st.caption("Klikni na tlačítko výše pro stažení dat.")
        return

    sources: dict[str, pd.DataFrame] = st.session_state["model_data"]
    now_ts:  pd.Timestamp             = st.session_state["model_now"]
    errors:  dict[str, str]           = st.session_state.get("model_errors", {})

    st.markdown(f"**Staženo:** {now_ts.strftime('%d.%m.%Y %H:%M:%S')}")

    # ── Status každého zdroje ────────────────────────────────────
    st.markdown("#### 📡 Stav zdrojů dat")

    SOURCE_META = {
        "ACE": {
            "popis":   "Systémová odchylka CZ [MWh/15min]",
            "zdroj":   "ČEPS SOAP",
            "cols":    ["ace_MWh", "ace_MW"],
            "klic":    True,
        },
        "SVR": {
            "popis":   "SVR aktivace — aFRR/mFRR [MW]",
            "zdroj":   "ČEPS SOAP",
            "cols":    ["aFRR_up_MW", "aFRR_dn_MW", "mFRR_up_MW", "mFRR_dn_MW", "mFRR5_MW"],
            "klic":    False,
        },
        "Zatížení RT": {
            "popis":   "Zatížení soustavy RT [MW]",
            "zdroj":   "ČEPS SOAP",
            "cols":    ["load_actual_pumping_MW", "load_actual_MW"],
            "klic":    False,
        },
        "DAP ceny": {
            "popis":   "Day-Ahead Price CZ [EUR/MWh]",
            "zdroj":   "ENTSO-E",
            "cols":    ["dap_EUR_MWh"],
            "klic":    False,
        },
        "Load forecast": {
            "popis":   "Load forecast D+1 [MW]",
            "zdroj":   "ENTSO-E",
            "cols":    ["load_fc_MW"],
            "klic":    False,
        },
        "Wind/Solar fc": {
            "popis":   "Wind + solar forecast D+1 [MW]",
            "zdroj":   "ENTSO-E",
            "cols":    [],
            "klic":    False,
        },
    }

    status_rows = []
    for name, meta in SOURCE_META.items():
        df = sources.get(name, pd.DataFrame())
        if df.empty:
            status_rows.append({
                "Zdroj":      name,
                "Popis":      meta["popis"],
                "API":        meta["zdroj"],
                "Řádků":      0,
                "Poslední data": "—",
                "Zpoždění":   "—",
                "Stav":       "❌ chyba" + (f": {errors[name]}" if name in errors else ""),
            })
        else:
            last_ts   = df.index.max()
            lag_min   = int((now_ts - last_ts).total_seconds() / 60)
            lag_str   = f"{lag_min} min" if lag_min >= 0 else "—"
            stav      = "✅ OK" if lag_min <= 20 else ("⚠️ zpoždění" if lag_min <= 60 else "🔴 staré")
            if meta["klic"]:
                stav = stav + " ⭐"
            status_rows.append({
                "Zdroj":         name,
                "Popis":         meta["popis"],
                "API":           meta["zdroj"],
                "Řádků":         len(df),
                "Poslední data": last_ts.strftime("%d.%m %H:%M"),
                "Zpoždění":      lag_str,
                "Stav":          stav,
            })

    st.dataframe(
        pd.DataFrame(status_rows),
        use_container_width=True,
        hide_index=True,
    )

    # ── Detailní tabulky ─────────────────────────────────────────
    st.markdown("#### 📋 Data podle zdroje")

    tabs_list  = list(SOURCE_META.keys())
    inner_tabs = st.tabs(tabs_list)

    for tab_obj, name in zip(inner_tabs, tabs_list):
        with tab_obj:
            df = sources.get(name, pd.DataFrame())
            meta = SOURCE_META[name]

            if df.empty:
                st.warning(f"Žádná data pro **{name}**.")
                continue

            # Zobraz sloupce relevantní pro model (nebo vše)
            show_cols = [c for c in meta["cols"] if c in df.columns]
            if not show_cols:
                show_cols = df.columns.tolist()

            df_show = df[show_cols].copy() if show_cols else df.copy()

            # Seřadit sestupně (nejnovější nahoře)
            df_show = df_show.sort_index(ascending=False)

            # Formátuj index jako string
            df_display = df_show.copy()
            df_display.index = df_display.index.strftime("%d.%m.%Y %H:%M")
            df_display.index.name = "Čas"

            # KPI řádek
            last_row = df_show.iloc[0]
            kpi_cols = st.columns(min(len(show_cols), 4))
            for i, col in enumerate(show_cols[:4]):
                val = last_row.get(col, np.nan)
                label = col.replace("_", " ")
                if pd.notna(val):
                    kpi_cols[i].metric(label, f"{val:+.1f}" if "MW" in col else f"{val:.2f}")
                else:
                    kpi_cols[i].metric(label, "—")

            st.dataframe(df_display, use_container_width=True, height=400)

            # Download tlačítko
            csv = df_display.to_csv()
            st.download_button(
                f"⬇ Stáhnout {name} jako CSV",
                data=csv,
                file_name=f"model_{name.lower().replace('/', '_').replace(' ', '_')}.csv",
                mime="text/csv",
            )

    # ── Sloučená tabulka všech dat ───────────────────────────────
    st.markdown("#### 🔗 Sloučená tabulka všech featur")
    st.caption(
        "Zarovnání všech zdrojů na společný 15min grid. "
        "NaN = data v daném ISP nejsou dostupná."
    )

    dfs_to_merge = []
    for name, df in sources.items():
        if not df.empty:
            dfs_to_merge.append(df)

    if dfs_to_merge:
        merged = pd.concat(dfs_to_merge, axis=1)
        merged = merged[~merged.index.duplicated(keep="last")].sort_index(ascending=False)
        merged_display = merged.copy()
        merged_display.index = merged_display.index.strftime("%d.%m.%Y %H:%M")
        merged_display.index.name = "Čas"

        st.dataframe(merged_display, use_container_width=True, height=450)

        csv_merged = merged_display.to_csv()
        st.download_button(
            "⬇ Stáhnout sloučenou tabulku jako CSV",
            data=csv_merged,
            file_name="model_features_merged.csv",
            mime="text/csv",
        )

        nan_pct = (merged.isna().mean() * 100).round(1)
        st.markdown("**Pokrytí dat (% NaN):**")
        nan_df = nan_pct.reset_index()
        nan_df.columns = ["Featura", "NaN %"]
        nan_df["Pokrytí %"] = (100 - nan_df["NaN %"]).round(1)
        st.dataframe(nan_df, use_container_width=True, hide_index=True)
