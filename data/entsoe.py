from functools import lru_cache

import pandas as pd
import streamlit as st
from entsoe import EntsoePandasClient

from config import ENTSOE_TOKEN
from data.store import load_snapshot

# ╔══════════════════════════════════════════════════════════════╗
# ║  Dvě vrstvy:                                                  ║
# ║   *_live()  → reálné API volání, spouští CRON                 ║
# ║   fetch_*() → čtení snapshotu (fallback na live), volá APP    ║
# ╚══════════════════════════════════════════════════════════════╝


@lru_cache(maxsize=1)
def _get_client():
    return EntsoePandasClient(api_key=ENTSOE_TOKEN)


client = _get_client()


# ── LIVE (cron) ──────────────────────────────────────────────────
def fetch_entsoe_data_live():
    now        = pd.Timestamp.now(tz="Europe/Prague")
    start_day  = now.normalize()
    end_imbal  = now + pd.Timedelta(hours=1)
    end_load   = start_day + pd.Timedelta(days=2)
    end_out    = start_day + pd.Timedelta(days=7)

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


def fetch_dap_live(day_offset: int = 0):
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


def fetch_installed_capacity_live():
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    end   = start + pd.Timedelta(days=1)
    try:
        raw = client.query_installed_generation_capacity(
            country_code="CZ", start=start, end=end, psr_type=None
        )
        if raw is None or raw.empty:
            return pd.Series(dtype=float)
        last = raw.iloc[-1]
        return last.dropna()
    except Exception:
        return pd.Series(dtype=float)


def fetch_activation_prices_live():
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    end   = now + pd.Timedelta(hours=1)
    try:
        raw = client.query_activated_balancing_energy_prices(
            country_code="CZ", start=start, end=end
        )
        if raw is None or raw.empty:
            return pd.DataFrame()
        df_act = raw.pivot_table(
            index=raw.index, columns=["ReserveType", "Direction"], values="Price"
        )
        if df_act.index.tz is None:
            df_act.index = df_act.index.tz_localize("UTC").tz_convert("Europe/Prague")
        else:
            df_act.index = df_act.index.tz_convert("Europe/Prague")
        return df_act
    except Exception:
        return pd.DataFrame()


def fetch_wind_solar_forecast_live():
    now       = pd.Timestamp.now(tz="Europe/Prague")
    start_day = now.normalize()
    end_day   = start_day + pd.Timedelta(days=2)
    try:
        raw = client.query_wind_and_solar_forecast(
            country_code="CZ", start=start_day, end=end_day, psr_type=None
        )
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs("Actual Aggregated", level=1, axis=1, drop_level=True) \
                if "Actual Aggregated" in raw.columns.get_level_values(1) \
                else raw.xs(raw.columns.get_level_values(1)[0], level=1, axis=1, drop_level=True)
        if raw.index.tz is None:
            raw.index = raw.index.tz_localize("UTC").tz_convert("Europe/Prague")
        else:
            raw.index = raw.index.tz_convert("Europe/Prague")
        return raw
    except Exception:
        return pd.DataFrame()


def fetch_reserves_live():
    now      = pd.Timestamp.now(tz="Europe/Prague")
    start    = now.normalize()
    end      = now.normalize() + pd.Timedelta(days=10)
    start_yr = pd.Timestamp(f"{now.year}-01-01", tz="Europe/Prague")
    end_yr   = pd.Timestamp(f"{now.year}-07-01", tz="Europe/Prague")
    if now.month >= 7:
        start_yr = pd.Timestamp(f"{now.year}-07-01", tz="Europe/Prague")
        end_yr   = pd.Timestamp(f"{now.year+1}-01-01", tz="Europe/Prague")

    def _q(fn, pt, ma, s, e):
        try:
            return fn(country_code="CZ", start=s, end=e,
                      process_type=pt, type_marketagreement_type=ma)
        except Exception:
            return pd.DataFrame()

    return dict(
        afrr_d_amt = _q(client.query_contracted_reserve_amount, "A51", "A01", start, end),
        afrr_d_pri = _q(client.query_contracted_reserve_prices,  "A51", "A01", start, end),
        afrr_y_amt = _q(client.query_contracted_reserve_amount, "A51", "A04", start_yr, end_yr),
        afrr_y_pri = _q(client.query_contracted_reserve_prices,  "A51", "A04", start_yr, end_yr),
        mfrr_d_amt = _q(client.query_contracted_reserve_amount, "A52", "A01", start, end),
        mfrr_d_pri = _q(client.query_contracted_reserve_prices,  "A52", "A01", start, end),
        now=now, start=start, end=end,
    )


# ── READER (app) — čte snapshot, fallback na live ────────────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_entsoe_data():
    snap = load_snapshot("entsoe_data")
    return snap if snap is not None else fetch_entsoe_data_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_dap(day_offset: int = 0):
    snap = load_snapshot(f"dap_{day_offset}")
    return snap if snap is not None else fetch_dap_live(day_offset)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_installed_capacity():
    snap = load_snapshot("installed_capacity")
    return snap if snap is not None else fetch_installed_capacity_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_activation_prices():
    snap = load_snapshot("activation_prices")
    return snap if snap is not None else fetch_activation_prices_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_wind_solar_forecast():
    snap = load_snapshot("wind_solar_forecast")
    return snap if snap is not None else fetch_wind_solar_forecast_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_reserves():
    snap = load_snapshot("reserves")
    return snap if snap is not None else fetch_reserves_live()
