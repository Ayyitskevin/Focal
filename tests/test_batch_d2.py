"""Batch D / Slice D2: feedback triage state.

Once real beta feedback flows, an append-only panel stops being a queue and
starts being a guilt pile. Notes now carry status new/done: the console shows
the new-only queue, a one-click Done triages without deleting, and the archive
(including C4 exit reasons and the weekly digest's week counts) keeps everything.
"""

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch, migrate=True):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "d2-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    saas._MIGRATED_TENANT_DBS.clear()
    if migrate:
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


def _done(feedback_id):
    return asyncio.run(
        saas.operator_feedback_done(
            _operator_request(f"/admin/saas/feedback/{feedback_id}/done"), feedback_id
        )
    )


def test_done_leaves_the_queue_but_never_the_record(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.record_tenant_feedback(t["id"], "help", "Where are gallery PINs?")
    saas.record_tenant_feedback(t["id"], "billing", "Invoice CSV export?")
    queue = saas.recent_tenant_feedback(status="new")
    assert len(queue) == 2 and all(row["status"] == "new" for row in queue)

    resp = _done(queue[-1]["id"])  # triage the PIN question
    assert resp.status_code == 303 and "#feedback" in resp.headers["location"]
    assert [r["message"] for r in saas.recent_tenant_feedback(status="new")] == [
        "Invoice CSV export?"
    ]
    archive = saas.recent_tenant_feedback()  # no filter: everything survives
    assert len(archive) == 2
    assert {r["status"] for r in archive} == {"new", "done"}

    _done(queue[-1]["id"])  # double-click safe: idempotent, still 303
    with pytest.raises(HTTPException) as exc:
        _done(999999)
    assert exc.value.status_code == 404


def test_pre_d2_rows_backfill_to_new(tmp_path, monkeypatch):
    # A control DB written before D2: tenant_feedback exists without status.
    _configure_saas(tmp_path, monkeypatch, migrate=False)
    con = sqlite3.connect(tmp_path / "control.db")
    con.execute(
        """CREATE TABLE tenant_feedback (
               id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, page TEXT,
               message TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT (datetime('now')))"""
    )
    con.execute("INSERT INTO tenant_feedback (tenant_id, page, message) VALUES (1,'help','old')")
    con.commit()
    con.close()

    saas.migrate_control()  # adds status; the pre-existing note joins the queue
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")  # id 1
    queue = saas.recent_tenant_feedback(status="new")
    assert [r["message"] for r in queue] == ["old"] and queue[0]["status"] == "new"


def test_triage_is_operator_only(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.record_tenant_feedback(t["id"], "help", "note")
    fid = saas.recent_tenant_feedback()[0]["id"]
    # A VALID tenant admin on its own host: the platform console does not exist
    # there (require_platform_admin → 404), so tenants can't triage feedback.
    fp = security._pw_fp(t.get("admin_password_hash") or "")
    principal = f"tenant:{t['id']}:{t['slug']}:{fp}"
    cookie = f"{security.ADMIN_COOKIE}={security.sign(principal)}"
    host = f"{t['slug']}.mise.test"
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/admin/saas/feedback/{fid}/done",
            "query_string": b"",
            "headers": [
                (b"host", host.encode()),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    with saas.tenant_runtime(t):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(saas.operator_feedback_done(request, fid))
    assert exc.value.status_code == 404
    assert saas.recent_tenant_feedback()[0]["status"] == "new"  # untouched


def test_digest_still_counts_triaged_notes(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "operator@example.com")
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.record_tenant_feedback(t["id"], "help", "answered on day one")
    _done(saas.recent_tenant_feedback()[0]["id"])

    from app import mailer

    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, **kw: sent.append(body))
    assert saas.weekly_digest_sweep() == 1
    # The digest reports the WEEK, not the queue: triaged notes still count.
    assert "Feedback notes: 1" in sent[0]
