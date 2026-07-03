"""Pre-launch audit — the two money-boundary blockers.

Both were confirmed by a 3/3 adversarial verification pass:

1. pay.py double-charge: two DISTINCT completed checkout sessions for one
   invoice+kind (two tabs / a double-click) each recorded a satisfying payment,
   so a $2,000 invoice paid twice collected $4,000. UNIQUE(stripe_event_id) only
   guards Stripe RE-DELIVERIES of one event, not two independent sessions.

2. saas.py lockout: checkout.session.completed hardcoded plan_status="trialing",
   clobbering the real "active" status of a lapsed tenant who just re-subscribed
   (recovery checkout runs trial_days=0 -> Stripe activates immediately). Whichever
   of the two paired events landed last won, so a just-paid customer could be left
   "trialing" with an expired trial and locked out.
"""

import pytest

from app import config, db, saas
from app.public import pay

pytestmark = pytest.mark.unit


# ─────────────────────────── Blocker 1: pay.py double-charge ───────────────────────────


@pytest.fixture
def capture_alerts(monkeypatch):
    fired = []
    monkeypatch.setattr(pay.alerts, "security_alert", lambda text: fired.append(text))
    return fired


def _seed_invoice(total_cents, deposit_cents=0, *, status="sent", project_status="contract_signed"):
    db.run("INSERT INTO clients (name, email) VALUES (?,?)", ("Osteria Nova", "n@example.com"))
    client_id = db.one("SELECT id FROM clients ORDER BY id DESC LIMIT 1")["id"]
    db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)",
        (client_id, "Winter tasting", project_status),
    )
    project_id = db.one("SELECT id FROM projects ORDER BY id DESC LIMIT 1")["id"]
    slug = f"inv-dblchg-{project_id}"
    db.run(
        """INSERT INTO invoices (project_id, slug, title, total_cents, deposit_cents, status)
           VALUES (?,?,?,?,?,?)""",
        (project_id, slug, "Winter tasting", total_cents, deposit_cents, status),
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


def _payment_count(invoice_id):
    return db.one("SELECT COUNT(*) AS c FROM payments WHERE invoice_id=?", (invoice_id,))["c"]


def test_second_distinct_session_is_flagged_not_silently_double_counted(capture_alerts):
    invoice_id, project_id = _seed_invoice(200000, deposit_cents=0)

    first = pay._record_paid_session(*_event("evt_dbl_A", invoice_id, "full", 200000))
    assert first == {"ok": True}
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "paid"
    assert db.one("SELECT status FROM projects WHERE id=?", (project_id,))["status"] == (
        "retainer_paid"
    )
    assert capture_alerts == []

    # A SECOND independently-completed session (distinct event AND session id).
    second = pay._record_paid_session(*_event("evt_dbl_B", invoice_id, "full", 200000))
    assert second == {"ok": True, "duplicate_charge": True}
    # The money is on record (both charges really happened — needed for the refund),
    # but the invoice is NOT re-advanced and the operator is alerted to refund.
    assert _payment_count(invoice_id) == 2
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "paid"
    assert len(capture_alerts) == 1
    assert "double charge" in capture_alerts[0].lower() and str(invoice_id) in capture_alerts[0]


def test_same_event_redelivery_is_still_a_benign_duplicate(capture_alerts):
    invoice_id, _ = _seed_invoice(50000, deposit_cents=0)
    ev = _event("evt_redeliver", invoice_id, "full", 50000)

    assert pay._record_paid_session(*ev) == {"ok": True}
    # Stripe retries the SAME event id → UNIQUE(stripe_event_id) rolls the INSERT back;
    # not a double charge, just a re-delivery. One payment row, no double-charge alert.
    assert pay._record_paid_session(*ev) == {"ok": True, "duplicate": True}
    assert _payment_count(invoice_id) == 1
    assert capture_alerts == []


def test_underpaid_then_correct_retry_still_settles(capture_alerts):
    # Regression guard: the new conditional-advance keys on SETTLEMENT STATE, not on
    # the mere existence of a prior payment row — so an underpaid attempt followed by a
    # correct one must still settle (and must not be mistaken for a double charge).
    invoice_id, _ = _seed_invoice(90000, deposit_cents=30000)

    under = pay._record_paid_session(*_event("evt_under", invoice_id, "deposit", 100))
    assert under["underpaid"] is True
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "sent"

    good = pay._record_paid_session(*_event("evt_good", invoice_id, "deposit", 30000))
    assert good == {"ok": True}
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == (
        "deposit_paid"
    )
    assert _payment_count(invoice_id) == 2  # both attempts on record
    # The underpaid alert fired once; the correct retry did NOT raise a double-charge alert.
    assert len(capture_alerts) == 1 and "underpaid" in capture_alerts[0].lower()


# ─────────────────────── Blocker 2: saas.py post-recovery lockout ───────────────────────


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "audit-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _checkout_completed(event_id, tenant, sub_id="sub_rec", customer="cus_rec"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer,
                "subscription": sub_id,
                "metadata": {"tenant_id": str(tenant["id"]), "slug": tenant["slug"]},
            }
        },
    }


def _subscription_event(event_id, tenant, status, sub_id="sub_rec", customer="cus_rec"):
    return {
        "id": event_id,
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": sub_id,
                "status": status,
                "customer": customer,
                "metadata": {"tenant_id": str(tenant["id"]), "slug": tenant["slug"]},
            }
        },
    }


def _lapsed(tenant_id):
    """Force the trialing-but-expired state a recovery checkout starts from."""
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=?, stripe_subscription_id=NULL WHERE id=?",
            (saas._iso(saas._now() - saas.timedelta(days=5)), tenant_id),
        )


def test_recovery_active_survives_a_late_checkout_completed(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _lapsed(t["id"])
    assert saas.tenant_has_access(saas.tenant_by_slug("alpha")) is False

    # Recovery: Stripe activates immediately (trial_days=0). The subscription event
    # carries the truth; checkout.session.completed arrives LAST (the bug's trigger).
    saas._process_saas_event(_subscription_event("evt_sub", t, "active"))
    saas._process_saas_event(_checkout_completed("evt_co", t))

    row = saas.tenant_by_slug("alpha")
    assert row["plan_status"] == "active"  # not clobbered back to 'trialing'
    assert row["stripe_subscription_id"] == "sub_rec"
    assert saas.tenant_has_access(row) is True  # the just-paid customer is NOT locked out


def test_recovery_active_survives_either_event_order(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    _lapsed(t["id"])

    # The other order: checkout.session.completed first, then the subscription event.
    saas._process_saas_event(_checkout_completed("evt_co2", t))
    saas._process_saas_event(_subscription_event("evt_sub2", t, "active"))

    row = saas.tenant_by_slug("beta")
    assert row["plan_status"] == "active" and saas.tenant_has_access(row) is True


def test_new_signup_stays_trialing_with_subscription_attached(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("gamma", "Gamma Studio", "gamma@example.com", "secret123")
    # Fresh trial (create_tenant sets a future trial_ends_at + 'trialing').
    saas._process_saas_event(_checkout_completed("evt_co3", t, sub_id="sub_new"))
    saas._process_saas_event(_subscription_event("evt_sub3", t, "trialing", sub_id="sub_new"))

    row = saas.tenant_by_slug("gamma")
    assert row["plan_status"] == "trialing"
    assert row["stripe_subscription_id"] == "sub_new"
    assert saas.tenant_has_access(row) is True
