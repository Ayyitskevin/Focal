"""B2B invoicing essentials — PO number + net terms on invoices, company billing on clients.

DB-backed (real tmp DB + admin/public routes), same pattern as test_smoke_money_ops.py. Proves
the PO/net-terms persist on a draft, that sending stamps the net-terms due date (and leaves a
manual due date alone when there are no net terms), that company billing details persist and
surface on the client-facing invoice, and that duplication carries net terms but not the
order-specific PO. Nothing here sends or charges — invoices stay draft until marked sent (§11.4).
"""

import json
from datetime import date, timedelta

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


def _client(**cols) -> int:
    keys = ["name", *cols.keys()]
    vals = ["Blue Plate Co", *cols.values()]
    ph = ",".join("?" * len(keys))
    return db.run(f"INSERT INTO clients ({','.join(keys)}) VALUES ({ph})", tuple(vals))


def _draft_invoice(cid: int) -> int:
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "Spring menu"))
    return db.run(
        "INSERT INTO invoices (project_id, slug, title) VALUES (?,?,?)",
        (pid, "inv-b2b", "Invoice — Spring menu"),
    )


def _update_form(**over) -> dict:
    """A minimal valid invoice-edit form: one $500 line plus whatever fields are overridden."""
    form = {
        "title": "Invoice — Spring menu",
        "item_label_0": "Half-day shoot",
        "item_qty_0": "1",
        "item_price_0": "500.00",
        "deposit": "0",
        "net_days": "0",
        "po_number": "",
        "due_date": "",
        "terms": "",
    }
    form.update(over)
    return form


# --- PO + net terms persist -------------------------------------------------


def test_po_and_net_days_persist_on_draft(admin_client):
    iid = _draft_invoice(_client())
    r = admin_client.post(
        f"/admin/studio/invoices/{iid}", data=_update_form(po_number="PO-7781", net_days="30")
    )
    assert r.status_code in (200, 303)
    row = db.one("SELECT po_number, net_days FROM invoices WHERE id=?", (iid,))
    assert row["po_number"] == "PO-7781" and row["net_days"] == 30


@pytest.mark.parametrize("bad", ["-1", "400", "abc"])
def test_net_days_out_of_range_or_nonnumeric_rejected(admin_client, bad):
    iid = _draft_invoice(_client())
    r = admin_client.post(
        f"/admin/studio/invoices/{iid}", data=_update_form(net_days=bad), follow_redirects=False
    )
    assert r.status_code == 400
    # rejected write never lands
    assert db.one("SELECT net_days FROM invoices WHERE id=?", (iid,))["net_days"] == 0


# --- send stamps the net-terms due date -------------------------------------


def test_send_with_net_terms_stamps_due_date(admin_client):
    iid = _draft_invoice(_client())
    admin_client.post(f"/admin/studio/invoices/{iid}", data=_update_form(net_days="30"))
    r = admin_client.post(f"/admin/studio/invoices/{iid}/send", follow_redirects=False)
    assert r.status_code == 303
    row = db.one("SELECT status, due_date FROM invoices WHERE id=?", (iid,))
    assert row["status"] == "sent"
    assert row["due_date"] == (date.today() + timedelta(days=30)).isoformat()


def test_send_without_net_terms_keeps_manual_due_date(admin_client):
    iid = _draft_invoice(_client())
    admin_client.post(
        f"/admin/studio/invoices/{iid}", data=_update_form(net_days="0", due_date="2099-01-15")
    )
    admin_client.post(f"/admin/studio/invoices/{iid}/send", follow_redirects=False)
    row = db.one("SELECT due_date FROM invoices WHERE id=?", (iid,))
    assert row["due_date"] == "2099-01-15"  # net_days=0 -> manual date untouched


# --- company billing details on the client ----------------------------------


def test_client_billing_fields_persist(admin_client):
    cid = _client()
    r = admin_client.post(
        f"/admin/studio/clients/{cid}",
        data={
            "name": "Blue Plate Co",
            "company": "Blue Plate Co LLC",
            "billing_email": "ap@blueplate.example",
            "billing_address": "123 Market St\nAsheville, NC 28801",
            "tax_id": "EIN 12-3456789",
        },
    )
    assert r.status_code in (200, 303)
    row = db.one("SELECT billing_email, billing_address, tax_id FROM clients WHERE id=?", (cid,))
    assert row["billing_email"] == "ap@blueplate.example"
    assert "Market St" in row["billing_address"]
    assert row["tax_id"] == "EIN 12-3456789"


def test_public_invoice_shows_po_billing_and_tax(admin_client):
    cid = _client(
        company="Blue Plate Co LLC",
        billing_address="123 Market St\nAsheville NC",
        tax_id="EIN 12-3456789",
    )
    iid = _draft_invoice(cid)
    admin_client.post(f"/admin/studio/invoices/{iid}", data=_update_form(po_number="PO-7781"))
    admin_client.post(f"/admin/studio/invoices/{iid}/send", follow_redirects=False)
    slug = db.one("SELECT slug FROM invoices WHERE id=?", (iid,))["slug"]
    body = admin_client.get(f"/i/{slug}").text
    assert "PO-7781" in body
    assert "Market St" in body
    assert "EIN 12-3456789" in body


# --- duplication carries net terms, not the order-specific PO ----------------


def test_duplicate_copies_net_terms_not_po(admin_client):
    iid = _draft_invoice(_client())
    admin_client.post(
        f"/admin/studio/invoices/{iid}", data=_update_form(po_number="PO-7781", net_days="45")
    )
    admin_client.post(f"/admin/studio/invoices/{iid}/send", follow_redirects=False)
    r = admin_client.post(f"/admin/studio/invoices/{iid}/duplicate", follow_redirects=False)
    assert r.status_code == 303
    dup = db.one(
        "SELECT net_days, po_number, status FROM invoices WHERE id != ? ORDER BY id DESC LIMIT 1",
        (iid,),
    )
    assert dup["net_days"] == 45 and dup["po_number"] is None and dup["status"] == "draft"


def test_invoice_email_defaults_to_billing_contact(admin_client):
    cid = _client(company="Blue Plate Co LLC", billing_email="ap@blueplate.example")
    iid = _draft_invoice(cid)
    admin_client.post(f"/admin/studio/invoices/{iid}", data=_update_form())
    admin_client.post(f"/admin/studio/invoices/{iid}/send", follow_redirects=False)
    body = admin_client.get(f"/admin/studio/invoices/{iid}").text
    # the AP/billing contact pre-fills the invoice email recipient
    assert "ap@blueplate.example" in body


def test_create_invoice_from_offer_unaffected(admin_client):
    # regression guard: offer→invoice still builds a draft with default net terms / no PO
    cid = _client()
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "P"))
    db.run(
        "INSERT INTO galleries (slug, title, pin, project_id, plutus_offer_decision, "
        "plutus_last_bundles) VALUES (?,?,?,?, 'approved', ?)",
        (
            "gx",
            "GX",
            "1",
            pid,
            json.dumps(
                [
                    {
                        "sku": "PRINT",
                        "label": "Prints",
                        "line_items": [{"label": "Prints", "qty": 1, "unit_cents": 12000}],
                    }
                ]
            ),
        ),
    )
    gid = db.one("SELECT id FROM galleries WHERE slug='gx'")["id"]
    r = admin_client.post(f"/admin/studio/invoices/from-offer/{gid}", follow_redirects=False)
    assert r.status_code == 303
    inv = db.one("SELECT net_days, po_number FROM invoices WHERE project_id=?", (pid,))
    assert inv["net_days"] == 0 and inv["po_number"] is None
