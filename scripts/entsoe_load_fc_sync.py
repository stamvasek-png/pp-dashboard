# Cron: 1x denně ve 23:00 CEST — prognóza zatížení D+1 z ENTSO-E → SQLite
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import ENTSOE_TOKEN
from entsoe import EntsoePandasClient

TZ      = ZoneInfo("Europe/Prague")
DB_PATH = Path(__file__).parent.parent / "data" / "ceps_odchylka.db"


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entsoe_load_forecast (
                cas         TEXT PRIMARY KEY,
                forecast_mw REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lfc_cas ON entsoe_load_forecast (cas)")


def fetch_and_store() -> int:
    client    = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    now       = pd.Timestamp.now(tz="Europe/Prague")
    start_day = now.normalize()
    end_day   = start_day + pd.Timedelta(days=2)

    fc = client.query_load_forecast("CZ", start=start_day, end=end_day)
    if isinstance(fc, pd.DataFrame):
        fc = fc.iloc[:, 0]
    fc = fc.dropna()

    rows = [(ts.isoformat(), float(val)) for ts, val in fc.items()]
    if not rows:
        return 0

    with _db() as conn:
        cursor = conn.executemany(
            "INSERT OR REPLACE INTO entsoe_load_forecast (cas, forecast_mw) VALUES (?, ?)",
            rows,
        )
    return cursor.rowcount


if __name__ == "__main__":
    init_db()
    new = fetch_and_store()
    print(f"entsoe_load_fc_sync: {new} řádků uloženo — {datetime.now(TZ):%Y-%m-%d %H:%M:%S}")
