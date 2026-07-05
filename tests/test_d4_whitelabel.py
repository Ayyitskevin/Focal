"""D4 first-user friction fixes: white-label leaks + post-payment confirmation.

The correctness/security audits couldn't see these — they're multi-tenant identity
leaks and client-trust-moment gaps a real first hosted studio (and its paying
client) would hit on day one:
- A hosted tenant's client documents/letterhead must NOT stamp the operator's
  "Food & Beverage" discipline; render._site_specialty returns "" in a tenant
  context so hosted studios brand by name only (self-host keeps its specialty).
- A client returning from Stripe checkout must be reassured, not re-shown a Pay
  button (the webhook confirming the payment may not have landed yet).
"""

import asyncio
import json

import pytest
from starlette.requests import Request

from app import config, db, render
from app.public import pay

pytestmark = pytest.mark.unit


# ── render._site_specialty: the tenant-aware discipline subtitle ──────────────


def test_specialty_is_blank_for_a_hosted_tenant_but_set_for_self_host(monkeypatch):
    monkeypatch.setattr(config, "SITE_SPECIALTY", "Food & Beverage")

    # Self-host (no SaaS): the operator's own specialty shows.
    monkeypatch.setattr(config, "SAAS_MODE", False)
    assert render._site_specialty() == "Food & Beverage"

    # Hosted, inside a tenant: name-only, never the operator's discipline.
    monkeypatch.setattr(config, "SAAS_MODE", True)
    from app import saas

    monkeypatch.setattr(saas, "current_tenant", lambda: {"studio_name": "Aperture Studio"})
    assert render._site_specialty() == ""

    # Hosted, no tenant (root/marketing): falls back to config.
    monkeypatch.setattr(saas, "current_tenant", lambda: None)
    assert render._site_specialty() == "Food & Beverage"


# ── post-payment confirmation on the client invoice ──────────────────────────


def _seed_invoice(status="viewed"):
    db.run("INSERT INTO clients (name, email) VALUES (?,?)", ("Osteria Uno", "u@example.com"))
    cid = db.one("SELECT id FROM clients ORDER BY id DESC LIMIT 1")["id"]
    db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)",
        (cid, "Spring menu", "contract_signed"),
    )
    pid = db.one("SELECT id FROM projects ORDER BY id DESC LIMIT 1")["id"]
    slug = f"inv-d4-{pid}"
    db.run(
        """INSERT INTO invoices (project_id, slug, title, total_cents, deposit_cents, status,
              line_items) VALUES (?,?,?,?,?,?,?)""",
        (pid, slug, "Spring menu", 200000, 0, status, json.dumps([{"label": "Shoot", "qty": 1}])),
    )
    return slug


def _get_invoice(slug, thanks=0):
    req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/i/{slug}",
            "query_string": (f"thanks={thanks}".encode() if thanks else b""),
            "headers": [(b"host", b"studio.example.com"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("studio.example.com", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    return asyncio.run(pay.view_invoice(req, slug, thanks=thanks))


def test_stripe_return_reassures_and_suppresses_the_pay_button(monkeypatch):
    monkeypatch.setattr(pay.features, "stripe_enabled", lambda: True)
    slug = _seed_invoice(status="viewed")

    body = _get_invoice(slug, thanks=1).body.decode()
    assert "processing" in body and "no need to pay again" in body
    assert "client-pay-btn" not in body  # never re-show Pay right after paying


def test_normal_view_still_shows_the_pay_button(monkeypatch):
    monkeypatch.setattr(pay.features, "stripe_enabled", lambda: True)
    slug = _seed_invoice(status="viewed")

    body = _get_invoice(slug, thanks=0).body.decode()
    assert "client-pay-btn" in body
    assert "no need to pay again" not in body
