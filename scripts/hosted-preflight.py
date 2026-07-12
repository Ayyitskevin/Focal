#!/usr/bin/env python3
"""Run hosted SaaS launch readiness checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import saas_preflight  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check hosted Mise SaaS launch readiness.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--no-write-probes",
        action="store_true",
        help="do not create/write temp files in configured data directories",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="bootstrap check before containers start; defer runtime backup evidence",
    )
    args = parser.parse_args()

    report = saas_preflight.check_readiness(
        project_root=Path(__file__).resolve().parents[1],
        write_probes=not args.no_write_probes,
        require_runtime_evidence=not args.static,
    )
    print(saas_preflight.format_json(report) if args.json else saas_preflight.format_text(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
