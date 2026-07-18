#!/usr/bin/env python3
"""Run Mise's static product tour with isolated, disposable local state.

This is deliberately not a tenant or App Review account seeder. It strips every
inherited MISE_* variable, ignores local env files, binds Uvicorn to loopback, and
removes the temporary data directory after the server exits.
"""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def demo_environment(data_dir: Path, port: int) -> dict[str, str]:
    """Return a local-only environment with no inherited Mise integrations."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("MISE_")}
    root = f"localhost:{port}"
    env.update(
        {
            "MISE_ENV_FILE": str(data_dir / "no-env-file"),
            "MISE_HOST": "127.0.0.1",
            "MISE_PORT": str(port),
            "MISE_BASE_URL": f"http://{root}",
            "MISE_DATA_DIR": str(data_dir),
            "MISE_SECRET_KEY": secrets.token_urlsafe(32),
            "MISE_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "MISE_COOKIE_SECURE": "false",
            "MISE_SHOWCASE_SEED": "false",
            "MISE_SAAS_MODE": "true",
            "MISE_SAAS_ROOT_DOMAIN": root,
            "MISE_SAAS_MARKETING_HOST": root,
            "MISE_SAAS_CONTROL_DB_PATH": str(data_dir / "saas-control.db"),
            "MISE_SAAS_TENANT_DATA_DIR": str(data_dir / "tenants"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def run_server(command: list[str], env: dict[str, str]) -> int:
    """Run Uvicorn in its own signal group and shut it down cleanly on Ctrl+C."""
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        start_new_session=True,
    )

    def interrupt_on_terminate(_signum, _frame):
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, interrupt_on_terminate)
    try:
        try:
            return process.wait()
        except KeyboardInterrupt:
            try:
                process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the static Mise reviewer tour in disposable local state."
    )
    parser.add_argument("--port", type=_port, default=8400)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mise-review-") as temporary:
        data_dir = Path(temporary)
        env = demo_environment(data_dir, args.port)
        demo_url = f"http://localhost:{args.port}/demo"
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ]

        print("Mise reviewer tour")
        print(f"  open: {demo_url}")
        print("  state: disposable; removed after Ctrl+C")
        print("  integrations: disabled; inherited MISE_* values ignored")
        return run_server(command, env)


if __name__ == "__main__":
    raise SystemExit(main())
