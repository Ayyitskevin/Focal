"""Batch C / Slice C3: operator trial extension.

The audit's gap: the only recovery for a promising-but-expired trial was
immediate payment — no way to say "take another week." The console action
extends the trial from now (or the current end if still running), re-arms the
lifecycle emails for the new window, and leaves an audit line in the notes.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "c3-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _operator_request(path):
    cookie = (
        f"{security.ADMIN_COOKIE}="
        f"{security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [
                (b"host", b"mise.test"),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _set(tenant_id, **cols):
    sets = ", ".join(f"{k}=?" for k in cols)
    with saas.control_connect() as con:
        con.execute(f"UPDATE tenants SET {sets} WHERE id=?", (*cols.values(), tenant_id))


def _extend(tenant_id, days=7):
    return asyncio.run(
        saas.operator_extend_trial(
            _operator_request(f"/admin/saas/{tenant_id}/extend-trial"), tenant_id, days=days
        )
    )


def test_expired_trial_extension_restores_access_and_rearms_lifecycle(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(
        t["id"],
        trial_ends_at=saas._iso(saas._now() - saas.timedelta(days=10)),
        trial_reminder_sent_at=saas._iso(saas._now() - saas.timedelta(days=12)),
        winback_sent_at=saas._iso(saas._now() - saas.timedelta(days=6)),
    )
    assert saas.tenant_has_access(saas.tenant_by_slug("alpha")) is False

    resp = _extend(t["id"], days=7)
    assert resp.status_code == 303
    row = saas.tenant_by_slug("alpha")
    # Access is back, measured from NOW (not stacked on the long-lapsed end).
    assert saas.tenant_has_access(row) is True
    ends = saas._parse_iso(row["trial_ends_at"])
    assert 6 <= (ends - saas._now()).days <= 7
    # Lifecycle emails re-armed for the new window; audit line recorded.
    assert row["trial_reminder_sent_at"] is None and row["winback_sent_at"] is None
    assert "trial extended 7d by operator" in row["notes"]


def test_running_trial_extends_from_its_current_end(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    current_end = saas._now() + saas.timedelta(days=5)
    _set(t["id"], trial_ends_at=saas._iso(current_end), notes="existing note")

    _extend(t["id"], days=10)
    row = saas.tenant_by_slug("alpha")
    ends = saas._parse_iso(row["trial_ends_at"])
    assert 14 <= (ends - saas._now()).days <= 15  # 5 remaining + 10 granted
    assert row["notes"].startswith("existing note\n")  # appended, not clobbered


def test_days_are_clamped_and_only_trialing_qualifies(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _extend(t["id"], days=999)
    ends = saas._parse_iso(saas.tenant_by_slug("alpha")["trial_ends_at"])
    assert (ends - saas._now()).days <= 14 + saas.TRIAL_EXTEND_MAX_DAYS  # clamped to 30

    _set(t["id"], plan_status="active")
    with pytest.raises(HTTPException) as exc:
        _extend(t["id"])
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        _extend(999999)
    assert exc.value.status_code == 404
