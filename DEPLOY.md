# Nasazení PP Dashboardu na Hetzner

Tenhle návod tě provede nasazením na čistý Hetzner Cloud server (Ubuntu 24.04).
Aplikace zůstává ve Streamlitu (vizuál beze změny), ale **data se stahují cronem
na pozadí** a aplikace je jen čte z disku — proto je v prohlížeči rychlá.

## Jak to funguje (architektura)

```
        ┌─────────────────────┐        ┌──────────────────────────┐
 CRON ─▶│ scripts/refresh_data │ ─────▶ │ data/snapshots/*.pkl     │
        │ (volá ENTSO-E/ČEPS/  │  uloží │ (hotová data)            │
        │  ENTSO-G API)        │        └──────────────────────────┘
        └─────────────────────┘                     │ čte (rychle)
                                                     ▼
                                          ┌──────────────────────┐
                                  HTTP ──▶│ Streamlit app (Docker)│
                                          └──────────────────────┘
```

- `data/*.py` má dvě vrstvy: `*_live()` (reálné API volání, spouští cron) a
  `fetch_*()` (čte snapshot, fallback na live, volá appka).
- Žádná pomalá API volání během vykreslování → rychlé UI.

---

## 1. Příprava serveru

Vytvoř Hetzner Cloud server (stačí **CX22**, 2 vCPU / 4 GB). SSH dovnitř a nainstaluj Docker:

```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
```

## 2. Stažení projektu

```bash
git clone https://github.com/stamvasek-png/pp-dashboard.git
cd pp-dashboard
```

## 3. Konfigurace

```bash
cp .env.example .env
nano .env          # vyplň ENTSOE_TOKEN (nebo nech prázdné = výchozí token)
```

## 4. Build a spuštění

```bash
docker compose up -d --build
```

Aplikace teď běží na `http://<IP-serveru>:8501`.

## 5. První naplnění dat

Při prvním startu jsou snapshoty prázdné (appka si v tu chvíli stáhne data
živě jako fallback). Hned je naplň:

```bash
docker compose exec -T app python scripts/refresh_data.py
```

## 6. Cron — automatická aktualizace dat

Tohle je klíčové pro rychlost. Otevři crontab na **hostu**:

```bash
crontab -e
```

a přidej (uprav cestu k projektu, pokud je jiná):

```cron
# Živá data každých 5 minut
*/5 * * * * cd /root/pp-dashboard && docker compose exec -T app python scripts/refresh_data.py >> /var/log/pp-refresh.log 2>&1

# Historie plynu jednou denně v 5:10
10 5 * * * cd /root/pp-dashboard && docker compose exec -T app python scripts/update_gas_history.py >> /var/log/pp-gas.log 2>&1
```

> ČEPS data mají TTL ~1 min; pokud chceš svěžejší čísla, dej `*/1 * * * *`.
> Pozor jen na rate-limity ENTSO-E — `*/5` je bezpečný kompromis.

## 7. Doména a HTTPS (volitelné)

Spusť i nginx reverse proxy:

```bash
docker compose --profile proxy up -d --build
```

Appka pak jede na portu **80**. Pro HTTPS:

1. Nasměruj A záznam domény na IP serveru.
2. V `deploy/nginx.conf` doplň `server_name` a odkomentuj HTTPS blok.
3. Vystav certifikát (Let's Encrypt) — nejjednodušeji přes certbot na hostu, nebo
   přidej do compose [Caddy](https://caddyserver.com/), který certifikát řeší
   automaticky. Certifikáty namapuj do `deploy/certs/` a v compose odkomentuj
   port 443 a mount certifikátů.

> Tip: pokud nechceš řešit certbot ručně, řekni mi a přidám Caddy službu —
> ta vyřídí HTTPS sama za tebe.

## 8. Aktualizace aplikace

```bash
cd /root/pp-dashboard
git pull
docker compose up -d --build
```

## 9. Užitečné příkazy

```bash
docker compose logs -f app          # logy aplikace
docker compose ps                   # stav kontejnerů
docker compose restart app          # restart
docker compose down                 # zastavení
ls -la data/snapshots/              # kontrola, že cron plní snapshoty
```

---

## Troubleshooting

| Problém | Řešení |
|---|---|
| Appka „věčně načítá" za nginx | WebSocket hlavičky — viz `deploy/nginx.conf`, jsou už nastavené |
| Prázdné grafy hned po startu | Spusť `docker compose exec -T app python scripts/refresh_data.py` |
| Data se neaktualizují | Zkontroluj cron: `crontab -l` a log `/var/log/pp-refresh.log` |
| ENTSO-E vrací chyby | Zkontroluj `ENTSOE_TOKEN` v `.env` a rate-limity |
