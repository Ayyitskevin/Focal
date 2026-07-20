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
