#!/usr/bin/env python3
"""Run hosted backups (ADR 0057): one pass, or --loop for the compose sidecar.

Local consistent DB snapshots always; off-site rclone sync when
MISE_BACKUP_RCLONE_REMOTE is set (that's the part that survives disk loss).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, hosted_backup  # noqa: E402

log = logging.getLogger("mise.hosted_backup.cli")


def _once() -> int:
    try:
        summary = hosted_backup.run_backup(
            Path(config.DATA_DIR),
            Path(config.SAAS_TENANT_DATA_DIR),
            Path(config.SAAS_CONTROL_DB_PATH),
            retention_days=int(os.environ.get("MISE_BACKUP_RETENTION_DAYS", "14")),
            rclone_remote=os.environ.get("MISE_BACKUP_RCLONE_REMOTE", "").strip(),
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
    interval_s = int(float(os.environ.get("MISE_BACKUP_INTERVAL_HOURS", "24")) * 3600)
    while True:
        _once()  # a failed pass must not kill the loop — the marker goes stale
        # and ops_monitor's backup_stale alert is the escalation path
        time.sleep(interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
