# Intraday model SO – deployment dokumentace

Tento dokument je určen pro nasazení intraday predikčního modelu systémové odchylky (SO) do produkce.

---

## Co model dělá

Predikuje systémovou odchylku CZ (SO) v MW pro 4 budoucí 15minutové intervaly:
- T+1 = nejbližší budoucí interval (15 min dopředu)
- T+2 = 30 min dopředu
- T+3 = 45 min dopředu
- T+4 = 60 min dopředu

Pro každý horizont vrací:
- `so_pred_MW` – predikovaná SO v MW
- `so_pred_MWh` – predikovaná SO v MWh (= so_pred_MW × 0.25)
- `prob_nabij` – pravděpodobnost záporné SO (< -50 MW)
- `prob_neutral` – pravděpodobnost neutrální SO
- `prob_vybij` – pravděpodobnost kladné SO (> +50 MW)

---

## Architektura modelu

- **Typ:** XGBoost (regressor + klasifikátor)
- **Počet modelů:** 4 horizonty × 96 intervalů × 2 typy = 768 modelů
- **Soubory:** `reg_h{1-4}_i{00-95}.json` (regressor), `clf_h{1-4}_i{00-95}.json` (klasifikátor)
- **Feature set:** 181 features (viz `feature_cols.json`)
- **Trénovací období:** 2023-02-01 až 2026-05-26
- **Test set MAE:** T+1=44.7 MW, T+2=49.3 MW, T+3=51.2 MW, T+4=52.7 MW
- **Test set Acc:** T+1=62.1%, T+2=57.6%, T+3=54.6%, T+4=52.8%
- **Baseline MAE:** 63.6 MW (průměr trénovacího setu)

---

## Vstupní data

Data jsou stahována samostatným cronem a ukládána jako parquet soubory.
Skript **nesmí stahovat data sám** – pouze čte z těchto souborů.

### Potřebné soubory a jejich obsah

| Soubor | Obsah | Frekvence | Zpoždění |
|---|---|---|---|
| `so_min.parquet` | Systémová odchylka CZ [MW], minutová | 1 min | ~5 min |
| `svr_min.parquet` | Aktivace SVR (aFRR up/dn, mFRR up/dn) [MW], minutová | 1 min | ~5 min |
| `frekvence.parquet` | Frekvence sítě [Hz], minutová | 1 min | ~5 min |
| `ceny_re_min.parquet` | Cena aFRR [EUR/MWh], minutová | 1 min | ~5 min |
| `entsoe_dap.parquet` | Day-ahead cena elektřiny CZ [EUR/MWh], 15min | 1h | DA |
| `entsoe_wind_solar_cz.parquet` | Wind+Solar forecast CZ [MW], 15min | 1h | DA |
| `entsoe_wind_solar_de.parquet` | Wind+Solar forecast DE [MW], 15min | 1h | DA |
| `entsoe_load_fc.parquet` | Load forecast CZ [MW], 15min | 1h | DA |
| `entsoe_de_load_fc.parquet` | Load forecast DE [MW], 15min | 1h | DA |
| `entsoe_exchanges.parquet` | Scheduled exchanges CZ/DE/SK/AT/PL [MW], 15min | 1h | DA |
| `entsoe_de_imbalance.parquet` | DE imbalance prices [EUR/MWh], 15min | 1h | D-1 |
| `ag2_hist_{STANICE}.parquet` | Historická meteorologická data per stanice, hodinová | 1h | ~1h |

### AG2 meteorologické stanice (13)
CZ: LKPR, LKTB, LKMT, LKLN
DE: EDDC, EDDP, EDDM, EDDN
PL: EPWR, EPKT, EPWA, EPKK, EPGD

### Sloupce v parquet souborech

**so_min.parquet**
- index: datetime (timezone-aware, Europe/Prague)
- `SO_min_MW`: systémová odchylka [MW]

**svr_min.parquet**
- `afrr_up_min_MW`, `afrr_dn_min_MW`, `mfrr_up_min_MW`, `mfrr_dn_min_MW`

**frekvence.parquet**
- `freq_Hz`: frekvence sítě [Hz]

**ceny_re_min.parquet**
- `afrr_price_EUR`: cena aFRR [EUR/MWh]

**entsoe_dap.parquet**
- `dap_EUR_MWh`: DA cena [EUR/MWh]

**entsoe_wind_solar_cz.parquet**
- `entsoe_solar_fc_MW`, `entsoe_wind_fc_MW`

**entsoe_wind_solar_de.parquet**
- `de_solar_fc_MW`, `de_wind_fc_MW_1`, `de_wind_fc_MW_2` (nebo `de_wind_fc_total_MW`)

**entsoe_load_fc.parquet**
- `entsoe_load_fc_MW`

**entsoe_de_load_fc.parquet**
- `de_load_fc_MW`

**entsoe_exchanges.parquet**
- `sched_CZ_DE_MW`, `sched_DE_CZ_MW`, `sched_CZ_SK_MW`, `sched_SK_CZ_MW`
- `sched_CZ_AT_MW`, `sched_AT_CZ_MW`, `sched_CZ_PL_MW`, `sched_PL_CZ_MW`

**entsoe_de_imbalance.parquet**
- `de_imbal_long_EUR_MWh`, `de_imbal_short_EUR_MWh`

**ag2_hist_{STANICE}.parquet**
- `temp_C_{STANICE}`, `wind_ms_{STANICE}`, `cloud_pct_{STANICE}`
- `rh_pct_{STANICE}`, `dewpoint_C_{STANICE}`

---

## Feature engineering – klíčové detaily

### Produkce vs backtest
**DŮLEŽITÉ:** Model byl trénován s `shift(1)` na rolling features (backtest konvence).
V produkci se shift **nepoužívá** – používá se funkce `so_min_to_qh_prod()`.

### so_min_to_qh_prod()
```python
def so_min_to_qh_prod(df_min, col, min_minutes=8):
    """
    Převod minutových dat na 15min intervaly pro produkci.
    Neúplný interval je použit pokud má alespoň min_minutes minut dat.
    Intervaly s méně než min_minutes minutami = NaN.
    """
    counts = df_min[col].resample("15min").count()
    means  = df_min[col].resample("15min").mean()
    means[counts < min_minutes] = np.nan
    result = means.to_frame()
    result.index = result.index.tz_convert("Europe/Prague")
    return result[~result.index.duplicated(keep="last")].sort_index()
```

### Rolling features (bez shift v produkci)
- `so_last_15min` = průměr posledního QH intervalu
- `so_last_30min` = průměr posledních 2 QH intervalů
- `so_last_60min` = průměr posledních 4 QH intervalů
- `so_last_2h` = průměr posledních 8 QH intervalů
- `so_last_4h` = průměr posledních 16 QH intervalů
- `so_max_1h`, `so_min_1h`, `so_range_1h`
- `so_momentum_1`, `so_momentum_2`, `so_momentum_4`
- `so_rolling_90d_mean` (shift(1) se zachovává – jde o 90d průměr)

### AG2 data – posun -1h
AG2 historická data se publikují s ~1h zpožděním.
Při sestavení features použij `ts - 1h` jako index pro AG2 data:
```python
ts_hist = (ts - pd.Timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
```

### Časová konvence
- Veškerá data: `Europe/Prague` timezone
- ČEPS minutová data: period beginning (00:00 = data za 00:00-00:01)
- Resample na 15min: period beginning (00:00 = průměr 00:00-00:14)
- Predikované intervaly: period beginning konvence

---

## Spuštění

### Cron
```cron
5,20,35,50 * * * * /opt/pp-prediction/venv/bin/python /opt/pp-prediction/predict_id.py >> /opt/pp-prediction/logs/id.log 2>&1
```

Načasování :05, :20, :35, :50 = 5 minut po uzavření každého QH intervalu.
Minutová data mají ~5 min zpoždění – v :05 máš data do ~:00 atd.

### Vstup
- Adresář s parquet soubory (viz výše)
- Adresář s modely (`models/id/`)

### Výstup
Soubor `output/id_latest.json`:
```json
{
  "generated_at": "2026-06-09T10:35:12+02:00",
  "last_so_interval": "10:30",
  "last_so_value_MW": -45.2,
  "partial_minutes": 7,
  "predictions": [
    {
      "horizon": 1,
      "interval_start": "10:45",
      "interval_end": "11:00",
      "so_pred_MW": -38.1,
      "so_pred_MWh": -9.5,
      "so_direction": "nabij",
      "prob_nabij": 0.61,
      "prob_neutral": 0.28,
      "prob_vybij": 0.11
    }
  ]
}
```

---

## Kontroly při spuštění

| Kontrola | Limit | Akce |
|---|---|---|
| Lag SO dat | > 20 min | Varování do logu |
| Lag SVR dat | > 20 min | Varování do logu |
| Lag AG2 dat | > 90 min | Varování do logu |
| SO predikce | mimo ±500 MW | Chyba, nepsat výstup |
| Počet NaN features | > 30 | Varování do logu |

---


---

## Feature engineering – kompletní popis a kód

### Přehled skupin features (181 celkem)

| Skupina | Počet | Popis |
|---|---|---|
| Aktuální stav soustavy | 39 | Rolling features SO, SVR, frekvence, ceny RE |
| AG2 forecast | 11 | Hodinový forecast počasí (fc_ prefix) |
| SO historické | 19 | Průměry SO za posledních 7/14/30 dní per interval |
| Časové + pásma | 59 | Hodina, den, měsíc, cyklické encoding, pásma dne |
| Počasí AG2 (aktuální) | 21 | Teplota, vítr, oblačnost, vlhkost per stanice |
| Astronomické | 6 | Výška slunce, clearsky irradiance |
| DAP/ceny | 12 | Day-ahead cena, denní spread |
| OZE forecast | 4 | Wind+Solar CZ/DE forecast |
| Scheduled exchanges | 10 | Plánované toky CZ/DE/SK/AT/PL |
| Load forecast | 1 | ENTSO-E load forecast CZ |
| Kombinované _x_ | 35 | Interakce features × časová pásma |
| Trend + kapacita | 7 | Dlouhodobý trend SO, kapacita FVE |

---

### Kód – sestavení feature vektoru pro produkci

```python
import math
import numpy as np
import pandas as pd
import ephem
import holidays

# ── Inicializace ──────────────────────────────────────────────────────────────
cz_holidays_obj = holidays.CZ(years=range(2024, 2028))
de_holidays_obj = holidays.DE(years=range(2024, 2028))

observer = ephem.Observer()
observer.lat   = "50.08"
observer.lon   = "14.43"
observer.elev  = 202
observer.pressure = 0
sun_obj = ephem.Sun()

AG2_STATIONS = ["LKPR","LKTB","LKMT","LKLN",
                "EDDC","EDDP","EDDM","EDDN",
                "EPWR","EPKT","EPWA","EPKK","EPGD"]

def get_solar_params(ts):
    ts_utc = ts.tz_convert("UTC")
    observer.date = ts_utc.strftime("%Y/%m/%d %H:%M:%S")
    sun_obj.compute(observer)
    elevation_deg = float(sun_obj.alt) * 180 / math.pi
    if elevation_deg > 0:
        air_mass  = 1 / (math.sin(math.radians(elevation_deg)) +
                         0.50572 * (elevation_deg + 6.07995) ** -1.6364)
        air_mass  = min(air_mass, 38.0)
        doy       = ts_utc.timetuple().tm_yday
        et_irr    = 1361 * (1 + 0.033 * math.cos(2*math.pi*doy/365))
        clearsky  = et_irr * math.sin(math.radians(elevation_deg)) * (0.7**(air_mass**0.678))
        clearsky  = max(0, clearsky)
    else:
        clearsky = 0.0
    return elevation_deg, clearsky


# ── Produkční funkce pro převod minutových dat na QH ─────────────────────────
def so_min_to_qh_prod(df_min, col, min_minutes=8):
    """
    Produkční verze – BEZ shift(1).
    Neúplný interval použit pokud má alespoň min_minutes minut.
    """
    counts = df_min[col].resample("15min").count()
    means  = df_min[col].resample("15min").mean()
    means[counts < min_minutes] = np.nan
    result = means.to_frame(col.replace("_min_", "_"))
    result.index = result.index.tz_convert("Europe/Prague")
    return result[~result.index.duplicated(keep="last")].sort_index()


# ── Rolling features ze SO minutových dat ────────────────────────────────────
def compute_so_rolling(df_so_min, df_so_qh_hist):
    """
    df_so_min:   minutová SO data (posledních 35 dní)
    df_so_qh_hist: historická QH SO data (posledních 90 dní, pro rolling_90d)
    Vrací DataFrame s rolling features – BEZ shift (produkce).
    """
    # Produkční QH (s neúplným intervalem)
    df_so_qh_prod = so_min_to_qh_prod(df_so_min, "SO_min_MW", min_minutes=8)
    s = df_so_qh_prod["SO_MW"]

    df_roll = pd.DataFrame(index=df_so_qh_prod.index)
    df_roll["so_last_15min"]      = s
    df_roll["so_last_30min"]      = s.rolling(2).mean()
    df_roll["so_last_60min"]      = s.rolling(4).mean()
    df_roll["so_last_2h"]         = s.rolling(8).mean()
    df_roll["so_last_4h"]         = s.rolling(16).mean()
    df_roll["so_momentum_1"]      = s - s.shift(1)
    df_roll["so_momentum_2"]      = s - s.shift(2)
    df_roll["so_momentum_4"]      = s - s.shift(4)
    df_roll["so_accel"]           = s - 2*s.shift(1) + s.shift(2)
    df_roll["so_trend_direction"] = np.sign(s - s.shift(3))
    df_roll["so_volatility_1h"]   = s.rolling(4).std()
    df_roll["so_volatility_4h"]   = s.rolling(16).std()
    df_roll["so_max_1h"]          = s.rolling(4).max()
    df_roll["so_min_1h"]          = s.rolling(4).min()
    df_roll["so_range_1h"]        = df_roll["so_max_1h"] - df_roll["so_min_1h"]

    # Rolling 90d mean – zde shift(1) zachovat (dlouhodobý průměr)
    so_90d = df_so_qh_hist["SO_MW"].shift(1).rolling(
        window=90*96, min_periods=30*96).mean()
    df_roll["so_rolling_90d_mean"] = so_90d.reindex(df_so_qh_prod.index, method="ffill")

    return df_roll


# ── Rolling features ze SVR minutových dat ───────────────────────────────────
def compute_svr_rolling(df_svr_min, index_ref):
    """index_ref: index z df_so_qh_prod"""
    df = pd.DataFrame(index=index_ref)
    svr_netto = df_svr_min["afrr_up_min_MW"] - df_svr_min["afrr_dn_min_MW"]
    svr_total = df_svr_min["afrr_up_min_MW"] + df_svr_min["afrr_dn_min_MW"]

    for col_name, series in [
        ("svr_netto", svr_netto),
        ("svr_up",    df_svr_min["afrr_up_min_MW"]),
        ("svr_dn",    df_svr_min["afrr_dn_min_MW"]),
        ("svr_total", svr_total),
    ]:
        s = series.resample("15min").mean()
        counts = series.resample("15min").count()
        s[counts < 8] = np.nan
        s.index = s.index.tz_convert("Europe/Prague")
        s = s.reindex(index_ref)
        df[f"{col_name}_last"]    = s
        df[f"{col_name}_mean_1h"] = s.rolling(4).mean()
        df[f"{col_name}_std_1h"]  = s.rolling(4).std()

    svr_capacity = 400.0
    df["svr_utilization"] = (
        svr_total.resample("15min").mean()
        .reindex(index_ref) / svr_capacity
    ).clip(0, 1)
    return df


# ── Rolling features z frekvence ─────────────────────────────────────────────
def compute_freq_rolling(df_freq, index_ref):
    df = pd.DataFrame(index=index_ref)
    counts = df_freq["freq_Hz"].resample("15min").count()
    s_mean = df_freq["freq_Hz"].resample("15min").mean()
    s_dev  = (df_freq["freq_Hz"] - 50.0).abs().resample("15min").mean()
    s_max  = (df_freq["freq_Hz"] - 50.0).abs().resample("15min").max()

    for s in [s_mean, s_dev, s_max]:
        s[counts < 8] = np.nan
        s.index = s.index.tz_convert("Europe/Prague")

    s_mean = s_mean.reindex(index_ref)
    s_dev  = s_dev.reindex(index_ref)
    s_max  = s_max.reindex(index_ref)

    df["freq_last"]         = s_mean
    df["freq_dev_last"]     = s_dev
    df["freq_dev_max_last"] = s_max
    df["freq_mean_1h"]      = s_mean.rolling(4).mean()
    df["freq_dev_mean_1h"]  = s_dev.rolling(4).mean()
    df["freq_std_1h"]       = s_mean.rolling(4).std()
    df["freq_trend"]        = np.sign(s_mean - s_mean.shift(3))
    return df


# ── Rolling features z cen RE ─────────────────────────────────────────────────
def compute_ceny_rolling(df_ceny_re, index_ref):
    df = pd.DataFrame(index=index_ref)
    if "afrr_price_EUR" not in df_ceny_re.columns:
        return df
    counts  = df_ceny_re["afrr_price_EUR"].resample("15min").count()
    s_price = df_ceny_re["afrr_price_EUR"].resample("15min").mean()
    s_price[counts < 8] = np.nan
    s_price.index = s_price.index.tz_convert("Europe/Prague")
    s_price = s_price.reindex(index_ref)

    df["afrr_price_last"]    = s_price
    df["afrr_price_mean_1h"] = s_price.rolling(4).mean()
    df["afrr_price_trend"]   = np.sign(s_price - s_price.shift(3))
    return df


# ── Sestavení feature vektoru pro jeden timestamp ────────────────────────────
def build_feature_row(
    ts,                  # pd.Timestamp (Europe/Prague) – aktuální QH interval
    df_so_rolling,       # výstup compute_so_rolling()
    df_svr_rolling,      # výstup compute_svr_rolling()
    df_freq_rolling,     # výstup compute_freq_rolling()
    df_ceny_rolling,     # výstup compute_ceny_rolling()
    df_so_qh,            # historická QH SO data (posledních 35 dní)
    so_month_avg,        # GroupBy průměr SO per (month, interval)
    so_month_std,        # GroupBy std SO per (month, interval)
    so_weekday_avg,      # GroupBy průměr SO per (weekday, interval)
    so_workday_avg,      # GroupBy průměr SO per interval (pracovní dny)
    so_weekend_avg,      # GroupBy průměr SO per interval (víkendy)
    df_dap,              # ENTSO-E DAP data
    df_ws_cz,            # ENTSO-E Wind+Solar CZ
    df_ws_de,            # ENTSO-E Wind+Solar DE
    df_lf_cz,            # ENTSO-E Load forecast CZ
    df_lf_de,            # ENTSO-E Load forecast DE
    df_exc,              # ENTSO-E Scheduled exchanges
    df_imp_de,           # ENTSO-E DE imbalance prices
    ag2_hist_dfs,        # dict {site_id: DataFrame} AG2 historická data
    ag2_fc_dfs,          # dict {site_id: DataFrame} AG2 forecast data
    days_from_start,     # int – počet dní od 2023-01-01
):
    day_date = ts.date()
    interval = ts.hour * 4 + ts.minute // 15
    row = {}

    # ── 1. ČASOVÉ FEATURES ────────────────────────────────────────────────────
    row["hour"]            = ts.hour
    row["minute"]          = ts.minute
    row["interval_of_day"] = interval
    row["weekday"]         = ts.weekday()
    row["month"]           = ts.month
    row["quarter"]         = ts.quarter
    row["day_of_year"]     = ts.timetuple().tm_yday
    row["week_of_year"]    = ts.isocalendar()[1]
    row["is_weekend"]      = int(ts.weekday() >= 5)
    row["is_holiday_cz"]   = int(day_date in cz_holidays_obj)
    row["is_holiday_de"]   = int(day_date in de_holidays_obj)
    row["is_workday_cz"]   = int(ts.weekday() < 5 and day_date not in cz_holidays_obj)
    row["is_summer"]       = int(ts.month in [5,6,7,8,9])
    row["is_winter"]       = int(ts.month in [11,12,1,2])
    row["hour_sin"]        = math.sin(2*math.pi*ts.hour/24)
    row["hour_cos"]        = math.cos(2*math.pi*ts.hour/24)
    row["interval_sin"]    = math.sin(2*math.pi*interval/96)
    row["interval_cos"]    = math.cos(2*math.pi*interval/96)
    row["month_sin"]       = math.sin(2*math.pi*ts.month/12)
    row["month_cos"]       = math.cos(2*math.pi*ts.month/12)
    row["weekday_sin"]     = math.sin(2*math.pi*ts.weekday()/7)
    row["weekday_cos"]     = math.cos(2*math.pi*ts.weekday()/7)
    row["doy_sin"]         = math.sin(2*math.pi*row["day_of_year"]/365)
    row["doy_cos"]         = math.cos(2*math.pi*row["day_of_year"]/365)
    row["is_night"]        = int(ts.hour in range(0,6))
    row["is_morning"]      = int(ts.hour in range(6,9))
    row["is_day"]          = int(ts.hour in range(9,16))
    row["is_evening"]      = int(ts.hour in range(16,22))
    row["is_night2"]       = int(ts.hour in range(22,24))
    row["time_band"]       = (row["is_night"]*0 + row["is_morning"]*1 +
                               row["is_day"]*2   + row["is_evening"]*3 +
                               row["is_night2"]*4)

    # Dny do svátku
    for i in range(30):
        if (day_date + pd.Timedelta(days=i)) in cz_holidays_obj:
            row["days_to_holiday_cz"] = i; break
    else:
        row["days_to_holiday_cz"] = 30

    # Přemostění
    is_bridge = 0
    if ts.weekday() == 4 and (day_date + pd.Timedelta(days=3)) in cz_holidays_obj:
        is_bridge = 1
    if ts.weekday() == 0 and (day_date - pd.Timedelta(days=3)) in cz_holidays_obj:
        is_bridge = 1
    row["is_bridge_day"] = is_bridge

    # ── 2. ROLLING FEATURES (aktuální stav soustavy) ─────────────────────────
    if ts in df_so_rolling.index:
        for col in df_so_rolling.columns:
            v = df_so_rolling.loc[ts, col]
            if not pd.isna(v):
                row[col] = float(v)

    if not df_svr_rolling.empty and ts in df_svr_rolling.index:
        for col in df_svr_rolling.columns:
            v = df_svr_rolling.loc[ts, col]
            if not pd.isna(v):
                row[col] = float(v)

    if not df_freq_rolling.empty and ts in df_freq_rolling.index:
        for col in df_freq_rolling.columns:
            v = df_freq_rolling.loc[ts, col]
            if not pd.isna(v):
                row[col] = float(v)

    if not df_ceny_rolling.empty and ts in df_ceny_rolling.index:
        for col in df_ceny_rolling.columns:
            v = df_ceny_rolling.loc[ts, col]
            if not pd.isna(v):
                row[col] = float(v)

    # ── 3. SO HISTORICKÉ VZORY ────────────────────────────────────────────────
    so_qh_index_set = set(df_so_qh.index)

    for d, key in [(1,"so_yesterday_I"),(7,"so_last_week_I"),
                   (14,"so_2weeks_I"),(21,"so_3weeks_I"),(28,"so_4weeks_I")]:
        ts_h = ts - pd.Timedelta(days=d)
        if ts_h in so_qh_index_set:
            row[key] = float(df_so_qh.loc[ts_h, "SO_MW"])

    vals_7d, vals_14d, vals_30d = [], [], []
    for d in range(1, 31):
        ts_h = ts - pd.Timedelta(days=d)
        if ts_h in so_qh_index_set:
            val = float(df_so_qh.loc[ts_h, "SO_MW"])
            if d <= 7:  vals_7d.append(val)
            if d <= 14: vals_14d.append(val)
            vals_30d.append(val)

    if vals_7d:
        row["so_avg_7d_I"] = float(np.mean(vals_7d))
        row["so_std_7d_I"] = float(np.std(vals_7d))
    if vals_14d:
        row["so_avg_14d_I"] = float(np.mean(vals_14d))
    if vals_30d:
        row["so_avg_30d_I"] = float(np.mean(vals_30d))

    try:
        row["so_month_interval_avg"] = float(so_month_avg.loc[(ts.month, interval)])
        row["so_month_interval_std"] = float(so_month_std.loc[(ts.month, interval)])
    except Exception:
        pass
    try:
        row["so_weekday_interval_avg"] = float(so_weekday_avg.loc[(ts.weekday(), interval)])
    except Exception:
        pass
    try:
        if ts.weekday() < 5:
            row["so_daytype_interval_avg"] = float(so_workday_avg.loc[interval])
        else:
            row["so_daytype_interval_avg"] = float(so_weekend_avg.loc[interval])
    except Exception:
        pass

    # SO trend signal
    so_30d = row.get("so_avg_30d_I", None)
    if so_30d is not None and ts in df_so_rolling.index:
        rv = df_so_rolling.loc[ts, "so_rolling_90d_mean"]
        if not pd.isna(rv):
            row["so_trend_signal"] = so_30d - float(rv)

    ts_d1 = ts - pd.Timedelta(days=1)
    ts_d2 = ts - pd.Timedelta(days=2)
    ts_d3 = ts - pd.Timedelta(days=3)
    if all(t in so_qh_index_set for t in [ts_d1, ts_d2, ts_d3]):
        so1 = float(df_so_qh.loc[ts_d1, "SO_MW"])
        so2 = float(df_so_qh.loc[ts_d2, "SO_MW"])
        so3 = float(df_so_qh.loc[ts_d3, "SO_MW"])
        row["so_trend_3d_I"]        = so1 - so3
        row["so_acceleration_3d_I"] = so1 - 2*so2 + so3
        if so_30d:
            row["so_yesterday_vs_30d"] = so1 - so_30d

    # ── 4. TREND FEATURES ─────────────────────────────────────────────────────
    row["days_elapsed"]    = float(days_from_start)
    row["days_elapsed_sq"] = float(days_from_start**2) / 1000
    cz_solar_growth = (3200 - 2500) / (3*365)
    row["est_solar_capacity_MWp"] = 2500 + days_from_start * cz_solar_growth

    if "so_month_interval_avg" in row and "so_yesterday_I" in row:
        row["so_vs_seasonal"] = row["so_yesterday_I"] - row["so_month_interval_avg"]

    # ── 5. DAP CENA ───────────────────────────────────────────────────────────
    if not df_dap.empty and ts in df_dap.index:
        row["dap_t"] = float(df_dap.loc[ts, "dap_EUR_MWh"])
    if not df_dap.empty:
        ts_day_start = ts.normalize()
        ts_day_end   = ts_day_start + pd.Timedelta(days=1)
        dap_day = df_dap[(df_dap.index >= ts_day_start) &
                         (df_dap.index < ts_day_end)]["dap_EUR_MWh"]
        if len(dap_day) > 0:
            row["dap_daily_avg"]    = float(dap_day.mean())
            row["dap_daily_spread"] = float(dap_day.max() - dap_day.min())
            row["dap_daily_std"]    = float(dap_day.std())

    # ── 6. OZE FORECAST ───────────────────────────────────────────────────────
    if not df_ws_cz.empty and ts in df_ws_cz.index:
        for col in df_ws_cz.columns:
            row[col] = float(df_ws_cz.loc[ts, col])
    if not df_ws_de.empty and ts in df_ws_de.index:
        for col in df_ws_de.columns:
            row[col] = float(df_ws_de.loc[ts, col])
    if not df_lf_cz.empty and ts in df_lf_cz.index:
        row["entsoe_load_fc_MW"] = float(df_lf_cz.loc[ts, "entsoe_load_fc_MW"])
    if not df_lf_de.empty and ts in df_lf_de.index:
        row["de_load_fc_MW"] = float(df_lf_de.loc[ts, "de_load_fc_MW"])

    # ── 7. SCHEDULED EXCHANGES ────────────────────────────────────────────────
    if not df_exc.empty and ts in df_exc.index:
        for col in df_exc.columns:
            row[col]           = float(df_exc.loc[ts, col])
            row["feat_" + col] = float(df_exc.loc[ts, col])

    # ── 8. DE IMBALANCE ───────────────────────────────────────────────────────
    if not df_imp_de.empty and ts in df_imp_de.index:
        for col in df_imp_de.columns:
            row[col] = float(df_imp_de.loc[ts, col])

    # ── 9. AG2 HISTORICKÁ DATA (posun -1h) ────────────────────────────────────
    ts_hist = (ts - pd.Timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    for site_id, df_ag2 in ag2_hist_dfs.items():
        if ts_hist in df_ag2.index:
            for col in df_ag2.columns:
                row[col] = float(df_ag2.loc[ts_hist, col])

    # ── 10. AG2 FORECAST DATA (bez posunu) ────────────────────────────────────
    # Pro trénink: historická data bez posunu jako proxy
    # Pro produkci: skutečný GetHourlyForecast
    ts_fc = ts.replace(minute=0, second=0, microsecond=0)
    for site_id, df_fc in ag2_fc_dfs.items():
        if ts_fc in df_fc.index:
            for col in df_fc.columns:
                if col.startswith("fc_"):
                    row[col] = float(df_fc.loc[ts_fc, col])

    # ── 11. ASTRONOMICKÉ FEATURES ─────────────────────────────────────────────
    try:
        el, cs = get_solar_params(ts)
    except Exception:
        el, cs = 0.0, 0.0
    row["solar_elevation_deg"] = el
    row["clearsky_ghi_Wm2"]    = cs
    row["is_daylight"]         = int(el > 0)
    row["solar_elevation_sin"] = math.sin(math.radians(max(el, 0)))

    # ── 12. KOMBINOVANÉ FEATURES (_x_) ────────────────────────────────────────
    so_last  = row.get("so_last_15min", 0)
    freq_dev = row.get("freq_dev_last", 0)

    for pasmo in ["night","morning","day","evening"]:
        is_p = row.get(f"is_{pasmo}", 0)
        row[f"so_last_x_{pasmo}"]     = so_last * is_p
        row[f"so_momentum_x_{pasmo}"] = row.get("so_momentum_1", 0) * is_p

    row["so_last_x_freq"]  = so_last * freq_dev
    row["freq_x_day"]      = row.get("freq_last", 0) * row.get("is_day", 0)
    row["freq_dev_x_day"]  = freq_dev * row.get("is_day", 0)

    if "svr_netto_last" in row:
        row["svr_x_freq"] = row["svr_netto_last"] * freq_dev

    if "so_avg_30d_I" in row:
        so_30d_val = row["so_avg_30d_I"]
        row["so_30d_x_night"]   = so_30d_val * row.get("is_night", 0)
        row["so_30d_x_day"]     = so_30d_val * row.get("is_day", 0)
        row["so_30d_x_morning"] = so_30d_val * row.get("is_morning", 0)

    if "so_month_interval_avg" in row:
        som = row["so_month_interval_avg"]
        row["so_month_x_night"]   = som * row.get("is_night", 0)
        row["so_month_x_day"]     = som * row.get("is_day", 0)
        row["so_month_x_evening"] = som * row.get("is_evening", 0)

    if "dap_t" in row:
        row["dap_x_day"]   = row["dap_t"] * row.get("is_day", 0)
        row["dap_x_night"] = row["dap_t"] * row.get("is_night", 0)

    if "hdd_CZ" in row:
        row["hdd_x_morning"] = row["hdd_CZ"] * row.get("is_morning", 0)
        row["hdd_x_evening"] = row["hdd_CZ"] * row.get("is_evening", 0)
        row["hdd_x_weekend"] = row["hdd_CZ"] * row.get("is_weekend", 0)

    if "wind_avg_regional" in row:
        row["wind_x_night"]   = row["wind_avg_regional"] * row.get("is_night", 0)
        row["wind_x_day"]     = row["wind_avg_regional"] * row.get("is_day", 0)
        row["wind_x_evening"] = row["wind_avg_regional"] * row.get("is_evening", 0)

    if "oze_total_regional" in row:
        row["oze_x_day"]  = row["oze_total_regional"] * row.get("is_day", 0)
        row["oze_x_night"]= row["oze_total_regional"] * row.get("is_night", 0)

    if "feat_solar_fc_cz" in row:
        row["solar_x_day"]     = row["feat_solar_fc_cz"] * row.get("is_day", 0)
        row["solar_x_morning"] = row["feat_solar_fc_cz"] * row.get("is_morning", 0)

    if "est_irradiance_Wm2" in row:
        row["irradiance_x_day"]     = row["est_irradiance_Wm2"] * row.get("is_day", 0)
        row["irradiance_x_morning"] = row["est_irradiance_Wm2"] * row.get("is_morning", 0)

    if "so_rolling_90d_mean" in row:
        rm = row["so_rolling_90d_mean"]
        row["trend_x_night"]   = rm * row.get("is_night", 0)
        row["trend_x_morning"] = rm * row.get("is_morning", 0)
        row["trend_x_day"]     = rm * row.get("is_day", 0)

    if "svr_utilization" in row:
        row["svr_util_x_day"]     = row["svr_utilization"] * row.get("is_day", 0)
        row["svr_util_x_evening"] = row["svr_utilization"] * row.get("is_evening", 0)

    if "entsoe_load_fc_MW" in row and "entsoe_solar_fc_MW" in row:
        row["load_solar_ratio"] = row["entsoe_load_fc_MW"] / (row["entsoe_solar_fc_MW"] + 1)

    # AG2 agregáty
    cz_temp  = [v for k,v in row.items() if k.startswith("temp_C_LK") and not pd.isna(v)]
    de_temp  = [v for k,v in row.items() if k.startswith("temp_C_ED") and not pd.isna(v)]
    pl_temp  = [v for k,v in row.items() if k.startswith("temp_C_EP") and not pd.isna(v)]
    cz_wind  = [v for k,v in row.items() if k.startswith("wind_ms_LK") and not pd.isna(v)]
    de_wind  = [v for k,v in row.items() if k.startswith("wind_ms_ED") and not pd.isna(v)]
    pl_wind  = [v for k,v in row.items() if k.startswith("wind_ms_EP") and not pd.isna(v)]
    cz_cloud = [v for k,v in row.items() if k.startswith("cloud_pct_LK") and not pd.isna(v)]
    de_cloud = [v for k,v in row.items() if k.startswith("cloud_pct_ED") and not pd.isna(v)]
    cz_rh    = [v for k,v in row.items() if k.startswith("rh_pct_LK") and not pd.isna(v)]
    cz_dew   = [v for k,v in row.items() if k.startswith("dewpoint_C_LK") and not pd.isna(v)]

    if cz_temp:  row["temp_avg_CZ"]     = float(np.mean(cz_temp))
    if de_temp:  row["temp_avg_DE"]     = float(np.mean(de_temp))
    if pl_temp:  row["temp_avg_PL"]     = float(np.mean(pl_temp))
    if cz_wind:  row["wind_avg_CZ"]     = float(np.mean(cz_wind))
    if de_wind:  row["wind_avg_DE"]     = float(np.mean(de_wind))
    if pl_wind:  row["wind_avg_PL"]     = float(np.mean(pl_wind))
    if cz_cloud: row["cloud_avg_CZ"]    = float(np.mean(cz_cloud))
    if de_cloud: row["cloud_avg_DE"]    = float(np.mean(de_cloud))
    if cz_rh:    row["rh_avg_CZ"]       = float(np.mean(cz_rh))
    if cz_dew:   row["dewpoint_avg_CZ"] = float(np.mean(cz_dew))

    wind_reg = [row[k] for k in ["wind_avg_CZ","wind_avg_DE","wind_avg_PL"] if k in row]
    if wind_reg:
        row["wind_avg_regional"] = float(np.mean(wind_reg))

    if "temp_avg_CZ" in row and "temp_avg_DE" in row:
        row["temp_diff_CZ_DE"] = row["temp_avg_CZ"] - row["temp_avg_DE"]
    if "temp_avg_CZ" in row and "temp_avg_PL" in row:
        row["temp_diff_CZ_PL"] = row["temp_avg_CZ"] - row["temp_avg_PL"]
    if "temp_avg_CZ" in row and "dewpoint_avg_CZ" in row:
        row["temp_dew_spread_CZ"] = row["temp_avg_CZ"] - row["dewpoint_avg_CZ"]
    if "temp_avg_CZ" in row:
        row["hdd_CZ"] = max(0, 15 - row["temp_avg_CZ"])
        row["cdd_CZ"] = max(0, row["temp_avg_CZ"] - 22)
    if "temp_avg_DE" in row:
        row["hdd_DE"] = max(0, 15 - row["temp_avg_DE"])

    if "cloud_avg_CZ" in row:
        row["est_irradiance_Wm2"] = row["clearsky_ghi_Wm2"] * (1 - row["cloud_avg_CZ"]/100)
    if "solar_elevation_sin" in row and "cloud_avg_CZ" in row:
        row["elevation_x_cloud"] = row["solar_elevation_sin"] * (1 - row["cloud_avg_CZ"]/100)

    if "entsoe_solar_fc_MW" in row:
        row["feat_solar_fc_cz"] = row["entsoe_solar_fc_MW"]
    if "entsoe_wind_fc_MW" in row:
        row["feat_wind_fc_cz"] = row["entsoe_wind_fc_MW"]

    wind_de_cols = [k for k in row if k.startswith("de_wind_fc_MW_")]
    if wind_de_cols:
        row["de_wind_fc_total_MW"] = sum(row[k] for k in wind_de_cols)
        row["feat_wind_fc_de"]     = row["de_wind_fc_total_MW"]
    elif "de_wind_fc_total_MW" in row:
        row["feat_wind_fc_de"] = row["de_wind_fc_total_MW"]

    oze_total = sum(row.get(k, 0) for k in
        ["entsoe_wind_fc_MW","entsoe_solar_fc_MW","de_wind_fc_total_MW","de_solar_fc_MW"]
        if k in row)
    if oze_total > 0:
        row["oze_total_regional"] = oze_total

    sched_sum = sum(row.get(f"feat_sched_{exp}_{imp}_MW", 0)
                    for exp, imp in [("CZ","DE"),("DE","CZ"),("CZ","SK"),("SK","CZ"),
                                     ("CZ","AT"),("AT","CZ"),("CZ","PL"),("PL","CZ")]
                    if f"feat_sched_{exp}_{imp}_MW" in row)
    if sched_sum != 0:
        row["sched_netto_CZ"] = sched_sum

    if "de_imbal_long_EUR_MWh" in row and "de_imbal_short_EUR_MWh" in row:
        row["de_imbal_spread"] = row["de_imbal_long_EUR_MWh"] - row["de_imbal_short_EUR_MWh"]
        if "dap_t" in row:
            row["de_cz_price_spread"] = row["dap_t"] - row["de_imbal_long_EUR_MWh"]

    return row


# ── Sestavení feature vektoru pro predikci ────────────────────────────────────
def build_x_vector(ts_pred, row_dict, feature_cols):
    """
    Převede dict features na numpy array ve správném pořadí dle feature_cols.
    Chybějící features = 0.
    """
    x = pd.Series(row_dict).reindex(feature_cols).fillna(0).values.reshape(1, -1)
    return pd.DataFrame(x, columns=feature_cols)
```

---

### Spuštění predikce pro T+1 až T+4

```python
from xgboost import XGBRegressor, XGBClassifier
import json

HORIZONS = [1, 2, 3, 4]
INTERVAL_LABELS = {i: f"{i//4:02d}:{(i%4)*15:02d}" for i in range(96)}
clf_map_inv = {0: -1, 1: 0, 2: 1}

# Načti modely
models_reg = {h: {} for h in HORIZONS}
models_clf = {h: {} for h in HORIZONS}
for h in HORIZONS:
    for i in range(96):
        reg = XGBRegressor()
        reg.load_model(f"models/id/reg_h{h}_i{i:02d}.json")
        clf = XGBClassifier()
        clf.load_model(f"models/id/clf_h{h}_i{i:02d}.json")
        models_reg[h][i] = reg
        models_clf[h][i] = clf

with open("models/id/feature_cols.json") as f:
    feature_cols = json.load(f)

# Aktuální timestamp (poslední dostupný QH interval)
now_ts = ...  # pd.Timestamp aktuálního QH intervalu

# Sestavení features pro predikovaný interval
predictions = {}
for h in HORIZONS:
    ts_pred  = now_ts + pd.Timedelta(minutes=15*h)
    interval = ts_pred.hour * 4 + ts_pred.minute // 15

    row  = build_feature_row(ts_pred, ...)
    x_df = build_x_vector(ts_pred, row, feature_cols)

    pred_reg   = float(models_reg[h][interval].predict(x_df)[0])
    pred_proba = list(models_clf[h][interval].predict_proba(x_df)[0])
    pred_dir   = clf_map_inv[int(models_clf[h][interval].predict(x_df)[0])]

    predictions[h] = {
        "horizon":        h,
        "interval_start": INTERVAL_LABELS[interval],
        "interval_end":   INTERVAL_LABELS[(interval + 1) % 96],
        "so_pred_MW":     round(pred_reg, 1),
        "so_pred_MWh":    round(pred_reg * 0.25, 2),
        "so_direction":   "nabij" if pred_dir == -1 else ("vybij" if pred_dir == 1 else "neutral"),
        "prob_nabij":     round(pred_proba[0], 3),
        "prob_neutral":   round(pred_proba[1], 3),
        "prob_vybij":     round(pred_proba[2], 3),
    }
```

---

## Soubory modelu

```
models/id/
├── reg_h1_i00.json   # regressor T+1, interval 00:00
├── reg_h1_i01.json   # regressor T+1, interval 00:15
│   ...
├── reg_h4_i95.json   # regressor T+4, interval 23:45
├── clf_h1_i00.json   # klasifikátor T+1, interval 00:00
│   ...
├── clf_h4_i95.json   # klasifikátor T+4, interval 23:45
├── feature_cols.json # seznam 181 features v správném pořadí
└── results.json      # MAE/Acc per interval (pro monitoring)
```

Interval číslování: `i = hour * 4 + minute // 15`
Příklad: 14:30 → `i = 14*4 + 30//15 = 58` → `reg_h1_i58.json`
