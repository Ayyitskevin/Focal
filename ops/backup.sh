#!/usr/bin/env bash
# Stage 1 (runs as the mise user, nightly): consistent, SELF-VERIFIED SQLite snapshot.
# sqlite3 .backup is WAL-safe. The snapshot is integrity-checked before it is kept,
# so a corrupt snapshot fails loud instead of silently replacing a good one.
# This stage is LOCAL ONLY — off-machine durability is the mickey-side pull (see ops/BACKUP.md).
set -euo pipefail
DATA=${MISE_DATA_DIR:-/opt/mise/data}
OUT="$DATA/backups"
mkdir -p "$OUT"
LOCK="$DATA/.backup.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "BACKUP SKIPPED: another backup pass is already running" >&2
  exit 75
fi
SOURCE="$DATA/mise.db"
if [ ! -f "$SOURCE" ] || [ -L "$SOURCE" ]; then
  echo "BACKUP FAILED: source database is missing or is a symlink" >&2
  exit 1
fi
STAMP=$(date +%F-%H%M%S-%N)
STAGING="$DATA/.backup-staging"
mkdir -p "$STAGING"
find "$STAGING" -type f -delete
TMP=$(mktemp "$STAGING/mise-snapshot.XXXXXX.db")
STAGED_ARCHIVE="$TMP.gz"
ARCHIVE="$OUT/mise-$STAMP.db.gz"
cleanup_tmp() {
  rm -f "$TMP" "$STAGED_ARCHIVE"
}
trap cleanup_tmp EXIT

sqlite3 "$SOURCE" ".backup '$TMP'"

# Native caption instructions/candidates are short-lived operational data, not
# restore material. Scrub only the destination snapshot, then VACUUM so prior
# text cannot remain recoverable in free pages inside the compressed database.
if [ "$(sqlite3 "$TMP" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mobile_caption_suggestions';")" = "1" ]; then
  sqlite3 "$TMP" "PRAGMA secure_delete=ON;
    UPDATE mobile_caption_suggestions
       SET session_id=NULL,
           status=CASE WHEN status IN ('queued','running','ready','failed') THEN 'failed' ELSE status END,
           context_json=NULL,candidate_text=NULL,provider=NULL,model=NULL,
           failure_code=CASE WHEN status IN ('queued','running','ready','failed') THEN 'session_ended' ELSE NULL END,
           completed_at=CASE WHEN status IN ('queued','running','ready','failed') THEN COALESCE(completed_at,datetime('now')) ELSE completed_at END;"
fi
if [ "$(sqlite3 "$TMP" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mobile_caption_usage';")" = "1" ]; then
  sqlite3 "$TMP" "PRAGMA secure_delete=ON;
    UPDATE mobile_caption_usage
       SET state='finished',finished_at=COALESCE(finished_at,datetime('now'))
     WHERE state='active';"
fi
if [ "$(sqlite3 "$TMP" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('api_sessions','api_tokens');")" = "2" ]; then
  sqlite3 "$TMP" "PRAGMA secure_delete=ON;
    UPDATE api_sessions
       SET revoked_at=COALESCE(revoked_at,CAST(strftime('%s','now') AS INTEGER)),
           revoke_reason=COALESCE(revoke_reason,'backup_restore')
     WHERE revoked_at IS NULL;
    UPDATE api_tokens
       SET revoked_at=COALESCE(revoked_at,CAST(strftime('%s','now') AS INTEGER))
     WHERE revoked_at IS NULL;"
fi
if [ "$(sqlite3 "$TMP" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('mobile_push_devices','mobile_notification_deliveries');")" = "2" ]; then
  sqlite3 "$TMP" "PRAGMA secure_delete=ON;
    UPDATE mobile_push_devices
       SET active=0,session_id=NULL,token_ciphertext=NULL,
           disabled_reason='backup_restore',
           disabled_at=COALESCE(disabled_at,datetime('now')),
           updated_at=datetime('now');
    UPDATE mobile_notification_deliveries
       SET status='failed',claim_token=NULL,claimed_at=NULL,queued_job_id=NULL,
           reason='backup_restore',updated_at=datetime('now')
     WHERE status IN ('queued','sending','retry');"
fi
if [ "$(sqlite3 "$TMP" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='jobs';")" = "1" ]; then
  sqlite3 "$TMP" "UPDATE jobs
       SET status='failed',error='backup_restore',updated_at=datetime('now')
     WHERE status IN ('queued','running')
       AND kind IN ('apns_delivery','mobile_caption_suggestion');"
fi

# Rebuild once after all destination-only sanitizers so deleted token, push,
# caption, and provider data cannot remain recoverable in free database pages.
sqlite3 "$TMP" "PRAGMA secure_delete=ON; VACUUM;"

# HARD CHECK: a snapshot we cannot verify is not a backup (R21).
res=$(sqlite3 "$TMP" "PRAGMA integrity_check;")
if [ "$res" != "ok" ]; then
  echo "BACKUP FAILED: integrity_check on fresh snapshot = $res" >&2
  rm -f "$TMP"
  exit 1
fi
core_tables=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM sqlite_master
  WHERE type='table' AND name IN ('schema_migrations','clients','projects');")
if [ "$core_tables" != "3" ]; then
  echo "BACKUP FAILED: snapshot is missing required Mise schema" >&2
  exit 1
fi
migration_count=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM schema_migrations;")
if [ "$migration_count" -lt 1 ]; then
  echo "BACKUP FAILED: snapshot has no applied migrations" >&2
  exit 1
fi
foreign_key_errors=$(sqlite3 "$TMP" "PRAGMA foreign_key_check;")
if [ -n "$foreign_key_errors" ]; then
  echo "BACKUP FAILED: snapshot foreign-key check failed" >&2
  exit 1
fi

gzip -c "$TMP" > "$STAGED_ARCHIVE"
mv "$STAGED_ARCHIVE" "$ARCHIVE"
find "$OUT" -name 'mise-*.db.gz' -mtime +14 -delete
echo "stage1 ok: mise-$STAMP.db.gz ($(du -h "$ARCHIVE" | cut -f1)) verified=ok"
