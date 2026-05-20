"""
04_predict.py
═══════════════════════════════════════════════════════════════════
Produkční predikční skript — spouští se ze Streamlitu při každém
refreshi dashboardu (místo cronu).

Co dělá:
  1. Stáhne posledních 7 dní dat z ČEPS + ENTSO-E (stejné funkce
     jako dashboard — žádné nové závislosti)
  2. Sestaví feature vektor pro aktuální čas t
  3. Spustí 96 XGBoost modelů → predikce ACE na t+15min .. t+24h
  4. Uloží výsledek do predictions.parquet (append)
  5. Vrátí DataFrame vhodný přímo pro Plotly graf

Robustnost při chybějících datech:
  - Chybí ACE lag → použije poslední dostupnou hodnotu (ffill)
  - Chybí DAP (před 13:00) → ffill z předchozího dne
  - Chybí wind/solar → NaN (XGBoost zvládne)
  - Cokoliv jiného → NaN → XGBoost zvládne

Integrace do app.py:
  from predict import run_prediction, load_latest_predictions
═══════════════════════════════════════════════════════════════════
"""

import os
import logging
import warnings
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd
import joblib
import streamlit as st

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)

MODEL_PATH       = "./data/models/xgb_baseline.joblib"
PREDICTIONS_PATH = "./data/predictions/predictions.parquet"
os.makedirs("./data/predictions", exist_ok=True)

# Stejné konstanty jako v 02_build_features.py
CEPS_NS  = "https://www.ceps.cz/CepsData/StructuredData/1.0"
CB_MAP   = {
    "value1": "PSE_actual_MW",    "value2": "PSE_planned_MW",
    "value3": "SEPS_actual_MW",   "value4": "SEPS_planned_MW",
    "value5": "APG_actual_MW",    "value6": "APG_planned_MW",
    "value7": "TenneT_actual_MW", "value8": "TenneT_planned_MW",
    "value9": "50HzT_actual_MW",  "value10": "50HzT_planned_MW",
    "value11": "CEPS_actual_MW",  "value12": "CEPS_planned_MW",
}
GEN_MAP  = {
    "value1": "TPP_MW",  "value2": "CCGT_MW", "value3": "NPP_MW",
    "value4": "HPP_MW",  "value5": "PsPP_MW", "value6": "AltPP_MW",
    "value7": "ApPP_MW", "value8": "WPP_MW",  "value9": "PVPP_MW",
}
RES_MAP  = {"value1": "WPP_res_MW", "value2": "PVPP_res_MW"}
OBSERVED_COLS = [
    "load_actual_pumping_MW", "load_actual_MW",
    "TPP_MW", "CCGT_MW", "NPP_MW", "HPP_MW", "PsPP_MW",
    "AltPP_MW", "ApPP_MW", "WPP_MW", "PVPP_MW",
    "WPP_res_MW", "PVPP_res_MW",
    "PSE_actual_MW", "SEPS_actual_MW", "APG_actual_MW",
    "TenneT_actual_MW", "50HzT_actual_MW", "CEPS_actual_MW",
    "net_DE_MW", "net_SK_MW", "net_AT_MW", "net_PL_MW",
    "aFRR_up_MW", "aFRR_dn_MW", "mFRR_up_MW", "mFRR_dn_MW", "mFRR5_MW",
    "aFRR_net_MW", "mFRR_net_MW",
    "res_total_MW", "res_penetration_pct", "total_gen_MW",
    "net_export_total_MW", "solar_fc_error_MW", "load_fc_error_MW",
    "imbal_price_long_eur", "imbal_price_short_eur",
]


# ════════════════════════════════════════════════════════════════
# NAČTENÍ MODELU
# ════════════════════════════════════════════════════════════════

@st.cache_resource
def load_model():
    """Načte model jednou a drží v paměti (Streamlit cache_resource)."""
    if not os.path.exists(MODEL_PATH):
        log.error(f"Model nenalezen: {MODEL_PATH}")
        return None, None
    data = joblib.load(MODEL_PATH)
    log.info(f"Model načten: {len(data['models'])} horizontů")
    return data["models"], data["feature_cols"]


# ════════════════════════════════════════════════════════════════
# FETCH ŽIVÝCH DAT (7 dní zpět)
# Používáme stejné ČEPS/ENTSO-E funkce jako dashboard
# ════════════════════════════════════════════════════════════════

def fetch_live_ace(ceps_client) -> pd.Series:
    """
    Posledních 7 dní ACE z ČEPS (QH = 15min).
    Robustní: pokud chybí posledních N ISP → ffill.
    """
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now - timedelta(days=7)
    try:
        result = ceps_client.service.AktualniSystemovaOdchylkaCR(
            dateFrom  =start.replace(tzinfo=None),
            dateTo    =now.replace(tzinfo=None),
            agregation="QH",
            function  ="AVG",
        )
        rows = []
        for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
            a = item.attrib
            ts = pd.Timestamp(a["date"]).tz_convert("Europe/Prague")
            rows.append({"time": ts, "ace_MWh": float(a.get("value1", 0))})
        if not rows:
            return pd.Series(dtype=float, name="ace_MWh")
        df = pd.DataFrame(rows).set_index("time")["ace_MWh"]
        # Doplň na pravidelný 15min grid ffill (ČEPS má ~5min zpoždění)
        idx_full = pd.date_range(
            df.index.min(), now, freq="15min", tz="Europe/Prague"
        )
        df = df.reindex(idx_full).ffill()
        return df
    except Exception as e:
        log.warning(f"ACE fetch failed: {e}")
        return pd.Series(dtype=float, name="ace_MWh")


def fetch_live_features(ceps_client, entsoe_client) -> pd.DataFrame:
    """
    Stáhne všechna potřebná data pro sestavení feature vektoru.
    Vrátí DataFrame na 15min gridu posledních 7 dní + D+1 forecasts.
    """
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now - timedelta(days=7)

    # ── ACE (target history + lags) ─────────────────────────────
    ace = fetch_live_ace(ceps_client)
    if ace.empty:
        log.error("ACE data nejsou dostupná — predikce nelze provést")
        return pd.DataFrame()

    master_idx = ace.index
    frames     = [ace.rename("ace_MWh").to_frame()]

    def _safe_ceps(method, kwargs, rename_map=None):
        """Bezpečné ČEPS volání — při chybě vrátí prázdný DataFrame."""
        try:
            result = getattr(ceps_client.service, method)(
                dateFrom  =start.replace(tzinfo=None),
                dateTo    =now.replace(tzinfo=None),
                **kwargs
            )
            rows = []
            for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
                a   = item.attrib
                ts  = pd.Timestamp(a["date"]).tz_convert("Europe/Prague")
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
            df = pd.DataFrame(rows).set_index("time")
            if rename_map:
                df = df.rename(columns=rename_map)
            # Resample na 15min + zarovnej na master_idx
            df = df.resample("15min").mean().interpolate(method="time", limit=2)
            df = df.reindex(master_idx, method="nearest",
                            tolerance=pd.Timedelta("8min"))
            return df
        except Exception as e:
            log.warning(f"  {method}: {e}")
            return pd.DataFrame()

    def _safe_entsoe(fn, kwargs, ffill=True):
        """Bezpečné ENTSO-E volání — při chybě vrátí prázdný DataFrame."""
        try:
            raw = fn(**kwargs)
            if raw is None or (hasattr(raw, "empty") and raw.empty):
                return pd.DataFrame()
            if isinstance(raw, pd.Series):
                raw = raw.to_frame()
            if raw.index.tz is None:
                raw.index = raw.index.tz_localize("UTC").tz_convert("Europe/Prague")
            else:
                raw.index = raw.index.tz_convert("Europe/Prague")
            if ffill:
                raw = raw.resample("15min").last().ffill()
            else:
                raw = raw.resample("15min").mean().interpolate(limit=2)
            raw = raw.reindex(master_idx, method="ffill")
            return raw
        except Exception as e:
            log.warning(f"  ENTSO-E fetch: {e}")
            return pd.DataFrame()

    # ── ČEPS observed features ───────────────────────────────────
    svr = _safe_ceps("AktivaceSVRvCR", {
        "agregation": "QH", "function": "AVG", "param1": "all"
    })
    if not svr.empty:
        svr_map = {
            "value1": "aFRR_up_MW", "value2": "aFRR_dn_MW",
            "value3": "mFRR_up_MW", "value4": "mFRR_dn_MW",
            "value7": "mFRR5_MW",
        }
        svr = svr.rename(columns={k: v for k, v in svr_map.items()
                                   if k in svr.columns})
        frames.append(svr)

    load = _safe_ceps("Load", {
        "agregation": "QH", "function": "AVG", "version": "RT"
    })
    if not load.empty:
        load = load.rename(columns={"value1": "load_actual_pumping_MW",
                                     "value2": "load_actual_MW"})
        frames.append(load)

    cb = _safe_ceps("CrossborderPowerFlows", {
        "agregation": "QH", "function": "AVG", "version": "RT"
    }, rename_map=CB_MAP)
    if not cb.empty:
        actual_cols = [c for c in cb.columns if "actual" in c]
        cb_feat = cb[actual_cols].copy()
        if "TenneT_actual_MW" in cb_feat and "50HzT_actual_MW" in cb_feat:
            cb_feat["net_DE_MW"] = cb_feat["TenneT_actual_MW"] + cb_feat["50HzT_actual_MW"]
        if "SEPS_actual_MW" in cb_feat:
            cb_feat["net_SK_MW"] = cb_feat["SEPS_actual_MW"]
        if "APG_actual_MW" in cb_feat:
            cb_feat["net_AT_MW"] = cb_feat["APG_actual_MW"]
        if "PSE_actual_MW" in cb_feat:
            cb_feat["net_PL_MW"] = cb_feat["PSE_actual_MW"]
        frames.append(cb_feat)

    gen = _safe_ceps("Generation", {
        "agregation": "QH", "function": "AVG", "version": "RT", "para1": "all"
    }, rename_map=GEN_MAP)
    if not gen.empty:
        frames.append(gen)

    res = _safe_ceps("GenerationRES", {
        "agregation": "QH", "function": "AVG", "version": "RT", "para1": "all"
    }, rename_map=RES_MAP)
    if not res.empty:
        frames.append(res)

    # ── ENTSO-E known future features ────────────────────────────
    start_e  = pd.Timestamp(start, tz="Europe/Prague")
    end_e    = pd.Timestamp(now + timedelta(days=2), tz="Europe/Prague")
    today_e  = now.normalize()
    tomorrow = today_e + timedelta(days=1)

    # DAP D0 + D+1
    dap_frames = []
    for day_start in [today_e, tomorrow]:
        try:
            raw = entsoe_client.query_day_ahead_prices(
                "CZ",
                start=day_start,
                end=day_start + timedelta(days=1),
            )
            if raw is not None and not raw.empty:
                s = raw.tz_convert("Europe/Prague").rename("dap_EUR_MWh")
                # Hodinová → 15min ffill
                idx_15 = pd.date_range(
                    day_start, day_start + timedelta(days=1),
                    freq="15min", inclusive="left", tz="Europe/Prague"
                )
                s = s.reindex(idx_15, method="ffill")
                dap_frames.append(s.to_frame())
        except Exception as e:
            log.debug(f"  DAP {day_start.date()}: {e}")

    if dap_frames:
        dap_all = pd.concat(dap_frames)
        dap_all = dap_all.reindex(master_idx, method="ffill")
        frames.append(dap_all)

    # Load forecast D+1
    lfc = _safe_entsoe(
        entsoe_client.query_load_forecast,
        {"country_code": "CZ", "start": today_e, "end": end_e},
        ffill=True,
    )
    if not lfc.empty:
        lfc.columns = ["load_fc_MW"]
        frames.append(lfc)

    # Wind + Solar forecast D+1
    ws = _safe_entsoe(
        entsoe_client.query_wind_and_solar_forecast,
        {"country_code": "CZ", "start": today_e, "end": end_e,
         "psr_type": None},
        ffill=True,
    )
    if not ws.empty:
        if isinstance(ws.columns, pd.MultiIndex):
            lvls = ws.columns.get_level_values(1)
            ws = (ws.xs("Actual Aggregated", level=1, axis=1)
                  if "Actual Aggregated" in lvls
                  else ws.xs(lvls[0], level=1, axis=1))
        ws.columns = [f"fc_{c.lower()}_MW" for c in ws.columns]
        frames.append(ws)

    # Imbalance prices (observed, ale použitelné jako featura)
    try:
        imp = entsoe_client.query_imbalance_prices(
            "CZ", start=start_e, end=pd.Timestamp(now, tz="Europe/Prague")
        )
        if imp is not None and not imp.empty:
            imp = imp.tz_convert("Europe/Prague")
            imp.columns = [f"imbal_price_{c.lower()}_EUR" for c in imp.columns]
            imp = imp.resample("15min").last().ffill()
            imp = imp.reindex(master_idx, method="ffill")
            frames.append(imp)
    except Exception:
        pass

    # ── Sestavení DataFrame ──────────────────────────────────────
    df = pd.concat([f for f in frames if not f.empty], axis=1)
    df = df.loc[master_idx]

    log.info(f"Live features: {len(df)} řádků × {len(df.columns)} sloupců")
    return df


# ════════════════════════════════════════════════════════════════
# SESTAVENÍ FEATURE VEKTORU
# ════════════════════════════════════════════════════════════════

def build_live_feature_vector(df: pd.DataFrame, feature_cols: list) -> pd.DataFrame:
    """
    Přidá calendar + lag + rolling + derived featury.
    Vrátí DataFrame kde každý řádek = feature vektor pro predikci
    z daného timestampu.
    """
    # ── Calendar ────────────────────────────────────────────────
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
    df["is_holiday"] = [(m, d) in HOLIDAYS
                        for m, d in zip(idx.month, idx.day)]
    df["is_holiday"] = df["is_holiday"].astype(int)
    df["is_monday"]  = (idx.dayofweek == 0).astype(int)
    df["is_friday"]  = (idx.dayofweek == 4).astype(int)

    # ── Lags ────────────────────────────────────────────────────
    ace = df["ace_MWh"]
    for lag in [1, 2, 3, 4, 8, 12, 16, 24, 48, 96, 192, 288, 672]:
        df[f"ace_lag_{lag}"] = ace.shift(lag)

    # ── Rolling ─────────────────────────────────────────────────
    for window in [4, 8, 16, 32, 96]:
        df[f"ace_roll_mean_{window}"] = ace.shift(1).rolling(window).mean()
        df[f"ace_roll_std_{window}"]  = ace.shift(1).rolling(window).std()
    if "PVPP_MW" in df.columns and "WPP_MW" in df.columns:
        res_total = df["PVPP_MW"] + df["WPP_MW"]
        df["res_total_MW"]        = res_total
        df["res_roll_mean_96_MW"] = res_total.shift(1).rolling(96).mean()
        df["res_roll_std_96_MW"]  = res_total.shift(1).rolling(96).std()
    df["ace_daily_mean"] = ace.shift(1).rolling(96).mean()

    # ── Derived ─────────────────────────────────────────────────
    if "load_actual_MW" in df.columns and "load_fc_MW" in df.columns:
        df["load_fc_error_MW"] = df["load_actual_MW"] - df["load_fc_MW"]
    if "PVPP_MW" in df.columns and "fc_solar_mw" in df.columns:
        df["solar_fc_error_MW"] = df["PVPP_MW"] - df["fc_solar_mw"]
    gen_cols = [c for c in ["TPP_MW","CCGT_MW","NPP_MW","HPP_MW",
                             "PsPP_MW","AltPP_MW","WPP_MW","PVPP_MW"]
                if c in df.columns]
    if gen_cols:
        total_gen = df[gen_cols].sum(axis=1)
        df["total_gen_MW"] = total_gen
        if "PVPP_MW" in df.columns and "WPP_MW" in df.columns:
            df["res_penetration_pct"] = (
                (df["PVPP_MW"] + df["WPP_MW"]) / total_gen.replace(0, np.nan) * 100
            )
    net_cols = [c for c in df.columns if c.startswith("net_") and c.endswith("_MW")]
    if net_cols:
        df["net_export_total_MW"] = df[net_cols].sum(axis=1)
    if "aFRR_up_MW" in df.columns and "aFRR_dn_MW" in df.columns:
        df["aFRR_net_MW"] = df["aFRR_up_MW"] - df["aFRR_dn_MW"]
    if "mFRR_up_MW" in df.columns and "mFRR_dn_MW" in df.columns:
        df["mFRR_net_MW"] = df["mFRR_up_MW"] - df["mFRR_dn_MW"]
    if "dap_EUR_MWh" in df.columns:
        df["dap_daily_spread_EUR"] = df["dap_EUR_MWh"].resample("D").transform(
            lambda x: x.max() - x.min()
        )

    # ── Odstraň observed featury (nejsou dostupné v čase predikce) ──
    drop_obs = [c for c in OBSERVED_COLS if c in df.columns]
    df = df.drop(columns=drop_obs)

    # ── Zarovnej na feature_cols z trénování ────────────────────
    # Přidej chybějící sloupce jako NaN (XGBoost zvládne)
    for col in feature_cols:
        if col not in df.columns and col != "ace_MWh":
            df[col] = np.nan

    # Odstraň sloupce které model nezná
    known = [c for c in feature_cols if c in df.columns]
    unknown = [c for c in df.columns if c not in feature_cols and c != "ace_MWh"]
    if unknown:
        df = df.drop(columns=unknown)

    log.debug(f"Feature vector: {len(known)}/{len(feature_cols)} featur dostupných")
    return df


# ════════════════════════════════════════════════════════════════
# PREDIKCE
# ════════════════════════════════════════════════════════════════

def run_prediction(ceps_client, entsoe_client) -> Optional[pd.DataFrame]:
    """
    Hlavní funkce — volej ze Streamlitu při každém refreshi.

    Vrátí DataFrame s predikcí:
      index:        timestamp predikovaného ISP
      ace_pred_MWh: predikovaná hodnota ACE
      h:            horizont (1..96)
      predicted_at: kdy predikce vznikla
    """
    models, feature_cols = load_model()
    if models is None:
        log.error("Model není načten")
        return None

    now = pd.Timestamp.now(tz="Europe/Prague").floor("15min")
    log.info(f"Predikce z {now}")

    # 1. Stáhni živá data
    with st.spinner("Načítám data pro predikci..."):
        df_live = fetch_live_features(ceps_client, entsoe_client)

    if df_live.empty:
        log.error("Živá data nejsou dostupná")
        return None

    # 2. Sestavení feature vektoru
    df_feat = build_live_feature_vector(df_live, feature_cols)

    # 3. Najdi poslední řádek s plnými lags (t = now)
    # Potřebujeme min lag_672 = 7 dní zpět
    valid_rows = df_feat.dropna(subset=["ace_lag_1", "ace_lag_96"])
    if valid_rows.empty:
        log.error("Nedostatek dat pro lags")
        return None

    # Vezmi nejnovější dostupný řádek jako "teď"
    t_now = valid_rows.index.max()
    X_now = valid_rows.loc[[t_now], feature_cols].values  # shape (1, n_features)

    # 4. Predikuj pro všech 96 horizontů
    predictions = []
    for h in range(1, 97):
        if h not in models:
            continue
        try:
            pred = float(models[h].predict(X_now)[0])
        except Exception as e:
            log.warning(f"  h={h}: predict failed: {e}")
            pred = np.nan

        target_ts = t_now + timedelta(minutes=15 * h)
        predictions.append({
            "timestamp":    target_ts,
            "ace_pred_MWh": pred,
            "h":            h,
            "predicted_at": now,
        })

    df_pred = pd.DataFrame(predictions).set_index("timestamp")
    df_pred.index = df_pred.index.tz_localize("Europe/Prague") \
        if df_pred.index.tz is None else df_pred.index

    # 5. Ulož do parquet (append — uchováváme historii predikcí)
    _save_predictions(df_pred)

    log.info(f"Predikce OK: {len(df_pred)} ISP, "
             f"{df_pred.index.min().strftime('%H:%M')} → "
             f"{df_pred.index.max().strftime('%H:%M %d.%m')}")
    return df_pred


def _save_predictions(df_new: pd.DataFrame):
    """Append nové predikce do parquet souboru."""
    try:
        if os.path.exists(PREDICTIONS_PATH):
            existing = pd.read_parquet(PREDICTIONS_PATH)
            # Zachovej jen predikce z posledních 48h (šetři místo)
            cutoff = pd.Timestamp.now(tz="Europe/Prague") - timedelta(hours=48)
            existing = existing[existing.index >= cutoff]
            combined = pd.concat([existing, df_new])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.to_parquet(PREDICTIONS_PATH)
        else:
            df_new.to_parquet(PREDICTIONS_PATH)
    except Exception as e:
        log.warning(f"Uložení predikcí selhalo: {e}")


# ════════════════════════════════════════════════════════════════
# NAČTENÍ ULOŽENÝCH PREDIKCÍ (pro graf)
# ════════════════════════════════════════════════════════════════

def load_latest_predictions() -> pd.DataFrame:
    """
    Načte nejnovější sadu predikcí z parquet.
    Vrátí jen predikce z posledního predikčního běhu.
    """
    if not os.path.exists(PREDICTIONS_PATH):
        return pd.DataFrame()
    try:
        df = pd.read_parquet(PREDICTIONS_PATH)
        if df.empty:
            return df
        # Nejnovější predicted_at
        latest_run = df["predicted_at"].max()
        return df[df["predicted_at"] == latest_run].sort_index()
    except Exception:
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# PLOTLY TRACE — připravená křivka pro dashboard
# ════════════════════════════════════════════════════════════════

def prediction_trace(df_pred: pd.DataFrame):
    """
    Vrátí Plotly Scatter trace pro predikci ACE.
    Použij přímo v fig.add_trace().

    Příklad v app.py:
        from predict import run_prediction, prediction_trace
        df_pred = run_prediction(ceps, entsoe_client)
        if df_pred is not None:
            fig.add_trace(prediction_trace(df_pred))
    """
    import plotly.graph_objects as go

    # Barevné kódování jistoty: h=1..8 tmavší, h=32..96 světlejší
    colors = df_pred["h"].apply(
        lambda h: "rgba(255,111,0,0.9)" if h <= 8
        else "rgba(255,111,0,0.6)" if h <= 32
        else "rgba(255,111,0,0.35)"
    )

    return go.Scatter(
        x=df_pred.index,
        y=df_pred["ace_pred_MWh"],
        mode="lines",
        name="Predikce ACE",
        line=dict(color="#FF6F00", width=2, dash="dot"),
        hovertemplate=(
            "<b>Predikce ACE</b><br>"
            "%{x|%a %d.%m %H:%M}<br>"
            "<b>%{y:+.1f} MWh</b><br>"
            "<extra></extra>"
        ),
        showlegend=True,
    )


# ════════════════════════════════════════════════════════════════
# STANDALONE TEST (mimo Streamlit)
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(message)s")

    print("Test predikce (bez Streamlit cache)...")

    # Načti model přímo
    if not os.path.exists(MODEL_PATH):
        print(f"Model nenalezen: {MODEL_PATH}")
        sys.exit(1)

    model_data   = joblib.load(MODEL_PATH)
    models_dict  = model_data["models"]
    feature_cols = model_data["feature_cols"]

    print(f"Model načten: {len(models_dict)} horizontů")
    print(f"Feature cols: {len(feature_cols)}")
    print(f"Prvních 10 featur: {feature_cols[:10]}")
    print("\nPro plný test spusť ze Streamlitu nebo přidej ČEPS/ENTSO-E klienty.")
