import os
import requests
import pandas as pd
import streamlit as st
from datetime import date, timedelta

POINTS_CONFIG = {
    "Brandov/Waidhaus (DE)": {"lat": 50.608, "lon": 13.388, "flag": "🇩🇪"},
    "Lanžhot (SK)":          {"lat": 48.722, "lon": 17.044, "flag": "🇸🇰"},
    "Český Těšín (PL)":      {"lat": 49.748, "lon": 18.622, "flag": "🇵🇱"},
    "Zásobníky":             {"lat": 49.750, "lon": 15.800, "flag": "🏭"},
    "Distribuce":            {"lat": 49.400, "lon": 16.200, "flag": "🔵"},
    "Koneční spotřebitelé":  {"lat": 49.100, "lon": 15.500, "flag": "🏠"},
}

def _short_name(s: str) -> str:
    if "Brandov" in s or "Waidhaus" in s: return "Brandov/Waidhaus (DE)"
    if "Lanžhot" in s:                     return "Lanžhot (SK)"
    if "Těšín"   in s or "Cieszyn" in s:   return "Český Těšín (PL)"
    if "Storage" in s or "VGS" in s:       return "Zásobníky"
    if "Distribution" in s:                return "Distribuce"
    if "Final" in s:                        return "Koneční spotřebitelé"
    return s[:35]

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_entsog_flows(days: int = 90) -> pd.DataFrame:
    end   = date.today()
    start = end - timedelta(days=days)
    url = (
        "https://transparency.entsog.eu/api/v1/aggregateddata"
        f"?from={start}&to={end}"
        "&indicator=Physical%20Flow&periodType=day"
        "&timezone=CET&limit=10000&format=json&countryKey=CZ"
    )
    try:
        resp = requests.get(url, timeout=30)
        df   = pd.DataFrame(resp.json()["aggregateddata"])
        df["value_GWh"] = pd.to_numeric(df["value"], errors="coerce") / 1_000_000
        df["date"]      = pd.to_datetime(df["periodFrom"], utc=True).dt.tz_convert("Europe/Prague").dt.date
        df["point"]     = df["pointsNames"].apply(_short_name)
        entry = df[df["directionKey"]=="entry"].groupby(["date","point"])["value_GWh"].sum()
        exit_ = df[df["directionKey"]=="exit" ].groupby(["date","point"])["value_GWh"].sum()
        pivot = (entry.unstack(fill_value=0) - exit_.unstack(fill_value=0)).fillna(0)
        pivot.index = pd.to_datetime(pivot.index)
        for pt in POINTS_CONFIG:
            if pt not in pivot.columns:
                pivot[pt] = 0.0
        return pivot
    except Exception:
        return pd.DataFrame()


def load_entsog_history() -> pd.DataFrame:
    """
    Načte historická data z CSV (generovaného GitHub Actions).
    Fallback na live API (posledních 90 dní) pokud CSV neexistuje.
    """
    csv_path = "data/history/entsog_cz_flows.csv"
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("Europe/Prague")
        return df
    return fetch_entsog_flows(days=90)


def load_gie_history() -> pd.DataFrame:
    """
    Načte historická data zásobníků z CSV.
    """
    csv_path = "data/history/gie_cz_storage.csv"
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("Europe/Prague")
        return df
    return pd.DataFrame()
