"""Security Slice 4 (ADR 0064): invoice-payment amount reconciliation.

The load-bearing property: Stripe's authoritative ``amount_total`` — not the
metadata ``kind`` alone — decides whether an invoice is marked paid. A session
whose paid amount does not cover what the invoice owes for that kind (a bug, a
discounted/tampered checkout) still has its payment RECORDED (a real charge is
never dropped) but does NOT auto-satisfy the invoice, does NOT advance the
project funnel, and DOES alert the operator to reconcile by hand.

The existing webhook smoke test (test_smoke.test_invoice_lifecycle) already
covers signature rejection and duplicate-event idempotency against the live
route; these tests pin the reconciliation branch that sits underneath it.
"""

import pytest

from app import db
from app.public import pay

pytestmark = pytest.mark.unit


# --- _expected_amount: the server's own numbers, mirror of next_payment -----


def _inv(*, total_cents, deposit_cents=0):
    return {"total_cents": total_cents, "deposit_cents": deposit_cents}


def test_expected_amount_deposit_is_the_deposit():
    assert pay._expected_amount(_inv(total_cents=90000, deposit_cents=30000), "deposit") == 30000


def test_expected_amount_balance_is_total_minus_deposit():
    # Must never be the full total again — that would re-bill the deposit.
    assert pay._expected_amount(_inv(total_cents=90000, deposit_cents=30000), "balance") == 60000


def test_expected_amount_full_is_the_total():
    assert pay._expected_amount(_inv(total_cents=90000, deposit_cents=30000), "full") == 90000


# --- reconciliation against a seeded invoice --------------------------------


def _seed_invoice(total_cents, deposit_cents=0, *, status="sent", project_status="contract_signed"):
    """Insert client → project → invoice; return (invoice_id, project_id)."""
    db.run("INSERT INTO clients (name, email) VALUES (?,?)", ("Osteria Vega", "v@example.com"))
    client_id = db.one("SELECT id FROM clients ORDER BY id DESC LIMIT 1")["id"]
    db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)",
        (client_id, "Autumn tasting", project_status),
    )
    project_id = db.one("SELECT id FROM projects ORDER BY id DESC LIMIT 1")["id"]
    slug = f"inv-slice4-{project_id}"
    db.run(
        """INSERT INTO invoices (project_id, slug, title, total_cents, deposit_cents, status)
           VALUES (?,?,?,?,?,?)""",
        (project_id, slug, "Autumn tasting", total_cents, deposit_cents, status),
    )
    invoice_id = db.one("SELECT id FROM invoices WHERE slug=?", (slug,))["id"]
    return invoice_id, project_id


def _event(event_id, invoice_id, kind, amount):
    return (
        {"id": event_id},
        {
            "id": f"cs_{event_id}",
            "amount_total": amount,
            "metadata": {"invoice_id": str(invoice_id), "kind": kind},
        },
    )


@pytest.fixture
def capture_alerts(monkeypatch):
    fired = []
    monkeypatch.setattr(pay.alerts, "security_alert", lambda text: fired.append(text))
    return fired


def test_underpaid_deposit_records_payment_but_does_not_mark_paid(capture_alerts):
    invoice_id, project_id = _seed_invoice(90000, deposit_cents=30000)
    event, session = _event("evt_under_1", invoice_id, "deposit", 100)  # $1 vs $300 owed

    result = pay._record_paid_session(event, session)

    # The money is never lost: the payment row exists at the real amount.
    row = db.one(
        "SELECT amount_cents, kind FROM payments WHERE stripe_event_id=?", ("evt_under_1",)
    )
    assert row is not None and row["amount_cents"] == 100 and row["kind"] == "deposit"
    # But the invoice is NOT auto-satisfied and the funnel does NOT advance.
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "sent"
    assert db.one("SELECT status FROM projects WHERE id=?", (project_id,))["status"] == (
        "contract_signed"
    )
    # The operator is alerted to reconcile by hand.
    assert len(capture_alerts) == 1 and str(invoice_id) in capture_alerts[0]
    assert result["underpaid"] is True


def test_correct_deposit_marks_paid_and_advances_and_does_not_alert(capture_alerts):
    invoice_id, project_id = _seed_invoice(90000, deposit_cents=30000)
    event, session = _event("evt_ok_1", invoice_id, "deposit", 30000)  # exact deposit

    pay._record_paid_session(event, session)

    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == (
        "deposit_paid"
    )
    assert db.one("SELECT status FROM projects WHERE id=?", (project_id,))["status"] == (
        "retainer_paid"
    )
    assert capture_alerts == []


def test_overpayment_still_settles_and_does_not_alert(capture_alerts):
    # >= tolerates overpayment / rounding — an over-payment must not strand the invoice.
    invoice_id, _ = _seed_invoice(50000, deposit_cents=0)
    event, session = _event("evt_over_1", invoice_id, "full", 50001)

    pay._record_paid_session(event, session)

    d = db.one("SELECT status, paid_at FROM invoices WHERE id=?", (invoice_id,))
    assert d["status"] == "paid" and d["paid_at"]
    assert capture_alerts == []


def test_duplicate_underpaid_event_credits_once_and_alerts_once(capture_alerts):
    invoice_id, _ = _seed_invoice(90000, deposit_cents=30000)
    event, session = _event("evt_under_dup", invoice_id, "deposit", 100)

    first = pay._record_paid_session(event, session)
    second = pay._record_paid_session(event, session)  # Stripe retry, same event id

    # Exactly one payment row (UNIQUE stripe_event_id), and the retry is a no-op that
    # neither double-credits nor re-alerts (the INSERT rolls back before the alert).
    assert (
        db.one("SELECT COUNT(*) AS n FROM payments WHERE stripe_event_id=?", ("evt_under_dup",))[
            "n"
        ]
        == 1
    )
    assert first["underpaid"] is True and second["duplicate"] is True
    assert len(capture_alerts) == 1
