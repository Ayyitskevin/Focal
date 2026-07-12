#!/usr/bin/env python3
"""Run hosted backups (ADR 0057): one pass, or --loop for the compose sidecar.

Local consistent DB snapshots always; off-site rclone sync when
MISE_BACKUP_RCLONE_REMOTE is set (that's the part that survives disk loss).
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, hosted_backup  # noqa: E402

log = logging.getLogger("mise.hosted_backup.cli")
_FAILURE_RETRY_SECONDS = 15 * 60


def _interval_seconds(raw: str) -> int:
    try:
        hours = float(raw)
    except ValueError as exc:
        raise ValueError("backup interval must be a number of hours") from exc
    if not math.isfinite(hours) or not 1 <= hours <= 168:
        raise ValueError("backup interval must be between 1 and 168 hours")
    return int(hours * 3600)


def _once() -> int:
    try:
        summary = hosted_backup.run_backup(
            Path(config.DATA_DIR),
            Path(config.SAAS_TENANT_DATA_DIR),
            Path(config.SAAS_CONTROL_DB_PATH),
            retention_days=int(os.environ.get("MISE_BACKUP_RETENTION_DAYS", "14")),
            rclone_remote=os.environ.get("MISE_BACKUP_RCLONE_REMOTE", "").strip(),
            remote_encrypted=os.environ.get("MISE_BACKUP_RCLONE_REMOTE_ENCRYPTED", "")
            .strip()
            .lower()
            in {"1", "true", "yes"},
        )
    except Exception:
        log.exception("hosted backup failed")
        return 1
    print(summary)
    if summary["tenant_failures"] or summary["offsite"].startswith("failed"):
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Hosted Mise backup runner")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="run immediately, then every MISE_BACKUP_INTERVAL_HOURS (default 24)",
    )
    args = parser.parse_args()
    if not args.loop:
        return _once()
    try:
        interval_s = _interval_seconds(os.environ.get("MISE_BACKUP_INTERVAL_HOURS", "24"))
    except ValueError as exc:
        log.error("invalid MISE_BACKUP_INTERVAL_HOURS: %s", exc)
        return 2
    while True:
        result = _once()
        if result:
            # A failed pass does not kill the loop. Local staleness, per-tenant
            # failures, and off-site failures each have durable monitor markers.
            log.error("hosted backup pass failed; retrying after the configured interval")
        delay = min(interval_s, _FAILURE_RETRY_SECONDS) if result else interval_s
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
