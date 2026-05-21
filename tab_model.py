# ══════════════════════════════════════════════════════════════════
# tab_model.py  —  záložka Model v PP Dashboardu
# ══════════════════════════════════════════════════════════════════

import os
import logging
import numpy as np
import pandas as pd
import streamlit as st

try:
    import joblib
    _JOBLIB_OK = True
except ImportError:
    _JOBLIB_OK = False

log = logging.getLogger(__name__)

CEPS_NS  = "https://www.ceps.cz/CepsData/StructuredData/1.0"
TZ       = "Europe/Prague"
_HERE    = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(_HERE, "data", "models", "xgb_short_term.joblib")
# Fallback — pokud model ještě není přejmenován
if not os.path.exists(MODEL_PATH):
    _alt = os.path.join(_HERE, "data", "models", "xgb_baseline_slim.joblib")
    if os.path.exists(_alt):
        MODEL_PATH = _alt

KEY_HORIZONS = [1, 2, 3, 4, 5, 6, 7, 8]   # +15 až +120 min


# ══════════════════════════════════════════════════════════════════
# MODEL
# ══════════════════════════════════════════════════════════════════

@st.cache_resource
def load_model():
    if not _JOBLIB_OK:
        st.error("joblib není nainstalován — přidej do requirements.txt")
        return None, None, None
    if not os.path.exists(MODEL_PATH):
        return None, None, None
    data = joblib.load(MODEL_PATH)
    return data["models"], data["feature_cols"], data.get("key_horizons", KEY_HORIZONS)


# ══════════════════════════════════════════════════════════════════
# POMOCNÉ FUNKCE
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# FETCH FUNKCE
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ══════════════════════════════════════════════════════════════════

def build_features(df: pd.DataFrame, feature_cols: list) -> pd.DataFrame:
    idx = df.index

    df["hour"]       = idx.hour
    df["minute"]     = idx.minute
    df["isp"]        = idx.hour * 4 + idx.minute // 15
    df["dow"]        = idx.dayofweek
    df["month"]      = idx.month
    df["quarter"]    = idx.quarter
    df["is_weekend"] = (idx.dayofweek >= 5).astype(int)
    df["is_peak"]    = ((idx.hour >= 8) & (idx.hour < 20)).astype(int)
    df["hour_sin"]   = np.sin(2 * np.pi * df["isp"] / 96)
    df["hour_cos"]   = np.cos(2 * np.pi * df["isp"] / 96)
    df["dow_sin"]    = np.sin(2 * np.pi * df["dow"] / 7)
    df["dow_cos"]    = np.cos(2 * np.pi * df["dow"] / 7)
    df["month_sin"]  = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"]  = np.cos(2 * np.pi * df["month"] / 12)
    df["is_dst"]     = pd.Series(idx).apply(
        lambda x: int(x.dst().seconds > 0)
    ).values
    HOLIDAYS = {(1,1),(5,1),(5,8),(7,5),(7,6),(9,28),(10,28),(11,17),
                (12,24),(12,25),(12,26)}
    df["is_holiday"] = [(m, d) in HOLIDAYS for m, d in zip(idx.month, idx.day)]
    df["is_holiday"] = df["is_holiday"].astype(int)
    df["is_monday"]  = (idx.dayofweek == 0).astype(int)
    df["is_friday"]  = (idx.dayofweek == 4).astype(int)

    ace = df["ace_MWh"]
    for lag in [1, 2, 3, 4, 8, 12, 16, 24, 48, 96, 192, 288, 672]:
        df[f"ace_lag_{lag}"] = ace.shift(lag)
    for window in [4, 8, 16, 32, 96]:
        df[f"ace_roll_mean_{window}"] = ace.shift(1).rolling(window).mean()
        df[f"ace_roll_std_{window}"]  = ace.shift(1).rolling(window).std()
    df["ace_daily_mean"] = ace.shift(1).rolling(96).mean()

    if "dap_EUR_MWh" in df.columns:
        df["dap_daily_spread_EUR"] = df["dap_EUR_MWh"].resample("D").transform(
            lambda x: x.max() - x.min()
        )
    if "load_actual_MW" in df.columns and "load_fc_MW" in df.columns:
        df["load_fc_error_MW"] = df["load_actual_MW"] - df["load_fc_MW"]
    else:
        df["load_fc_error_MW"] = np.nan

    # Zarovnej na feature_cols modelu
    for col in feature_cols:
        if col not in df.columns and col != "ace_MWh":
            df[col] = np.nan
    extra = [c for c in df.columns if c not in feature_cols and c != "ace_MWh"]
    df = df.drop(columns=extra, errors="ignore")
    return df


# ══════════════════════════════════════════════════════════════════
# PREDIKCE
# ══════════════════════════════════════════════════════════════════

def run_prediction_from_data(sources: dict) -> pd.DataFrame | None:
    """Spustí predikci nad již stažennými daty (z session_state)."""
    models, feature_cols, horizons = load_model()
    if models is None:
        st.error(f"Model nenalezen: {MODEL_PATH}")
        return None

    # Sestav DataFrame ze zdrojů
    frames = []
    for name, df in sources.items():
        if not df.empty:
            # Ponech jen numerické sloupce, zahoď ace_MW (duplikát)
            num = df.select_dtypes(include="number")
            if "ace_MW" in num.columns:
                num = num.drop(columns=["ace_MW"])
            frames.append(num)

    if not frames:
        st.error("Žádná data pro predikci.")
        return None

    df_all = pd.concat(frames, axis=1)
    df_all = df_all[~df_all.index.duplicated(keep="last")].sort_index()

    if "ace_MWh" not in df_all.columns:
        st.error("Chybí ACE data.")
        return None

    # Feature engineering
    df_feat = build_features(df_all.copy(), feature_cols)

    # Nejnovější řádek s platným lag_1 a lag_96
    valid = df_feat.dropna(subset=["ace_lag_1", "ace_lag_96"])
    if valid.empty:
        st.error("Nedostatek historických dat pro výpočet lagů (potřeba 7 dní).")
        return None

    t_now = valid.index.max()
    X_now = valid.loc[[t_now], feature_cols].values

    # Predikce h=1..8 (+15 až +120 min)
    predictions = {}
    for h in KEY_HORIZONS:
        if h not in models:
            continue
        try:
            predictions[h] = float(models[h].predict(X_now)[0])
        except Exception as e:
            log.warning(f"h={h}: {e}")
            predictions[h] = np.nan

    if not predictions:
        st.error("Žádná predikce — modely nefungují.")
        return None

    rows = []
    for h, pred_mwh in predictions.items():
        ts = t_now + pd.Timedelta(minutes=15 * h)
        rows.append({
            "timestamp":       ts,
            "ace_pred_MWh":    pred_mwh,
            "ace_pred_MW":     pred_mwh * 4,
            "h":               h,
            "predicted_from":  t_now,
        })

    df_pred = pd.DataFrame(rows).set_index("timestamp")
    if df_pred.index.tz is None:
        df_pred.index = df_pred.index.tz_localize(TZ)

    # Ulož do session_state pro záložku Odchylka & Generace
    st.session_state["df_pred_model"] = df_pred
    st.session_state["df_pred_model_time"] = pd.Timestamp.now(tz=TZ)

    return df_pred


# ══════════════════════════════════════════════════════════════════
# PLOTLY TRACE — pro záložku Odchylka & Generace
# ══════════════════════════════════════════════════════════════════

def get_prediction_trace():
    """Vrátí plotly trace predikce pro vložení do fig_ceps_combined."""
    import plotly.graph_objects as go

    df_pred = st.session_state.get("df_pred_model")
    if df_pred is None or df_pred.empty:
        return None

    return go.Scatter(
        x=df_pred.index,
        y=df_pred["ace_pred_MWh"],
        mode="lines+markers",
        name="Predikce XGBoost (+15–120 min)",
        line=dict(color="#7B1FA2", width=2.5, dash="dot"),
        marker=dict(color="#7B1FA2", size=7, symbol="circle"),
        hovertemplate=(
            "<b>Predikce ACE</b><br>"
            "%{x|%H:%M}<br>"
            "<b>%{y:+.1f} MWh</b>"
            "<extra></extra>"
        ),
        showlegend=True,
    )


# ══════════════════════════════════════════════════════════════════
# HLAVNÍ RENDER FUNKCE
# ══════════════════════════════════════════════════════════════════

def render_model_tab(ceps_client, entsoe_client):
    st.markdown(
        '<div class="section-title">🤖 Vstupní data predikčního modelu ACE — XGBoost short-term</div>',
        unsafe_allow_html=True,
    )
    st.info(
        "**Krok 1:** Stáhni vstupní data.  \n"
        "**Krok 2:** Spusť predikci (+15 až +120 min).  \n"
        "Výsledek se zobrazí zde i v záložce **📊 Odchylka & Generace**."
    )

    # ── KROK 1: Stažení dat ──────────────────────────────────────
    col_btn1, col_btn2 = st.columns([1, 1])

    with col_btn1:
        fetch_clicked = st.button(
            "⬇️ Krok 1 — Stáhnout data pro model",
            type="primary",
            use_container_width=True,
        )

    if fetch_clicked:
        now = pd.Timestamp.now(tz=TZ)
        sources: dict[str, pd.DataFrame] = {}
        errors:  dict[str, str]          = {}

        with st.spinner("Stahuji ACE (7 dní QH)..."):
            df = _fetch_ace(ceps_client, now)
            sources["ACE"] = df
            if df.empty: errors["ACE"] = "prázdná odpověď"

        with st.spinner("Stahuji SVR aktivace..."):
            df = _fetch_svr(ceps_client, now)
            sources["SVR"] = df
            if df.empty: errors["SVR"] = "prázdná odpověď"

        with st.spinner("Stahuji zatížení RT..."):
            df = _fetch_load(ceps_client, now)
            sources["Zatížení RT"] = df
            if df.empty: errors["Zatížení RT"] = "prázdná odpověď"

        with st.spinner("Stahuji DAP ceny..."):
            df = _fetch_dap(entsoe_client, now)
            sources["DAP ceny"] = df
            if df.empty: errors["DAP ceny"] = "prázdná odpověď"

        with st.spinner("Stahuji load forecast..."):
            df = _fetch_load_fc(entsoe_client, now)
            sources["Load forecast"] = df
            if df.empty: errors["Load forecast"] = "prázdná odpověď"

        with st.spinner("Stahuji wind/solar forecast..."):
            df = _fetch_wind_solar(entsoe_client, now)
            sources["Wind/Solar fc"] = df
            if df.empty: errors["Wind/Solar fc"] = "prázdná odpověď"

        st.session_state["model_data"]   = sources
        st.session_state["model_now"]    = now
        st.session_state["model_errors"] = errors
        # Invaliduj starou predikci
        st.session_state.pop("df_pred_model", None)
        st.success("✅ Data stažena")

    # ── KROK 2: Predikce ────────────────────────────────────────
    _data_ready = "model_data" in st.session_state
    with col_btn2:
        predict_clicked = st.button(
            "🔮 Krok 2 — Spustit predikci (+15–120 min)",
            type="secondary",
            use_container_width=True,
            disabled=not _data_ready,
        )

    if predict_clicked:
        sources = st.session_state.get("model_data", {})
        models, _, _ = load_model()
        if models is None:
            st.error(f"Model nenalezen na cestě: `{MODEL_PATH}`")
        else:
            with st.spinner("Počítám predikci..."):
                df_pred = run_prediction_from_data(sources)
            if df_pred is not None:
                st.success(
                    f"✅ Predikce OK — predikováno z "
                    f"{df_pred['predicted_from'].iloc[0].strftime('%H:%M')} "
                    f"→ {df_pred.index.max().strftime('%H:%M')}"
                )

    # ── Zobraz výsledek predikce ─────────────────────────────────
    df_pred = st.session_state.get("df_pred_model")
    if df_pred is not None and not df_pred.empty:
        st.markdown("#### 📈 Výsledek predikce")

        pred_time = st.session_state.get("df_pred_model_time")
        if pred_time:
            st.caption(f"Predikce spuštěna: {pred_time.strftime('%H:%M:%S')}")

        # KPI
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("+15 min", f"{df_pred['ace_pred_MWh'].iloc[0]:+.1f} MWh")
        k2.metric("+30 min", f"{df_pred['ace_pred_MWh'].iloc[1]:+.1f} MWh" if len(df_pred) > 1 else "—")
        k3.metric("+60 min", f"{df_pred['ace_pred_MWh'].iloc[3]:+.1f} MWh" if len(df_pred) > 3 else "—")
        k4.metric("+120 min", f"{df_pred['ace_pred_MWh'].iloc[7]:+.1f} MWh" if len(df_pred) > 7 else "—")

        # Tabulka
        df_show = df_pred[["ace_pred_MWh", "ace_pred_MW", "h"]].copy()
        df_show.index = df_show.index.strftime("%H:%M")
        df_show.index.name = "Čas"
        df_show = df_show.rename(columns={
            "ace_pred_MWh": "Predikce [MWh/15min]",
            "ace_pred_MW":  "Predikce [MW]",
            "h":            "Horizont [ISP]",
        })
        st.dataframe(df_show, use_container_width=True, hide_index=False)

        st.info("🟣 Predikce je také zobrazena v záložce **📊 Odchylka & Generace** — fialová čárkovaná křivka.")

    # ── Stavový panel dat ────────────────────────────────────────
    if "model_data" not in st.session_state:
        st.caption("Klikni na **Krok 1** pro stažení dat.")
        return

    sources  = st.session_state["model_data"]
    now_ts   = st.session_state["model_now"]
    errors   = st.session_state.get("model_errors", {})

    st.markdown(f"**Data stažena:** {now_ts.strftime('%d.%m.%Y %H:%M:%S')}")

    SOURCE_META = {
        "ACE":          {"popis": "Systémová odchylka CZ [MWh/15min]", "zdroj": "ČEPS SOAP", "klic": True},
        "SVR":          {"popis": "SVR aktivace — aFRR/mFRR [MW]",     "zdroj": "ČEPS SOAP", "klic": False},
        "Zatížení RT":  {"popis": "Zatížení soustavy RT [MW]",          "zdroj": "ČEPS SOAP", "klic": False},
        "DAP ceny":     {"popis": "Day-Ahead Price CZ [EUR/MWh]",       "zdroj": "ENTSO-E",   "klic": False},
        "Load forecast":{"popis": "Load forecast D+1 [MW]",             "zdroj": "ENTSO-E",   "klic": False},
        "Wind/Solar fc":{"popis": "Wind + solar forecast D+1 [MW]",     "zdroj": "ENTSO-E",   "klic": False},
    }

    st.markdown("#### 📡 Stav zdrojů dat")
    status_rows = []
    for name, meta in SOURCE_META.items():
        df = sources.get(name, pd.DataFrame())
        if df.empty:
            status_rows.append({
                "Zdroj":         name,
                "Popis":         meta["popis"],
                "API":           meta["zdroj"],
                "Řádků":         0,
                "Poslední data": "—",
                "Zpoždění":      "—",
                "Stav":          "❌ chyba" + (f": {errors[name]}" if name in errors else ""),
            })
        else:
            last_ts = df.index.max()
            lag_min = int((now_ts - last_ts).total_seconds() / 60)
            lag_str = f"{lag_min} min"
            if lag_min <= 20:
                stav = "✅ OK"
            elif lag_min <= 60:
                stav = "⚠️ zpoždění"
            else:
                stav = "🔴 staré"
            if meta["klic"]:
                stav += " ⭐"
            status_rows.append({
                "Zdroj":         name,
                "Popis":         meta["popis"],
                "API":           meta["zdroj"],
                "Řádků":         len(df),
                "Poslední data": last_ts.strftime("%d.%m %H:%M"),
                "Zpoždění":      lag_str,
                "Stav":          stav,
            })

    st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)

    # ── Detailní tabulky ─────────────────────────────────────────
    st.markdown("#### 📋 Data podle zdroje")

    COL_MAP = {
        "ACE":           ["ace_MWh", "ace_MW"],
        "SVR":           ["aFRR_up_MW", "aFRR_dn_MW", "mFRR_up_MW", "mFRR_dn_MW", "mFRR5_MW"],
        "Zatížení RT":   ["load_actual_pumping_MW", "load_actual_MW"],
        "DAP ceny":      ["dap_EUR_MWh"],
        "Load forecast": ["load_fc_MW"],
        "Wind/Solar fc": [],
    }

    inner_tabs = st.tabs(list(SOURCE_META.keys()))
    for tab_obj, name in zip(inner_tabs, SOURCE_META.keys()):
        with tab_obj:
            df = sources.get(name, pd.DataFrame())
            if df.empty:
                st.warning(f"Žádná data pro **{name}**.")
                continue

            show_cols = [c for c in COL_MAP.get(name, []) if c in df.columns]
            if not show_cols:
                show_cols = df.select_dtypes(include="number").columns.tolist()

            df_show = df[show_cols].sort_index(ascending=False).copy()

            # KPI
            last_row = df_show.iloc[0]
            kpi_cols = st.columns(min(len(show_cols), 4))
            for i, col in enumerate(show_cols[:4]):
                val = last_row.get(col, np.nan)
                if pd.notna(val):
                    kpi_cols[i].metric(
                        col.replace("_", " "),
                        f"{val:+.1f}" if "MWh" in col or "MW" in col else f"{val:.2f}"
                    )
                else:
                    kpi_cols[i].metric(col.replace("_", " "), "—")

            df_display = df_show.copy()
            df_display.index = df_display.index.strftime("%d.%m.%Y %H:%M")
            df_display.index.name = "Čas"
            st.dataframe(df_display, use_container_width=True, height=380)

            st.download_button(
                f"⬇ CSV — {name}",
                data=df_display.to_csv(),
                file_name=f"model_{name.lower().replace('/', '_').replace(' ', '_')}.csv",
                mime="text/csv",
            )

    # ── Sloučená tabulka ─────────────────────────────────────────
    st.markdown("#### 🔗 Sloučená tabulka všech featur")
    dfs_to_merge = [df.select_dtypes(include="number")
                    for df in sources.values() if not df.empty]
    if dfs_to_merge:
        merged = pd.concat(dfs_to_merge, axis=1)
        merged = merged[~merged.index.duplicated(keep="last")].sort_index(ascending=False)
        merged_display = merged.copy()
        merged_display.index = merged_display.index.strftime("%d.%m.%Y %H:%M")
        merged_display.index.name = "Čas"
        st.dataframe(merged_display, use_container_width=True, height=420)
        st.download_button(
            "⬇ CSV — všechna data",
            data=merged_display.to_csv(),
            file_name="model_features_merged.csv",
            mime="text/csv",
        )

        nan_df = (merged.isna().mean() * 100).round(1).reset_index()
        nan_df.columns = ["Featura", "NaN %"]
        nan_df["Pokrytí %"] = (100 - nan_df["NaN %"]).round(1)
        with st.expander("📊 Pokrytí dat (% NaN per featura)"):
            st.dataframe(nan_df, use_container_width=True, hide_index=True)
