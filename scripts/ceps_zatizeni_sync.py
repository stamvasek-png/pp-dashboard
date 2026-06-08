# Cron: každých 5 min — zatížení skutečnost z ČEPS SOAP → SQLite
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from zeep import Client as SoapClient

NS   = "https://www.ceps.cz/CepsData/StructuredData/1.0"
WSDL = "https://www.ceps.cz/_layouts/CepsData.asmx?WSDL"
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
            CREATE TABLE IF NOT EXISTS ceps_zatizeni (
                cas        TEXT PRIMARY KEY,
                zatizeni_mw REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_zat_cas ON ceps_zatizeni (cas)")


def fetch_and_store() -> int:
    ceps  = SoapClient(wsdl=WSDL)
    now   = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = ceps.service.Load(
        dateFrom  =today.replace(tzinfo=None),
        dateTo    =now.replace(tzinfo=None),
        agregation="MI",
        function  ="AVG",
        version   ="RT",
    )

    rows = []
    # Najdi sloupec zatížení (value1 nebo value2)
    series = {}
    for s in result.findall(f"{{{NS}}}series/{{{NS}}}serie"):
        series[s.get("id")] = s.get("name", "")

    load_key = next(
        (k for k, v in series.items() if "pumping" in v.lower() or "load" in v.lower()),
        "value1"
    )

    for item in result.findall(f"{{{NS}}}data/{{{NS}}}item"):
        val = item.get(load_key)
        if val is None:
            continue
        import pandas as pd
        cas = pd.Timestamp(item.get("date")).tz_convert(TZ).isoformat()
        rows.append((cas, float(val)))

    if not rows:
        return 0

    with _db() as conn:
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO ceps_zatizeni (cas, zatizeni_mw) VALUES (?, ?)",
            rows,
        )
    return cursor.rowcount


if __name__ == "__main__":
    init_db()
    new = fetch_and_store()
    print(f"ceps_zatizeni_sync: {new} řádků uloženo — {datetime.now(TZ):%Y-%m-%d %H:%M:%S}")
