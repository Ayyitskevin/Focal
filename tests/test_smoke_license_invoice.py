"""Licence ↔ invoice coupling (admin). Granting a licence from an invoice spawns a stub licence
linked by invoice_id (holder = the invoice's client, project set), redirects to the licence editor,
audits it, lists it on the invoice page, and surfaces a "via invoice" link on the company view —
all without touching the invoice total/line items (the money path stays sacred).
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.main import app


def _configure_tmp_db(tmp_path, monkeypatch):
    for attr, val in {
        "DATA_DIR": tmp_path,
        "DB_PATH": tmp_path / "mise.db",
        "MEDIA_DIR": tmp_path / "media",
        "ZIP_DIR": tmp_path / "zips",
        "TMP_DIR": tmp_path / "tmp",
        "BRAND_DIR": tmp_path / "brand",
        "RECEIPTS_DIR": tmp_path / "receipts",
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD": "test-pw",
    }.items():
        monkeypatch.setattr(config, attr, val)
    db.migrate()


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        assert r.status_code == 303
        yield client
    jobs.stop()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _invoice():
    cid = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Acme", "Acme Group"))
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "Spring shoot"))
    iid = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status) VALUES (?,?,?,?,?)",
        (pid, "inv-x", "Spring invoice", 120000, "draft"),
    )
    return cid, pid, iid


def test_grant_license_spawns_linked_stub(admin_client):
    cid, pid, iid = _invoice()
    r = admin_client.post(
        f"/admin/studio/invoices/{iid}/grant-license",
        data={"title": "Q1 social — US"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "/admin/studio/licenses/" in r.headers["location"]
    lic = db.one("SELECT * FROM licenses WHERE invoice_id=?", (iid,))
    assert lic is not None
    assert lic["holder_client_id"] == cid and lic["project_id"] == pid
    assert lic["title"] == "Q1 social — US"
    # the invoice total is untouched (money path sacred)
    assert db.one("SELECT total_cents FROM invoices WHERE id=?", (iid,))["total_cents"] == 120000
    # audited
    assert db.one(
        "SELECT 1 AS x FROM audit_log WHERE entity_type='license' AND entity_id=? AND action='create'",
        (lic["id"],),
    )


def test_grant_license_requires_title(admin_client):
    _, _, iid = _invoice()
    r = admin_client.post(
        f"/admin/studio/invoices/{iid}/grant-license", data={"title": "  "}, follow_redirects=False
    )
    assert r.status_code == 400
    assert db.one("SELECT COUNT(*) AS n FROM licenses")["n"] == 0


def test_invoice_page_lists_granted_license(admin_client):
    _, _, iid = _invoice()
    admin_client.post(
        f"/admin/studio/invoices/{iid}/grant-license", data={"title": "Menu print — 1yr"}
    )
    html = admin_client.get(f"/admin/studio/invoices/{iid}").text
    assert "Menu print — 1yr" in html and "Usage licences" in html


def test_company_view_shows_via_invoice(admin_client):
    cid, _, iid = _invoice()
    admin_client.post(f"/admin/studio/invoices/{iid}/grant-license", data={"title": "Social — US"})
    db.run(
        "UPDATE licenses SET status='active' WHERE invoice_id=?", (iid,)
    )  # company view = active
    html = admin_client.get(f"/admin/studio/companies/{cid}").text
    assert "Social — US" in html and "via invoice" in html


def test_grant_license_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.post(
            "/admin/studio/invoices/1/grant-license", data={"title": "x"}, follow_redirects=False
        )
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
