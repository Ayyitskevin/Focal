#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
  echo "Missing .env. Copy .env.example to .env and fill hosted production values first." >&2
  exit 2
fi

if [ -z "${MISE_CADDY_SITE_ADDRESS:-}" ]; then
  echo "Set MISE_CADDY_SITE_ADDRESS, for example:" >&2
  echo "  MISE_CADDY_SITE_ADDRESS='mise.example.com, *.mise.example.com' $0" >&2
  exit 2
fi

compose() {
  if [ -n "${MISE_COMPOSE_CMD:-}" ]; then
    # Allows MISE_COMPOSE_CMD='podman compose' on hosts without Docker Compose.
    # shellcheck disable=SC2086
    $MISE_COMPOSE_CMD "$@"
  else
    docker compose "$@"
  fi
}

echo "Building the app and backup images that will pass the launch gates..."
compose build mise backup

echo "Running hosted production preflight inside the built app image..."
compose run --rm --no-deps mise python scripts/hosted-preflight.py --static

echo "Stopping public ingress and the old backup loop before replacing the app..."
compose stop caddy backup

echo "Launching the private app for runtime gates..."
compose up -d mise

echo "Waiting for Mise health and control migrations..."
ready=0
for _ in $(seq 1 60); do
  if compose exec -T mise python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8400/healthz', timeout=2).read()" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "Mise did not become healthy within 120 seconds; Caddy was not started." >&2
  compose logs mise >&2
  exit 1
fi

echo "Forcing one complete encrypted backup pass..."
if ! compose run --rm --no-deps --entrypoint python backup scripts/hosted-backup.py; then
  exit 1
fi

echo "Running runtime preflight inside the data-mounted app container..."
compose exec -T mise python scripts/hosted-preflight.py

echo "Runtime gates passed; starting backup service and public ingress..."
compose up -d backup caddy

echo "Hosted stack status:"
compose ps
