FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MISE_HOST=0.0.0.0 \
    MISE_PORT=8400 \
    MISE_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg rclone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 --create-home mise \
    && mkdir -p /data \
    && chown -R mise:mise /data /app

USER mise
EXPOSE 8400

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8400/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8400", "--proxy-headers"]
