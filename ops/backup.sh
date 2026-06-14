#!/usr/bin/env bash
# Nightly DB snapshot — runs as the mise user (sqlite3 .backup is WAL-safe).
set -euo pipefail
DATA=/opt/mise/data
OUT="$DATA/backups"
mkdir -p "$OUT"
STAMP=$(date +%F-%H%M)
sqlite3 "$DATA/mise.db" ".backup '$OUT/mise-$STAMP.db'"
gzip -f "$OUT/mise-$STAMP.db"
find "$OUT" -name 'mise-*.db.gz' -mtime +14 -delete
echo "backup ok: mise-$STAMP.db.gz ($(du -h "$OUT/mise-$STAMP.db.gz" | cut -f1))"
