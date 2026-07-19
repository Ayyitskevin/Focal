"""Fail-closed contract for the retired reviewer-demo seed script."""

import ast
import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "seed_demo_tenant.py"
GAME_PLAN = ROOT / "docs" / "APP-STORE-GAMEPLAN.md"
SUBMISSION_PLAN = ROOT / "docs" / "APP-STORE-SUBMISSION.md"
CONDUCTOR_PLAN = ROOT / "docs" / "CONDUCTOR-PLAN.md"
NICHE_DECISION = ROOT / "docs" / "NICHE-STORY-DECISION.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
BLOCKER_URL = "https://github.com/Ayyitskevin/Focal/issues/185"


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


@pytest.mark.parametrize(
    "command",
    ([sys.executable, str(SCRIPT)], [sys.executable, "-m", "scripts.seed_demo_tenant"]),
)
def test_legacy_cli_refuses_before_reading_configuration(tmp_path, command):
    control_db = tmp_path / "control.db"
    tenant_dir = tmp_path / "tenants"
    result = subprocess.run(
        command,
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


def test_runpy_load_is_inert_and_main_refuses_before_hosted_state(tmp_path, monkeypatch):
    control_db = tmp_path / "control.db"
    tenant_dir = tmp_path / "tenants"
    monkeypatch.setenv("MISE_SAAS_CONTROL_DB_PATH", str(control_db))
    monkeypatch.setenv("MISE_SAAS_TENANT_DATA_DIR", str(tenant_dir))

    namespace = runpy.run_path(str(SCRIPT))
    assert callable(namespace["seed_demo_tenant"])
    assert not control_db.exists()
    assert not tenant_dir.exists()

    with pytest.raises(SystemExit, match=r"issues/185"):
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert not control_db.exists()
    assert not tenant_dir.exists()


def test_tombstone_has_no_state_or_configuration_import_surface():
    source = SCRIPT.read_text()
    tree = ast.parse(source)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imported_modules == {"__future__"}

    called_functions = {
        node.func.id if isinstance(node.func, ast.Name) else "attribute-call"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert called_functions <= {"SystemExit", "main"}


def test_app_store_docs_retire_every_unsafe_reviewer_provisioning_instruction():
    documents = {
        path: path.read_text()
        for path in (GAME_PLAN, SUBMISSION_PLAN, CONDUCTOR_PLAN, NICHE_DECISION, DOCS_INDEX)
    }

    for path, text in documents.items():
        assert BLOCKER_URL in text, f"{path.name} must route operators to the T3 hold"

    for path in (GAME_PLAN, SUBMISSION_PLAN, CONDUCTOR_PLAN):
        normalized = " ".join(documents[path].split()).lower()
        assert "do not run" in normalized
        assert "seed_demo_tenant.py" in normalized

    combined = " ".join("\n".join(documents.values()).split()).lower()
    for stale_instruction in (
        "`plan_status='active'`",
        "scripted seed run against staging",
        "launch is blocked on ops, not code",
        "t3–t5 are pre-submission work that needs no deploy",
        "reuse `bootstrap.ensure_public_showcase` as a mechanism",
        "kevin's selection unblocks the reviewer demo",
        "**unblocks:** conductor t3",
        "mise_saas_control_db_path",
        "mise_demo_tenant_password",
        "python -m scripts.seed_demo_tenant",
    ):
        assert stale_instruction not in combined

    assert "stable demo-owned identities" in combined
    assert "remains independently held" in combined
