import requests
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import os
import time

HISTORY_START  = date(2020, 1, 1)
PARQUET_PATH   = "data/history/entsog_all_flows.parquet"
GIE_CSV_PATH   = "data/history/gie_cz_storage.csv"
GIE_KEY        = "628043ec28b2f2395a95f5adad7ec983"

KEEP_COLS = [
    "periodFrom", "countryKey", "countryLabel",
    "directionKey", "adjacentSystemsKey", "adjacentSystemsLabel",
    "pointsNames", "value", "unit", "flowStatus",
]


def fetch_all_pages(from_date: date, to_date: date) -> pd.DataFrame:
    """Stáhne všechny stránky pro dané období — všechny země."""
    all_rows = []
    offset   = 0
    limit    = 2000

    while True:
        url = (
            "https://transparency.entsog.eu/api/v1/aggregateddata"
            f"?from={from_date}&to={to_date}"
            "&indicator=Physical%20Flow&periodType=day"
            f"&timezone=CET&limit={limit}&offset={offset}&format=json"
        )
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code} pro {from_date}–{to_date}, přeskakuji")
                break
            data  = resp.json()
            rows  = data.get("aggregateddata", [])
            total = data.get("meta", {}).get("total", 0)
            all_rows.extend(rows)
            offset += len(rows)
            if offset >= total or len(rows) == 0:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"    Chyba: {e}")
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df[[c for c in KEEP_COLS if c in df.columns]]
    df["value_GWh"] = pd.to_numeric(df["value"], errors="coerce") / 1_000_000
    df["date"] = pd.to_datetime(
        df["periodFrom"], utc=True
    ).dt.tz_convert("Europe/Prague").dt.normalize()
    return df.drop(columns=["value", "periodFrom"], errors="ignore")


def update_entsog():
    os.makedirs("data/history", exist_ok=True)

    if os.path.exists(PARQUET_PATH):
        existing  = pd.read_parquet(PARQUET_PATH)
        last_date = pd.to_datetime(existing["date"]).max().date()
        start     = last_date - timedelta(days=7)
        print(f"Existující data do {last_date}, stahuji od {start}")
    else:
        existing = pd.DataFrame()
        start    = HISTORY_START
        print(f"Nový soubor, stahuji od {start}")

    frames  = []
    current = start
    today   = date.today()

    while current <= today:
        end = min(current + timedelta(days=6), today)
        print(f"  {current} → {end} ...")
        df_week = fetch_all_pages(current, end)
        if not df_week.empty:
            frames.append(df_week)
        current = end + timedelta(days=1)
        time.sleep(0.5)

    if not frames:
        print("Žádná nová data.")
        return

    new_data = pd.concat(frames, ignore_index=True)

    if not existing.empty:
        combined = pd.concat([existing, new_data], ignore_index=True)
        key_cols = ["date", "countryKey", "directionKey",
                    "adjacentSystemsKey", "pointsNames"]
        key_cols = [c for c in key_cols if c in combined.columns]
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
    else:
        combined = new_data

    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_parquet(PARQUET_PATH, index=False)
    size_mb  = os.path.getsize(PARQUET_PATH) / 1024 / 1024
    print(f"Uloženo {len(combined)} řádků → {PARQUET_PATH} ({size_mb:.1f} MB)")


def fetch_gie_month(start: date, end: date) -> pd.DataFrame:
    url     = f"https://agsi.gie.eu/api?country=CZ&size=300&from={start}&to={end}"
    headers = {"x-key": GIE_KEY}
    try:
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
                    df[col].astype(str).str.replace(",", "."),
                    errors="coerce"
                )
        keep = [c for c in
                ["gasInStorage","injection","withdrawal",
                 "workingGasVolume","full","status"]
                if c in df.columns]
        return df.set_index("gasDayStart")[keep]
    except Exception as e:
        print(f"GIE chyba: {e}")
        return pd.DataFrame()


def update_gie():
    os.makedirs("data/history", exist_ok=True)

    if os.path.exists(GIE_CSV_PATH):
        existing  = pd.read_csv(GIE_CSV_PATH, index_col=0, parse_dates=True)
        last_date = existing.index[-1].date()
        start     = last_date - timedelta(days=7)
        print(f"GIE existující data do {last_date}, stahuji od {start}")
    else:
        existing = pd.DataFrame()
        start    = HISTORY_START
        print(f"GIE nový soubor, stahuji od {start}")

    frames  = []
    current = start
    today   = date.today()

    while current <= today:
        end = min(
            date(current.year, current.month, 1)
            + relativedelta(months=1) - timedelta(days=1),
            today
        )
        print(f"  GIE {current} → {end} ...")
        df_m = fetch_gie_month(current, end)
        if not df_m.empty:
            frames.append(df_m)
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
    size_kb = os.path.getsize(GIE_CSV_PATH) / 1024
    print(f"GIE uloženo {len(combined)} řádků → {GIE_CSV_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    print("=== ENTSO-G flows (všechny země) ===")
    update_entsog()
    print("\n=== GIE storage CZ ===")
    update_gie()
