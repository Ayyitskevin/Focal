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

echo "Running hosted production preflight..."
python scripts/hosted-preflight.py

echo "Launching Mise hosted production stack..."
compose up --build -d

echo "Hosted stack status:"
compose ps
