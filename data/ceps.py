from functools import lru_cache

import pandas as pd
import streamlit as st
from zeep import Client as SoapClient

from data.store import load_snapshot

CEPS_WSDL = "https://www.ceps.cz/_layouts/CepsData.asmx?WSDL"
CEPS_NS   = "https://www.ceps.cz/CepsData/StructuredData/1.0"

# ╔══════════════════════════════════════════════════════════════╗
# ║   *_live()  → reálné SOAP volání, spouští CRON               ║
# ║   fetch_*() → čtení snapshotu (fallback na live), volá APP   ║
# ╚══════════════════════════════════════════════════════════════╝


@lru_cache(maxsize=1)
def _get_ceps_client():
    return SoapClient(wsdl=CEPS_WSDL)


ceps = _get_ceps_client()


def _parse_ceps(result) -> pd.DataFrame:
    series = {}
    for s in result.findall(f"{{{CEPS_NS}}}series/{{{CEPS_NS}}}serie"):
        series[s.get("id")] = s.get("name")
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        row = {"time": pd.Timestamp(item.get("date")).tz_convert("Europe/Prague")}
        for vid, name in series.items():
            val = item.get(vid)
            row[name] = float(val) if val is not None else 0.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


# ── LIVE (cron) ──────────────────────────────────────────────────
def fetch_ceps_imbalance_live():
    """Systémová odchylka ČR z ČEPS — minutová, ~5min zpoždění."""
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    try:
        result = ceps.service.AktualniSystemovaOdchylkaCR(
            dateFrom  =start.to_pydatetime().replace(tzinfo=None),
            dateTo    =now.to_pydatetime().replace(tzinfo=None),
            agregation="MI",
            function  ="AVG",
        )
        rows = []
        for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
            rows.append({
                "time":        pd.Timestamp(item.get("date")).tz_convert("Europe/Prague"),
                "odchylka_MW": float(item.get("value1", 0)),
            })
        df = pd.DataFrame(rows).set_index("time")
        return df, now
    except Exception:
        return pd.DataFrame(), now


def fetch_ceps_svr_live():
    """Aktivace SVR v ČR z ČEPS — minutová, ~5min zpoždění."""
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    SERIES = {
        "value1": "aFRR+ [MW]",
        "value2": "aFRR- [MW]",
        "value3": "mFRR+ [MW]",
        "value4": "mFRR- [MW]",
        "value7": "mFRR5 [MW]",
    }
    try:
        result = ceps.service.AktivaceSVRvCR(
            dateFrom  =start.to_pydatetime().replace(tzinfo=None),
            dateTo    =now.to_pydatetime().replace(tzinfo=None),
            agregation="MI",
            function  ="AVG",
            param1    ="all",
        )
        rows = []
        for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
            row = {"time": pd.Timestamp(item.get("date")).tz_convert("Europe/Prague")}
            for vid, name in SERIES.items():
                row[name] = float(item.get(vid, 0))
            rows.append(row)
        return pd.DataFrame(rows).set_index("time")
    except Exception:
        return pd.DataFrame()


def fetch_ceps_imbalance_price_live():
    """Odhadovaná cena odchylky z ČEPS — 15min, CZK/MWh."""
    now   = pd.Timestamp.now(tz="Europe/Prague")
    today = now.normalize()
    try:
        result = ceps.service.OdhadovanaCenaOdchylky(
            dateFrom=today.to_pydatetime().replace(tzinfo=None),
            dateTo  =now.to_pydatetime().replace(tzinfo=None),
        )
        rows = []
        for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
            interval = item.get("value15", "")
            price    = float(item.get("value2", 0))
            if not interval or price == 0:
                continue
            hh, mm = interval.split("-")[0].split(":")
            ts = today + pd.Timedelta(hours=int(hh), minutes=int(mm))
            rows.append({"time": ts, "cena_CZK_MWh": price})
        df = pd.DataFrame(rows).set_index("time")
        return df
    except Exception:
        return pd.DataFrame()


def fetch_ceps_all_live():
    """Stáhne všechna ČEPS data najednou — jeden snapshot."""
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()

    def call(method, **kw):
        fn = getattr(ceps.service, method)
        return fn(
            dateFrom=start.to_pydatetime().replace(tzinfo=None),
            dateTo  =now.to_pydatetime().replace(tzinfo=None),
            **kw
        )

    def safe(method, **kw):
        try:    return _parse_ceps(call(method, **kw))
        except: return pd.DataFrame()

    df_imbal = safe("AktualniSystemovaOdchylkaCR", agregation="MI", function="AVG")
    df_svr   = safe("AktivaceSVRvCR", agregation="MI", function="AVG", param1="all")
    df_load  = safe("Load", agregation="MI", function="AVG", version="RT")
    df_gen   = safe("Generation", agregation="QH", function="AVG", version="RT", para1="all")
    df_res   = safe("GenerationRES", agregation="MI", function="AVG", version="RT", para1="all")
    df_freq  = safe("Frekvence")
    df_cb    = safe("CrossborderPowerFlows", agregation="MI", function="AVG", version="RT")

    df_cena = pd.DataFrame()
    try:
        r_cena = call("OdhadovanaCenaOdchylky")
        rows_c = []
        for item in r_cena.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
            interval = item.get("value15", "")
            price    = float(item.get("value2", 0) or 0)
            if not interval or price == 0:
                continue
            hh, mm = interval.split("-")[0].split(":")
            ts = start + pd.Timedelta(hours=int(hh), minutes=int(mm))
            rows_c.append({"time": ts, "Cena odchylky (CZK/MWh)": price})
        if rows_c:
            df_cena = pd.DataFrame(rows_c).set_index("time")
    except Exception:
        pass

    if not df_cb.empty:
        actual_cols = [c for c in df_cb.columns if "Actual" in c]
        if actual_cols:
            df_cb["Net Export (MW)"] = df_cb[actual_cols].sum(axis=1)

    return {
        "imbal": df_imbal, "svr": df_svr, "load": df_load,
        "gen": df_gen, "res": df_res, "freq": df_freq,
        "cb": df_cb, "cena": df_cena, "now": now,
    }


# ── READER (app) — čte snapshot, fallback na live ────────────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_ceps_imbalance():
    snap = load_snapshot("ceps_imbalance")
    return snap if snap is not None else fetch_ceps_imbalance_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ceps_svr():
    snap = load_snapshot("ceps_svr")
    return snap if snap is not None else fetch_ceps_svr_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ceps_imbalance_price():
    snap = load_snapshot("ceps_imbalance_price")
    return snap if snap is not None else fetch_ceps_imbalance_price_live()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ceps_all():
    snap = load_snapshot("ceps_all")
    return snap if snap is not None else fetch_ceps_all_live()
