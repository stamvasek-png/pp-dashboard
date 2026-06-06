"""
Snapshot store — odděluje stahování dat (cron) od jejich zobrazení (app).

Cron skript (scripts/refresh_data.py) volá *_live() funkce, které sáhnou na
API, a výsledek uloží přes save_snapshot(). Streamlit aplikace pak čte hotová
data přes load_snapshot() — žádná živá API volání na hot-path = rychlé UI.

Pickle je tu zvolen schválně: zachová přesné Python objekty (tuple/dict
DataFramů, MultiIndex sloupce, tz-aware indexy) bez serializační logiky.
Cron i app běží ve stejném Docker image (stejná verze pandas), takže je to
bezpečné.
"""
import os
import pickle
import tempfile
import time

SNAP_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


def _path(name: str) -> str:
    return os.path.join(SNAP_DIR, f"{name}.pkl")


def save_snapshot(name: str, obj) -> None:
    """Atomicky uloží objekt do data/snapshots/{name}.pkl."""
    os.makedirs(SNAP_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SNAP_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _path(name))  # atomický přesun
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_snapshot(name: str, default=None):
    """Načte snapshot; vrátí `default`, pokud soubor neexistuje nebo je vadný."""
    path = _path(name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return default


def snapshot_age(name: str):
    """Stáří snapshotu v sekundách, nebo None když neexistuje."""
    path = _path(name)
    if not os.path.exists(path):
        return None
    return time.time() - os.path.getmtime(path)


def has_snapshot(name: str) -> bool:
    return os.path.exists(_path(name))
