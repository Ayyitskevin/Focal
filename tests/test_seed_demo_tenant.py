"""Fail-closed contract for the retired reviewer-demo seed script."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "seed_demo_tenant.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_demo_tenant_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_callable_refuses_before_touching_hosted_state(tmp_path, monkeypatch):
    control_db = tmp_path / "control.db"
    tenant_dir = tmp_path / "tenants"
    monkeypatch.setenv("MISE_SAAS_CONTROL_DB_PATH", str(control_db))
    monkeypatch.setenv("MISE_SAAS_TENANT_DATA_DIR", str(tenant_dir))

    with pytest.raises(SystemExit, match=r"issues/185"):
        _load_seeder().seed_demo_tenant(
            slug="demo-tour",
            studio_name="Mise Demo Studio",
            owner_email="reviewer@demo.mise.local",
            password="review-me-please",
            preset="wedding",
        )

    assert not control_db.exists()
    assert not tenant_dir.exists()


def test_cli_refuses_before_reading_configuration(tmp_path):
    control_db = tmp_path / "control.db"
    tenant_dir = tmp_path / "tenants"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env={
            "PATH": "",
            "MISE_SAAS_MODE": "true",
            "MISE_SAAS_CONTROL_DB_PATH": str(control_db),
            "MISE_SAAS_TENANT_DATA_DIR": str(tenant_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Reviewer demo provisioning is disabled" in result.stderr
    assert "issues/185" in result.stderr
    assert not control_db.exists()
    assert not tenant_dir.exists()


def test_tombstone_contains_no_hosted_state_mutators():
    source = SCRIPT.read_text()

    for unsafe_operation in (
        "from app import",
        "create_tenant(",
        "UPDATE tenants",
        "DELETE FROM bookings",
        "tenant_runtime(",
    ):
        assert unsafe_operation not in source
