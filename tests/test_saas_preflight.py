import json
from pathlib import Path

from app import config, saas_preflight
from app.hosted_backup import OFFSITE_SUCCESS_MARKER_NAME


def _configure_ready_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control" / "saas.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    monkeypatch.setattr(config, "SAAS_PRICE_CENTS", 2000)
    monkeypatch.setattr(config, "SECRET_KEY", "not-a-default-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-password")
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_test")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20_monthly")
    monkeypatch.setattr(config, "SAAS_STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(config, "GMAIL_USER", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(config, "BACKUP_RCLONE_REMOTE", "misecrypt:mise")
    monkeypatch.setattr(config, "BACKUP_RCLONE_REMOTE_ENCRYPTED", True)
    generation = "20260711-120000-000001"
    generation_dir = tmp_path / "backups" / generation
    (generation_dir / "tenants").mkdir(parents=True, exist_ok=True)
    (generation_dir / "trash").mkdir()
    (generation_dir / "saas-control.db.gz").write_bytes(b"control")
    (generation_dir / "tenants" / "alpha.db.gz").write_bytes(b"alpha")
    parked = ".tenant-2-20260711120000"
    (generation_dir / "trash" / f"{parked}.db.gz").write_bytes(b"parked")
    (generation_dir / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": True,
                "stamp": generation,
                "control": "saas-control.db.gz",
                "expected_live": ["alpha"],
                "expected_parked": [parked],
                "captured_live": 1,
                "captured_parked": 1,
                "failures": [],
            }
        )
    )
    marker = tmp_path / "backups" / OFFSITE_SUCCESS_MARKER_NAME
    marker.write_text(generation)


def test_preflight_passes_launch_critical_checks_with_email_warning(tmp_path, monkeypatch):
    _configure_ready_saas(tmp_path, monkeypatch)

    report = saas_preflight.check_readiness(
        project_root=Path.cwd(),
        write_probes=True,
    )

    assert report["ready"] is True
    assert report["failures"] == 0
    assert report["warnings"] == 1
    assert next(c for c in report["checks"] if c["key"] == "email")["status"] == "warn"
    assert "READY" in saas_preflight.format_text(report)


def test_preflight_fails_missing_hosted_contract(tmp_path, monkeypatch):
    _configure_ready_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8400")
    monkeypatch.setattr(config, "SAAS_PRICE_CENTS", 1900)
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "")

    report = saas_preflight.check_readiness(project_root=Path.cwd(), write_probes=False)
    failed = {check["key"] for check in report["checks"] if check["status"] == "fail"}

    assert report["ready"] is False
    assert {"saas_mode", "price", "cookie_secure", "stripe_checkout"} <= failed


def test_preflight_requires_fresh_encrypted_offsite_success(tmp_path, monkeypatch):
    _configure_ready_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "BACKUP_RCLONE_REMOTE", "")

    report = saas_preflight.check_readiness(project_root=Path.cwd(), write_probes=True)

    check = next(item for item in report["checks"] if item["key"] == "offsite_backup")
    assert check["status"] == "fail"
    assert report["ready"] is False


def test_preflight_rejects_manifest_count_or_payload_mismatch(tmp_path, monkeypatch):
    _configure_ready_saas(tmp_path, monkeypatch)
    marker = tmp_path / "backups" / OFFSITE_SUCCESS_MARKER_NAME
    generation_dir = tmp_path / "backups" / marker.read_text()
    manifest_path = generation_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["captured_live"] = 2
    manifest_path.write_text(json.dumps(manifest))

    report = saas_preflight.check_readiness(
        project_root=Path.cwd(),
        write_probes=True,
    )

    check = next(item for item in report["checks"] if item["key"] == "offsite_backup")
    assert check["status"] == "fail"
    assert "count-mismatched" in check["detail"]

    manifest["captured_live"] = 1
    manifest_path.write_text(json.dumps(manifest))
    (generation_dir / "tenants" / "alpha.db.gz").unlink()
    report = saas_preflight.check_readiness(
        project_root=Path.cwd(),
        write_probes=True,
    )
    check = next(item for item in report["checks"] if item["key"] == "offsite_backup")
    assert check["status"] == "fail"
    assert "missing" in check["detail"]


def test_static_bootstrap_defers_runtime_evidence_but_not_remote_configuration(
    tmp_path,
    monkeypatch,
):
    _configure_ready_saas(tmp_path, monkeypatch)
    (tmp_path / "backups" / OFFSITE_SUCCESS_MARKER_NAME).unlink()

    report = saas_preflight.check_readiness(
        project_root=Path.cwd(),
        write_probes=True,
        require_runtime_evidence=False,
    )

    check = next(item for item in report["checks"] if item["key"] == "offsite_backup")
    assert check["status"] == "pass"
    assert report["ready"] is True
