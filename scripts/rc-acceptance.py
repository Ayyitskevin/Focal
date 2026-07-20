#!/usr/bin/env python3
"""Operator entry point: Focal release-candidate acceptance readiness.

Runs product behavioral gates (owner→client vertical, storage fail-loud,
seeder tombstone, source capability checks) and prints pass/fail/blocked/n/a.

Does not deploy, charge Stripe, submit to the App Store, or mutate production
tenants. Store-ship remains do-not-ship until #179/#180/#185 are decided.

Usage:
  .venv/bin/python scripts/rc-acceptance.py
  .venv/bin/python scripts/rc-acceptance.py --json
  .venv/bin/python scripts/rc-acceptance.py --no-tests   # structural only
  .venv/bin/python scripts/rc-acceptance.py --no-integrity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import rc_acceptance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Focal release-candidate acceptance readiness (product gates)."
    )
    parser.add_argument("--json", action="store_true", help="machine-readable JSON")
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="skip invoking pytest suites (structural checks only)",
    )
    parser.add_argument(
        "--no-integrity",
        action="store_true",
        help="skip storage/seeder/gallery regression suite after RC acceptance",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="project root (default: repository root)",
    )
    args = parser.parse_args()

    report = rc_acceptance.build_report(
        project_root=args.root,
        run_tests=not args.no_tests,
        include_integrity=not args.no_integrity,
    )
    print(rc_acceptance.format_json(report) if args.json else rc_acceptance.format_text(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
