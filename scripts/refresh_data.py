#!/usr/bin/env python3
"""
Cron entrypoint — stáhne všechna live data z API a uloží je jako snapshoty
do data/snapshots/. Streamlit aplikace pak čte jen tyhle snapshoty, takže
v UI neprobíhají žádná pomalá API volání.

Spuštění:
    python scripts/refresh_data.py

Doporučená frekvence cronu: každou 1–5 minut (viz DEPLOY.md).
"""
import os
import sys
import time
import traceback

# Umožní `from data... import` i `from config import` při spuštění skriptu
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.store import save_snapshot
from data.entsoe import (
    fetch_entsoe_data_live, fetch_dap_live, fetch_installed_capacity_live,
    fetch_activation_prices_live, fetch_wind_solar_forecast_live, fetch_reserves_live,
)
from data.ceps import (
    fetch_ceps_imbalance_live, fetch_ceps_svr_live,
    fetch_ceps_imbalance_price_live, fetch_ceps_all_live,
)
from data.entsog import fetch_entsog_flows_live


def _run(name, fn, *args, **kwargs):
    """Spustí jeden fetch a uloží snapshot; chybu zaloguje, ale nepřeruší běh."""
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        save_snapshot(name, result)
        print(f"  ✓ {name:<22} ({time.time() - t0:5.1f}s)")
        return True
    except Exception:
        print(f"  ✗ {name:<22} CHYBA:")
        traceback.print_exc()
        return False


def main():
    print(f"── refresh_data {time.strftime('%Y-%m-%d %H:%M:%S')} ──")
    tasks = [
        ("entsoe_data",          fetch_entsoe_data_live),
        ("dap_0",                lambda: fetch_dap_live(0)),
        ("dap_1",                lambda: fetch_dap_live(1)),
        ("installed_capacity",   fetch_installed_capacity_live),
        ("activation_prices",    fetch_activation_prices_live),
        ("wind_solar_forecast",  fetch_wind_solar_forecast_live),
        ("reserves",             fetch_reserves_live),
        ("ceps_imbalance",       fetch_ceps_imbalance_live),
        ("ceps_svr",             fetch_ceps_svr_live),
        ("ceps_imbalance_price", fetch_ceps_imbalance_price_live),
        ("ceps_all",             fetch_ceps_all_live),
        ("entsog_flows",         lambda: fetch_entsog_flows_live(90)),
    ]

    ok = sum(_run(name, fn) for name, fn in tasks)
    print(f"── hotovo: {ok}/{len(tasks)} snapshotů ──")
    # Nenulový exit kód jen když selhalo úplně všechno
    sys.exit(0 if ok > 0 else 1)


if __name__ == "__main__":
    main()
