"""Polish Slice 2: the fresh-clone first-run actually works.

The README quickstart says `cp .env.example .env` then `uvicorn app.main:app`.
Before this slice that was false: config only read /opt/mise/.env (bare-metal)
or MISE_ENV_FILE (tests/compose), so a fresh clone's ./.env silently loaded
NOTHING and the app died with "MISE_SECRET_KEY is not set". These tests run
config in a clean subprocess — the load happens at import time, so an
in-process test can't exercise it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent


def _probe(cwd: Path, extra_env: dict | None = None) -> str:
    """Import app.config in a clean interpreter at `cwd`; print the loaded secret."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO),
        **(extra_env or {}),
    }
    out = subprocess.run(
        [sys.executable, "-c", "from app import config; print(config.SECRET_KEY or '<unset>')"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_fresh_clone_dot_env_in_cwd_is_loaded(tmp_path):
    (tmp_path / ".env").write_text("MISE_SECRET_KEY=from-cwd-dot-env\n")
    assert _probe(tmp_path) == "from-cwd-dot-env"


def test_explicit_env_file_still_wins_over_cwd(tmp_path):
    # An operator pointing MISE_ENV_FILE somewhere must not have a stray ./.env
    # silently mixed in.
    (tmp_path / ".env").write_text("MISE_SECRET_KEY=stray-cwd-value\n")
    explicit = tmp_path / "explicit.env"
    explicit.write_text("MISE_SECRET_KEY=explicit-file-value\n")
    assert _probe(tmp_path, {"MISE_ENV_FILE": str(explicit)}) == "explicit-file-value"


def test_real_environment_variables_beat_any_env_file(tmp_path):
    (tmp_path / ".env").write_text("MISE_SECRET_KEY=file-value\n")
    assert _probe(tmp_path, {"MISE_SECRET_KEY": "real-env-value"}) == "real-env-value"
