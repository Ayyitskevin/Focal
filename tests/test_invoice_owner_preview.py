"""Owner invoice preview must never mint client first-view state (#184).

Drives the real shipped paths:
- owner/admin preview (admin session on public /i/{slug}, and admin paper URL)
- client public GET /i/{slug} one-time sent → viewed

Does not re-implement status logic in the test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


def _configure(tmp_path, monkeypatch):
    for attr, val in {
        "DATA_DIR": tmp_path,
        "DB_PATH": tmp_path / "mise.db",
        "MEDIA_DIR": tmp_path / "media",
        "ZIP_DIR": tmp_path / "zips",
        "TMP_DIR": tmp_path / "tmp",
        "BRAND_DIR": tmp_path / "brand",
        "RECEIPTS_DIR": tmp_path / "receipts",
        "SECRET_KEY": "invoice-preview-secret",
        "ADMIN_PASSWORD": "owner-password",
        "BASE_URL": "https://studio.test",
        "SAAS_MODE": False,
    }.items():
        monkeypatch.setattr(config, attr, val)
    ratelimit._hits.clear()
    db.migrate()


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://studio.test") as client:
        r = client.post("/admin/login", data={"password": "owner-password"}, follow_redirects=False)
        assert r.status_code == 303
        yield client
    jobs.stop()
    ratelimit._hits.clear()


@pytest.fixture
def public_client(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://studio.test") as client:
        yield client
    jobs.stop()
    ratelimit._hits.clear()


def _seed_sent_invoice() -> dict:
    client_id = db.run(
        "INSERT INTO clients (name, company, email) VALUES (?,?,?)",
        ("Blue Plate", "Blue Plate Co", "ops@blueplate.example"),
    )
    project_id = db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)",
        (client_id, "Q4 Menu", "session_planning"),
    )
    invoice_id = db.run(
        """INSERT INTO invoices
             (project_id, slug, title, line_items, total_cents, deposit_cents,
              due_date, status, sent_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        (
            project_id,
            "inv-preview-sent",
            "November coverage",
            '[{"label":"Shoot","qty":1,"price_cents":250000}]',
            250000,
            0,
            "2026-06-15",
            "sent",
        ),
    )
    return {
        "client_id": client_id,
        "project_id": project_id,
        "invoice_id": invoice_id,
        "slug": "inv-preview-sent",
    }


def _row(invoice_id: int):
    return db.one(
        "SELECT status, viewed_at FROM invoices WHERE id=?",
        (invoice_id,),
    )


def test_owner_admin_session_public_path_does_not_mark_viewed(admin_client):
    """Logged-in operator opening /i/{slug} must leave sent + viewed_at null."""
    seed = _seed_sent_invoice()
    before = _row(seed["invoice_id"])
    assert before["status"] == "sent"
    assert before["viewed_at"] is None

    # Real shipped public route under an admin session (owner "Client view" link).
    page = admin_client.get(f"/i/{seed['slug']}")
    assert page.status_code == 200
    assert "November coverage" in page.text

    after = _row(seed["invoice_id"])
    assert after["status"] == "sent"
    assert after["viewed_at"] is None


def test_owner_admin_paper_preview_does_not_mark_viewed(admin_client):
    """Admin invoice paper is the non-mutating owner representation."""
    seed = _seed_sent_invoice()
    page = admin_client.get(f"/admin/studio/invoices/{seed['invoice_id']}")
    assert page.status_code == 200
    assert "Invoice preview" in page.text or "November coverage" in page.text

    after = _row(seed["invoice_id"])
    assert after["status"] == "sent"
    assert after["viewed_at"] is None


def test_client_public_path_marks_viewed_once(public_client):
    """Anonymous client GET drives the real one-time sent → viewed transition."""
    seed = _seed_sent_invoice()
    assert _row(seed["invoice_id"])["status"] == "sent"

    first = public_client.get(f"/i/{seed['slug']}")
    assert first.status_code == 200
    after_first = _row(seed["invoice_id"])
    assert after_first["status"] == "viewed"
    assert after_first["viewed_at"] is not None
    first_viewed_at = after_first["viewed_at"]

    # Second client GET must not invent a second first-view mutation.
    second = public_client.get(f"/i/{seed['slug']}")
    assert second.status_code == 200
    after_second = _row(seed["invoice_id"])
    assert after_second["status"] == "viewed"
    assert after_second["viewed_at"] == first_viewed_at


def test_owner_then_client_preserves_client_first_view_semantics(admin_client, public_client):
    """Owner preview first, then a real client open, still flips exactly once."""
    seed = _seed_sent_invoice()

    assert admin_client.get(f"/i/{seed['slug']}").status_code == 200
    assert _row(seed["invoice_id"])["status"] == "sent"

    assert public_client.get(f"/i/{seed['slug']}").status_code == 200
    row = _row(seed["invoice_id"])
    assert row["status"] == "viewed"
    assert row["viewed_at"] is not None


# --- #184 acceptance gaps: engagement history, the owner API, tenant isolation -------------------


def _engagement_snapshot(invoice_id: int) -> dict:
    """Everything an owner preview must leave alone: the whole invoice row and the send/audit logs."""
    return {
        "invoice": dict(db.one("SELECT * FROM invoices WHERE id=?", (invoice_id,))),
        "emails_log": db.one("SELECT COUNT(*) AS n FROM emails_log")["n"],
        "audit_log": db.one("SELECT COUNT(*) AS n FROM audit_log")["n"],
        "payments": db.one("SELECT COUNT(*) AS n FROM payments")["n"],
    }


def _owner_headers(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": {
                "installation_id": "9C21D2B4-5F81-4B21-8DFC-2E1A33F0A9C9",
                "name": "Owner iPhone",
                "platform": "ios",
                "app_version": "2.0",
            },
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_every_owner_representation_leaves_the_whole_row_and_the_logs_unchanged(admin_client):
    """The native AR-chase read (the owner preview's data), the admin paper and the admin session
    on the public URL: the invoice row (status, viewed_at, every column), reminders sent
    (emails_log), the audit log and payments are byte-for-byte what they were."""
    seed = _seed_sent_invoice()
    headers = _owner_headers(admin_client)
    before = _engagement_snapshot(seed["invoice_id"])

    chase = admin_client.get(f"/api/v1/companies/{seed['client_id']}/ar-chase", headers=headers)
    assert chase.status_code == 200, chase.text
    # positive control: the preview data really carries this invoice, so the read did reach it
    assert seed["invoice_id"] in {i["invoice_id"] for i in chase.json()["overdue_invoices"]}
    assert admin_client.get(f"/admin/studio/invoices/{seed['invoice_id']}").status_code == 200
    assert admin_client.get(f"/i/{seed['slug']}").status_code == 200

    assert _engagement_snapshot(seed["invoice_id"]) == before


def test_the_owner_api_refuses_an_unauthenticated_reader_and_changes_nothing(public_client):
    """Positive control for the test above: the AR-chase read is owner-authenticated."""
    seed = _seed_sent_invoice()
    before = _engagement_snapshot(seed["invoice_id"])

    refused = public_client.get(f"/api/v1/companies/{seed['client_id']}/ar-chase")

    assert refused.status_code in (401, 403)
    assert _engagement_snapshot(seed["invoice_id"]) == before


def test_hosted_owner_of_one_studio_is_a_client_of_another_and_each_flips_correctly(
    tmp_path, monkeypatch
):
    """Tenant isolation (hosted): studio A's owner session is the owner on A's host and does NOT
    flip A's invoice; the same cookie replayed on studio B's host is not B's owner, so a real
    client-style open there is still recorded exactly once."""
    from starlette.requests import Request

    from app import saas, security
    from app.public import pay

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SECRET_KEY", "isolation-secret")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()
    alpha = saas.create_tenant("alpha", "Alpha Studio", "a@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "b@example.com", "secret123")

    def request(host: str, cookie: str | None) -> Request:
        headers = [(b"host", host.encode())]
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/i/x",
                "query_string": b"",
                "headers": headers,
                "scheme": "https",
                "server": (host, 443),
                "client": ("127.0.0.1", 50000),
            }
        )

    fp = security._pw_fp(alpha.get("admin_password_hash") or "")
    alpha_cookie = f"{security.ADMIN_COOKIE}={security.sign(f'tenant:{alpha["id"]}:alpha:{fp}')}"

    def seeded_status_after(tenant: dict, host: str, cookie: str | None) -> str:
        with saas.tenant_runtime(tenant):
            seed = _seed_sent_invoice()
            pay._record_client_first_view(request(host, cookie), seed["invoice_id"], "sent")
            return _row(seed["invoice_id"])["status"]

    assert seeded_status_after(alpha, "alpha.mise.test", alpha_cookie) == "sent"  # A's owner
    assert seeded_status_after(beta, "beta.mise.test", alpha_cookie) == "viewed"  # not B's owner
