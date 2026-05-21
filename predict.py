"""
predict.py — predikce systémové odchylky ACE CZ
Umístění: vedle app.py v kořeni repozitáře
"""

import os
import logging
import warnings

import numpy as np
import pandas as pd
import joblib
import streamlit as st

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)

_HERE            = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH       = os.path.join(_HERE, "data", "models", "xgb_baseline_slim.joblib")
PREDICTIONS_PATH = os.path.join(_HERE, "data", "predictions", "predictions.parquet")
os.makedirs(os.path.join(_HERE, "data", "predictions"), exist_ok=True)

CEPS_NS = "https://www.ceps.cz/CepsData/StructuredData/1.0"
CB_MAP  = {
    "value1": "PSE_actual_MW",    "value2": "PSE_planned_MW",
    "value3": "SEPS_actual_MW",   "value4": "SEPS_planned_MW",
    "value5": "APG_actual_MW",    "value6": "APG_planned_MW",
    "value7": "TenneT_actual_MW", "value8": "TenneT_planned_MW",
    "value9": "50HzT_actual_MW",  "value10": "50HzT_planned_MW",
    "value11": "CEPS_actual_MW",  "value12": "CEPS_planned_MW",
}
GEN_MAP = {
    "value1": "TPP_MW",  "value2": "CCGT_MW", "value3": "NPP_MW",
    "value4": "HPP_MW",  "value5": "PsPP_MW", "value6": "AltPP_MW",
    "value7": "ApPP_MW", "value8": "WPP_MW",  "value9": "PVPP_MW",
}
RES_MAP = {"value1": "WPP_res_MW", "value2": "PVPP_res_MW"}
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
TZ = "Europe/Prague"


def _tz(ts):
    """Zajistí že timestamp má správnou TZ — bez double-localize."""
    if ts.tzinfo is None:
        return ts.tz_localize(TZ)
    return ts.tz_convert(TZ)


def _tz_df(df):
    """Zajistí TZ na indexu DataFrame."""
    if df.empty:
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TZ)
    else:
        df.index = df.index.tz_convert(TZ)
    return df


# ════════════════════════════════════════════════════════════════
# MODEL
# ════════════════════════════════════════════════════════════════

@st.cache_resource
def load_model():
    if not os.path.exists(MODEL_PATH):
        log.error(f"Model nenalezen: {MODEL_PATH}")
        return None, None, None
    data = joblib.load(MODEL_PATH)
    return data["models"], data["feature_cols"], data["key_horizons"]


# ════════════════════════════════════════════════════════════════
# ČEPS PARSOVÁNÍ
# ════════════════════════════════════════════════════════════════

def _parse_ceps(result, rename_map=None):
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        a = item.attrib
        try:
            ts = pd.Timestamp(a["date"])
            if ts.tzinfo is None:
                ts = ts.tz_localize(TZ)
            else:
                ts = ts.tz_convert(TZ)
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
    df = pd.DataFrame(rows).set_index("time")
    if rename_map:
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    return df


def _safe_ceps(ceps_client, method, kwargs, rename_map, master_idx):
    now   = pd.Timestamp.now(tz=TZ)
    start = now - pd.Timedelta(days=7)
    try:
        result = getattr(ceps_client.service, method)(
            dateFrom=start.replace(tzinfo=None),
            dateTo  =now.replace(tzinfo=None),
            **kwargs,
        )
        df = _parse_ceps(result, rename_map)
        if df.empty:
            return pd.DataFrame()
        df = df.resample("15min").mean().interpolate(method="time", limit=2)
        return df.reindex(master_idx, method="nearest", tolerance=pd.Timedelta("8min"))
    except Exception as e:
        log.warning(f"ČEPS {method}: {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# ENTSO-E FETCH — bez tz parametru konfliktu
# ════════════════════════════════════════════════════════════════

def _safe_entsoe(fn, kwargs, master_idx, ffill=True):
    try:
        raw = fn(**kwargs)
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return pd.DataFrame()
        if isinstance(raw, pd.Series):
            raw = raw.to_frame()
        raw = _tz_df(raw)
        if isinstance(raw.columns, pd.MultiIndex):
            lvls = raw.columns.get_level_values(1)
            raw = (raw.xs("Actual Aggregated", level=1, axis=1)
                   if "Actual Aggregated" in lvls
                   else raw.xs(lvls[0], level=1, axis=1))
        if ffill:
            raw = raw.resample("15min").last().ffill()
        else:
            raw = raw.resample("15min").mean().interpolate(limit=2)
        return raw.reindex(master_idx, method="ffill")
    except Exception as e:
        log.warning(f"ENTSO-E {fn.__name__}: {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# FETCH ŽIVÝCH DAT
# ════════════════════════════════════════════════════════════════

def _fetch_ace(ceps_client):
    now   = pd.Timestamp.now(tz=TZ)
    start = now - pd.Timedelta(days=7)
    try:
        result = ceps_client.service.AktualniSystemovaOdchylkaCR(
            dateFrom  =start.replace(tzinfo=None),
            dateTo    =now.replace(tzinfo=None),
            agregation="QH",
            function  ="AVG",
        )
        df = _parse_ceps(result, {"value1": "ace_MWh"})
        if df.empty or "ace_MWh" not in df.columns:
            return pd.Series(dtype=float, name="ace_MWh")
        s = df["ace_MWh"]
        # Doplň na pravidelný 15min grid (ffill pro ~5min zpoždění ČEPS)
        idx_full = pd.date_range(
            start=s.index.min(),
            end=now,
            freq="15min",
            tz=TZ,
        )
        return s.reindex(idx_full).ffill()
    except Exception as e:
        log.warning(f"ACE fetch: {e}")
        return pd.Series(dtype=float, name="ace_MWh")


def fetch_live_data(ceps_client, entsoe_client):
    ace = _fetch_ace(ceps_client)
    if ace.empty:
        log.error("ACE data nejsou dostupná")
        return pd.DataFrame()

    master_idx = ace.index
    now        = pd.Timestamp.now(tz=TZ)
    today      = now.normalize()                    # pd.Timestamp s TZ
    tomorrow   = today + pd.Timedelta(days=1)       # pd.Timestamp s TZ
    end_fc     = today + pd.Timedelta(days=2)       # pd.Timestamp s TZ

    frames = [ace.rename("ace_MWh").to_frame()]

    # ── ČEPS ─────────────────────────────────────────────────────
    svr = _safe_ceps(ceps_client, "AktivaceSVRvCR",
                     {"agregation": "QH", "function": "AVG", "param1": "all"},
                     {"value1": "aFRR_up_MW", "value2": "aFRR_dn_MW",
                      "value3": "mFRR_up_MW", "value4": "mFRR_dn_MW",
                      "value7": "mFRR5_MW"},
                     master_idx)
    if not svr.empty:
        frames.append(svr)

    load = _safe_ceps(ceps_client, "Load",
                      {"agregation": "QH", "function": "AVG", "version": "RT"},
                      {"value1": "load_actual_pumping_MW", "value2": "load_actual_MW"},
                      master_idx)
    if not load.empty:
        frames.append(load)

    cb = _safe_ceps(ceps_client, "CrossborderPowerFlows",
                    {"agregation": "QH", "function": "AVG", "version": "RT"},
                    CB_MAP, master_idx)
    if not cb.empty:
        cb_feat = cb[[c for c in cb.columns if "actual" in c]].copy()
        if "TenneT_actual_MW" in cb_feat.columns and "50HzT_actual_MW" in cb_feat.columns:
            cb_feat["net_DE_MW"] = cb_feat["TenneT_actual_MW"] + cb_feat["50HzT_actual_MW"]
        if "SEPS_actual_MW" in cb_feat.columns:
            cb_feat["net_SK_MW"] = cb_feat["SEPS_actual_MW"]
        if "APG_actual_MW" in cb_feat.columns:
            cb_feat["net_AT_MW"] = cb_feat["APG_actual_MW"]
        if "PSE_actual_MW" in cb_feat.columns:
            cb_feat["net_PL_MW"] = cb_feat["PSE_actual_MW"]
        frames.append(cb_feat)

    gen = _safe_ceps(ceps_client, "Generation",
                     {"agregation": "QH", "function": "AVG", "version": "RT", "para1": "all"},
                     GEN_MAP, master_idx)
    if not gen.empty:
        frames.append(gen)

    res = _safe_ceps(ceps_client, "GenerationRES",
                     {"agregation": "QH", "function": "AVG", "version": "RT", "para1": "all"},
                     RES_MAP, master_idx)
    if not res.empty:
        frames.append(res)

    # ── ENTSO-E ───────────────────────────────────────────────────
    # DAP D0 + D+1 — resample přímo, bez date_range s TZ konfliktem
    dap_frames = []
    for day in [today, tomorrow]:
        try:
            raw = entsoe_client.query_day_ahead_prices(
                "CZ",
                start=day,
                end=day + pd.Timedelta(days=1),
            )
            if raw is not None and not raw.empty:
                raw = _tz_df(raw.to_frame() if isinstance(raw, pd.Series) else raw)
                raw_15 = raw.resample("15min").last().ffill()
                raw_15.columns = ["dap_EUR_MWh"]
                dap_frames.append(raw_15)
        except Exception as e:
            log.debug(f"DAP {day.date()}: {e}")

    if dap_frames:
        dap = pd.concat(dap_frames)
        dap = dap[~dap.index.duplicated(keep="last")]
        dap = dap.reindex(master_idx, method="ffill")
        frames.append(dap)

    lfc = _safe_entsoe(
        entsoe_client.query_load_forecast,
        {"country_code": "CZ", "start": today, "end": end_fc},
        master_idx, ffill=True,
    )
    if not lfc.empty:
        lfc.columns = ["load_fc_MW"]
        frames.append(lfc)

    ws = _safe_entsoe(
        entsoe_client.query_wind_and_solar_forecast,
        {"country_code": "CZ", "start": today, "end": end_fc, "psr_type": None},
        master_idx, ffill=True,
    )
    if not ws.empty:
        ws.columns = [f"fc_{c.lower()}_MW" for c in ws.columns]
        frames.append(ws)

    try:
        imp = entsoe_client.query_imbalance_prices(
            "CZ",
            start=now - pd.Timedelta(days=7),
            end=now,
        )
        if imp is not None and not imp.empty:
            imp = _tz_df(imp)
            imp.columns = [f"imbal_price_{c.lower()}_EUR" for c in imp.columns]
            imp = imp.resample("15min").last().ffill()
            imp = imp.reindex(master_idx, method="ffill")
            frames.append(imp)
    except Exception as e:
        log.debug(f"Imbal prices: {e}")

    df = pd.concat([f for f in frames if not f.empty], axis=1)
    return df.loc[master_idx]


# ════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ════════════════════════════════════════════════════════════════

def build_features(df, feature_cols):
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
    if "PVPP_MW" in df.columns and "WPP_MW" in df.columns:
        res_total = df["PVPP_MW"] + df["WPP_MW"]
        df["res_total_MW"]        = res_total
        df["res_roll_mean_96_MW"] = res_total.shift(1).rolling(96).mean()
        df["res_roll_std_96_MW"]  = res_total.shift(1).rolling(96).std()
    df["ace_daily_mean"] = ace.shift(1).rolling(96).mean()

    if "load_actual_MW" in df.columns and "load_fc_MW" in df.columns:
        df["load_fc_error_MW"] = df["load_actual_MW"] - df["load_fc_MW"]
    if "PVPP_MW" in df.columns and "fc_solar_mw" in df.columns:
        df["solar_fc_error_MW"] = df["PVPP_MW"] - df["fc_solar_mw"]
    gen_cols = [c for c in ["TPP_MW","CCGT_MW","NPP_MW","HPP_MW",
                             "PsPP_MW","AltPP_MW","WPP_MW","PVPP_MW"]
                if c in df.columns]
    if gen_cols:
        total = df[gen_cols].sum(axis=1)
        df["total_gen_MW"] = total
        if "PVPP_MW" in df.columns and "WPP_MW" in df.columns:
            df["res_penetration_pct"] = (
                (df["PVPP_MW"] + df["WPP_MW"]) / total.replace(0, np.nan) * 100
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

    df = df.drop(columns=[c for c in OBSERVED_COLS if c in df.columns])

    for col in feature_cols:
        if col not in df.columns and col != "ace_MWh":
            df[col] = np.nan
    extra = [c for c in df.columns if c not in feature_cols and c != "ace_MWh"]
    df = df.drop(columns=extra)
    return df


# ════════════════════════════════════════════════════════════════
# PREDIKCE
# ════════════════════════════════════════════════════════════════

def run_prediction(ceps_client, entsoe_client):
    models, feature_cols, key_horizons = load_model()
    if models is None:
        log.error("Model není načten")
        return None

    now = pd.Timestamp.now(tz=TZ).floor("15min")

    with st.spinner("Načítám data pro predikci..."):
        df_live = fetch_live_data(ceps_client, entsoe_client)

    if df_live.empty:
        log.error("Živá data nejsou dostupná")
        return None

    df_feat = build_features(df_live, feature_cols)

    valid = df_feat.dropna(subset=["ace_lag_1", "ace_lag_96"])
    if valid.empty:
        log.error("Nedostatek dat pro lags")
        return None

    t_now = valid.index.max()
    X_now = valid.loc[[t_now], feature_cols].values

    key_preds = {}
    for h in key_horizons:
        if h not in models:
            continue
        try:
            key_preds[h] = float(models[h].predict(X_now)[0])
        except Exception as e:
            log.warning(f"h={h}: {e}")
            key_preds[h] = np.nan

    s = pd.Series(key_preds).reindex(range(1, 97)).interpolate(method="linear")

    predictions = []
    for h, pred in s.items():
        predictions.append({
            "timestamp":    t_now + pd.Timedelta(minutes=15 * h),
            "ace_pred_MWh": pred,
            "h":            h,
            "predicted_at": now,
        })

    df_pred = pd.DataFrame(predictions).set_index("timestamp")
    if df_pred.index.tz is None:
        df_pred.index = df_pred.index.tz_localize(TZ)

    _save_predictions(df_pred)
    log.info(f"Predikce OK: {t_now.strftime('%H:%M')} → "
             f"{df_pred.index.max().strftime('%H:%M %d.%m')}")
    return df_pred


def _save_predictions(df_new):
    try:
        if os.path.exists(PREDICTIONS_PATH):
            existing = pd.read_parquet(PREDICTIONS_PATH)
            cutoff   = pd.Timestamp.now(tz=TZ) - pd.Timedelta(hours=48)
            existing = existing[existing.index >= cutoff]
            combined = pd.concat([existing, df_new])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.to_parquet(PREDICTIONS_PATH)
        else:
            df_new.to_parquet(PREDICTIONS_PATH)
    except Exception as e:
        log.warning(f"Uložení predikcí: {e}")


def load_latest_predictions():
    if not os.path.exists(PREDICTIONS_PATH):
        return pd.DataFrame()
    try:
        df = pd.read_parquet(PREDICTIONS_PATH)
        if df.empty:
            return df
        latest = df["predicted_at"].max()
        return df[df["predicted_at"] == latest].sort_index()
    except Exception:
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# PLOTLY TRACE
# ════════════════════════════════════════════════════════════════

def prediction_trace(df_pred):
    import plotly.graph_objects as go
    return go.Scatter(
        x=df_pred.index,
        y=df_pred["ace_pred_MWh"],
        mode="lines",
        name="Predikce ACE",
        line=dict(color="#FF6F00", width=2, dash="dot"),
        hovertemplate=(
            "<b>Predikce ACE</b><br>"
            "%{x|%a %d.%m %H:%M}<br>"
            "<b>%{y:+.1f} MWh</b>"
            "<extra></extra>"
        ),
        showlegend=True,
    )
