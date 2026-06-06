# ── PP Dashboard — Streamlit ────────────────────────────────────
# Image pro nasazení na Hetzner (nebo kamkoli s Dockerem).
# Vizuál zůstává 100% stejný jako na Streamlit Cloud.
FROM python:3.11-slim

# Streamlit běží bez bufferování logů a bez .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8501

WORKDIR /app

# curl je potřeba jen pro healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Nejdřív jen requirements → lepší využití cache vrstev
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Zbytek aplikace
COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/_stcore/health" || exit 1

# headless + adresa 0.0.0.0, aby byl Streamlit dostupný z kontejneru ven
CMD streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
