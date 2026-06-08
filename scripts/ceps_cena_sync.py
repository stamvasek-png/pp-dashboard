# Cron: každých 10 min — cena odchylky z ČEPS SOAP → SQLite
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from zeep import Client as SoapClient

WSDL = "https://www.ceps.cz/_layouts/CepsData.asmx?WSDL"
NS   = "https://www.ceps.cz/CepsData/StructuredData/1.0"
TZ   = ZoneInfo("Europe/Prague")
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
            CREATE TABLE IF NOT EXISTS ceps_cena_odchylky (
                cas         TEXT PRIMARY KEY,
                cena_czk_mwh REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cena_cas ON ceps_cena_odchylky (cas)")


def fetch_and_store() -> int:
    ceps  = SoapClient(wsdl=WSDL)
    now   = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = ceps.service.OdhadovanaCenaOdchylky(
        dateFrom=today.replace(tzinfo=None),
        dateTo=now.replace(tzinfo=None),
    )

    rows = []
    for item in result.findall(f"{{{NS}}}data/{{{NS}}}item"):
        interval = item.get("value15", "")
        price    = float(item.get("value2", 0) or 0)
        if not interval or price == 0:
            continue
        hh, mm = interval.split("-")[0].split(":")
        cas = today + pd.Timedelta(hours=int(hh), minutes=int(mm))
        rows.append((cas.isoformat(), price))

    if not rows:
        return 0

    with _db() as conn:
        cursor = conn.executemany(
            "INSERT OR REPLACE INTO ceps_cena_odchylky (cas, cena_czk_mwh) VALUES (?, ?)",
            rows,
        )
    return cursor.rowcount


if __name__ == "__main__":
    init_db()
    new = fetch_and_store()
    print(f"ceps_cena_sync: {new} řádků uloženo — {datetime.now(TZ):%Y-%m-%d %H:%M:%S}")
