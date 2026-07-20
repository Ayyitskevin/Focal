#!/usr/bin/env python3
"""CLI: Focal App Store readiness auditor (read-only, fail-closed).

Never contacts App Store Connect or production. See app/app_store_readiness.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_store_readiness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Focal App Store readiness auditor")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    report = app_store_readiness.build_report(project_root=args.root)
    print(
        app_store_readiness.format_json(report)
        if args.json
        else app_store_readiness.format_text(report)
    )
    # Exit 0 only when the audit has no hard fails. App Store ship status is
    # always printed separately and remains do-not-ship until Kevin decides
    # #179/#180/#185 — eng clean ≠ Connect approval.
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
