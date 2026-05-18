import requests
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import os

HISTORY_START = date(2020, 1, 1)
CSV_PATH      = "data/history/entsog_cz_flows.csv"
GIE_KEY       = "628043ec28b2f2395a95f5adad7ec983"
GIE_CSV_PATH  = "data/history/gie_cz_storage.csv"

def short_name(s: str) -> str:
    if "Brandov" in s or "Waidhaus" in s: return "Brandov/Waidhaus (DE)"
    if "Lanžhot" in s:                     return "Lanžhot (SK)"
    if "Těšín"   in s or "Cieszyn" in s:   return "Český Těšín (PL)"
    if "Storage" in s or "VGS" in s:       return "Zásobníky"
    if "Distribution" in s:                return "Distribuce"
    if "Final" in s:                        return "Koneční spotřebitelé"
    return s[:35]


def fetch_entsog_month(start: date, end: date) -> pd.DataFrame:
    url = (
        "https://transparency.entsog.eu/api/v1/aggregateddata"
        f"?from={start}&to={end}"
        "&indicator=Physical%20Flow&periodType=day"
        "&timezone=CET&limit=1000&format=json&countryKey=CZ"
    )
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        return pd.DataFrame()
    data = resp.json().get("aggregateddata", [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["value_GWh"] = pd.to_numeric(df["value"], errors="coerce") / 1_000_000
    df["date"]      = pd.to_datetime(df["periodFrom"], utc=True).dt.tz_convert("Europe/Prague").dt.date
    df["point"]     = df["pointsNames"].apply(short_name)
    entry = df[df["directionKey"]=="entry"].groupby(["date","point"])["value_GWh"].sum()
    exit_ = df[df["directionKey"]=="exit" ].groupby(["date","point"])["value_GWh"].sum()
    pivot = (entry.unstack(fill_value=0) - exit_.unstack(fill_value=0)).fillna(0)
    pivot.index = pd.to_datetime(pivot.index)
    return pivot


def update_entsog():
    os.makedirs("data/history", exist_ok=True)

    if os.path.exists(CSV_PATH):
        existing = pd.read_csv(CSV_PATH, index_col=0, parse_dates=True)
        last_date = existing.index[-1].date()
        start = last_date - timedelta(days=7)
        print(f"Existující data do {last_date}, stahuji od {start}")
    else:
        existing = pd.DataFrame()
        start = HISTORY_START
        print(f"Nový soubor, stahuji od {start}")

    frames = []
    current = start
    today   = date.today()
    while current <= today:
        end = min(
            date(current.year, current.month, 1) + relativedelta(months=1) - timedelta(days=1),
            today
        )
        print(f"  Stahuji {current} → {end} ...")
        df_month = fetch_entsog_month(current, end)
        if not df_month.empty:
            frames.append(df_month)
        current = end + timedelta(days=1)

    if not frames:
        print("Žádná nová data.")
        return

    new_data = pd.concat(frames)
    new_data = new_data[~new_data.index.duplicated(keep="last")]

    if not existing.empty:
        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    else:
        combined = new_data.sort_index()

    combined.to_csv(CSV_PATH)
    print(f"Uloženo {len(combined)} řádků do {CSV_PATH}")


def fetch_gie_month(start: date, end: date) -> pd.DataFrame:
    url = (
        f"https://agsi.gie.eu/api?country=CZ&size=300"
        f"&from={start}&to={end}"
    )
    headers = {"x-key": GIE_KEY}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return pd.DataFrame()
    data = resp.json().get("data", [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])
    for col in ["gasInStorage","injection","withdrawal","workingGasVolume","full"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",","."), errors="coerce"
            )
    return df.set_index("gasDayStart")[
        [c for c in ["gasInStorage","injection","withdrawal","workingGasVolume","full","status"]
         if c in df.columns]
    ]


def update_gie():
    os.makedirs("data/history", exist_ok=True)

    if os.path.exists(GIE_CSV_PATH):
        existing = pd.read_csv(GIE_CSV_PATH, index_col=0, parse_dates=True)
        last_date = existing.index[-1].date()
        start = last_date - timedelta(days=7)
        print(f"GIE existující data do {last_date}, stahuji od {start}")
    else:
        existing = pd.DataFrame()
        start = HISTORY_START
        print(f"GIE nový soubor, stahuji od {start}")

    frames = []
    current = start
    today   = date.today()
    while current <= today:
        end = min(
            date(current.year, current.month, 1) + relativedelta(months=1) - timedelta(days=1),
            today
        )
        print(f"  GIE {current} → {end} ...")
        df_month = fetch_gie_month(current, end)
        if not df_month.empty:
            frames.append(df_month)
        current = end + timedelta(days=1)

    if not frames:
        print("GIE: žádná nová data.")
        return

    new_data = pd.concat(frames)
    new_data = new_data[~new_data.index.duplicated(keep="last")]

    if not existing.empty:
        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    else:
        combined = new_data.sort_index()

    combined.to_csv(GIE_CSV_PATH)
    print(f"GIE uloženo {len(combined)} řádků do {GIE_CSV_PATH}")


if __name__ == "__main__":
    print("=== ENTSO-G flows ===")
    update_entsog()
    print("\n=== GIE storage ===")
    update_gie()
