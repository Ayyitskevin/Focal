"""Invoices at /i/{slug} + Stripe Checkout + signature-verified webhook."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import alerts, config, db, features, jobs, security, urls, workflows
from ..render import templates

log = logging.getLogger("mise.public.pay")
router = APIRouter()


def _stripe_field(obj, key: str, default=None):
    """Read from dict-like Stripe objects without relying on `.get()`.

    stripe.StripeObject supports item access but intentionally treats unknown
    attributes as missing Stripe fields, so calling obj.get(...) raises
    AttributeError in webhook smoke tests.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError):
        return default


def _stripe():
    import stripe

    # Pin the API version to the tested contract (config.STRIPE_API_VERSION), so an
    # SDK bump can't silently shift request/response shapes on the client-money path.
    if config.STRIPE_API_VERSION:
        stripe.api_version = config.STRIPE_API_VERSION
    return stripe


def _invoice_or_404(slug: str) -> "db.sqlite3.Row":
    d = db.one(
        """SELECT i.*, p.title AS project_title, c.name AS client_name,
                         c.company, c.email AS client_email,
                         c.billing_address, c.tax_id
                  FROM invoices i
                  JOIN projects p ON p.id=i.project_id
                  JOIN clients c ON c.id=p.client_id
                  WHERE i.slug=?""",
        (slug,),
    )
    if not d or d["status"] == "draft":
        raise HTTPException(status_code=404)
    return d


def next_payment(d: "db.sqlite3.Row") -> tuple[int, str]:
    """(amount_cents, kind) still owed — (0, '') when settled."""
    if d["status"] == "paid":
        return 0, ""
    if d["status"] == "deposit_paid":
        return d["total_cents"] - d["deposit_cents"], "balance"
    if d["deposit_cents"]:
        return d["deposit_cents"], "deposit"
    return d["total_cents"], "full"


@router.get("/i/{slug}", response_class=HTMLResponse)
async def view_invoice(request: Request, slug: str):
    d = _invoice_or_404(slug)
    if d["status"] == "sent":
        db.run(
            "UPDATE invoices SET status='viewed', viewed_at=datetime('now') WHERE id=?", (d["id"],)
        )
        log.info("invoice %s viewed from %s", d["id"], security.client_ip(request))
    amount, kind = next_payment(d)
    paid_cents = db.one(
        """SELECT COALESCE(SUM(amount_cents), 0) AS c
                           FROM payments WHERE invoice_id=?""",
        (d["id"],),
    )["c"]
    return templates.TemplateResponse(
        request,
        "public/invoice.html",
        {
            "d": d,
            "items": json.loads(d["line_items"]),
            "amount_due": amount,
            "pay_kind": kind,
            "paid_cents": paid_cents,
            "payments_on": features.stripe_enabled(),
        },
    )


@router.get("/i/{slug}/receipt", response_class=HTMLResponse)
async def view_receipt(request: Request, slug: str):
    """Printable receipt — a read-only render of payments Stripe already
    recorded, so it can never disagree with what was charged. 404 until at
    least one payment exists."""
    d = _invoice_or_404(slug)
    payments = db.all_(
        """SELECT amount_cents, kind, created_at
                          FROM payments WHERE invoice_id=?
                          ORDER BY created_at""",
        (d["id"],),
    )
    if not payments:
        raise HTTPException(status_code=404)
    paid_cents = sum(p["amount_cents"] for p in payments)
    return templates.TemplateResponse(
        request,
        "public/receipt.html",
        {
            "d": d,
            "payments": payments,
            "paid_cents": paid_cents,
            "remaining_cents": max(0, d["total_cents"] - paid_cents),
        },
    )


@router.post("/i/{slug}/pay")
async def pay_invoice(request: Request, slug: str):
    d = _invoice_or_404(slug)
    amount, kind = next_payment(d)
    if not amount:
        raise HTTPException(status_code=400, detail="nothing due on this invoice")
    if not features.stripe_enabled():
        raise HTTPException(status_code=503, detail="online payment is not configured")
    label = {"deposit": "Deposit", "balance": "Balance", "full": "Payment"}[kind]
    metadata = {"invoice_id": str(d["id"]), "kind": kind}
    if config.SAAS_MODE:
        from .. import saas

        tenant = saas.current_tenant()
        if tenant:
            metadata["tenant_slug"] = tenant["slug"]
    base = urls.public_base_url(request)
    stripe_mod = _stripe()
    # Charge with the *serving context's* Stripe key: the operator's own in
    # single-tenant mode, the tenant's own in hosted mode. features.stripe_enabled()
    # above already fails closed (503) when hosted and the tenant has no key, so the
    # operator's platform key can never be used to charge a studio's client.
    session = stripe_mod.checkout.Session.create(
        api_key=features.client_stripe_secret_key(),
        mode="payment",
        payment_method_types=["card", "us_bank_account"],
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount,
                    "product_data": {"name": f"{label} — {d['title']}"},
                },
            }
        ],
        customer_email=d["client_email"] or None,
        metadata=metadata,
        success_url=f"{base}/i/{slug}?thanks=1",
        cancel_url=f"{base}/i/{slug}",
    )
    db.run("UPDATE invoices SET stripe_session_id=? WHERE id=?", (session.id, d["id"]))
    log.info("invoice %s checkout %s created (%s, %s cents)", d["id"], session.id, kind, amount)
    return RedirectResponse(session.url, status_code=303)


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    secrets = features.client_stripe_webhook_secrets()
    if not secrets:
        raise HTTPException(status_code=503, detail="webhook not configured")
    payload = await request.body()
    stripe_mod = _stripe()
    signature = request.headers.get("stripe-signature", "")
    event = None
    # Current secret first, then the previous one (ADR 0054 rotation grace) so a
    # payment whose checkout session predates a key rotation still records.
    for secret in secrets:
        try:
            event = stripe_mod.Webhook.construct_event(payload, signature, secret)
            break
        except (ValueError, stripe_mod.SignatureVerificationError):
            continue
    if event is None:
        log.warning(
            "stripe webhook signature failed against %d known secret(s) — "
            "if this repeats, a checkout may predate a key rotation",
            len(secrets),
        )
        raise HTTPException(status_code=400, detail="bad signature")

    if event["type"] not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        return {"ok": True, "ignored": event["type"]}
    session = event["data"]["object"]
    if session["payment_status"] != "paid":  # ACH settles via the async event
        return {"ok": True, "pending": True}
    metadata = _stripe_field(session, "metadata", {}) or {}
    tenant_slug = _stripe_field(metadata, "tenant_slug")
    if config.SAAS_MODE:
        from .. import saas

        if not saas.current_tenant():
            if not tenant_slug:
                log.error("saas invoice webhook missing tenant_slug metadata")
                raise HTTPException(status_code=400, detail="missing tenant metadata")
            with saas.tenant_runtime(tenant_slug):
                return _record_paid_session(event, session)
    return _record_paid_session(event, session)


def _expected_amount(d, kind: str) -> int:
    """Cents the invoice is owed for this payment kind — the server's own numbers,
    the mirror of next_payment()."""
    if kind == "deposit":
        return d["deposit_cents"]
    if kind == "balance":
        return d["total_cents"] - d["deposit_cents"]
    return d["total_cents"]  # full


def _record_paid_session(event, session):
    invoice_id = int(session["metadata"]["invoice_id"])
    kind = session["metadata"]["kind"]
    d = db.one("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not d:
        log.error("stripe webhook for unknown invoice %s", invoice_id)
        raise HTTPException(status_code=404)
    # Amount reconciliation (ADR 0064): mark the invoice paid only if Stripe's
    # authoritative amount_total actually covers what the invoice is owed for this
    # kind. We create sessions with the server-derived amount, so a mismatch means a
    # bug or a discounted/tampered session — the payment is still RECORDED (money
    # arrived; never lose it) but the invoice is NOT auto-satisfied, and the operator
    # is alerted to reconcile by hand. >= tolerates overpayment/rounding.
    amount = int(session["amount_total"])
    expected = _expected_amount(d, kind)
    amount_ok = amount >= expected
    # Record the payment and advance invoice + project state as one atomic unit:
    # a crash between these writes would otherwise leave the payment logged but the
    # invoice unpaid, and Stripe's retry would short-circuit on the duplicate event
    # id (below) without ever repairing it. The INSERT runs first, so a duplicate
    # event rolls the whole tx back with nothing else written.
    duplicate = False
    already_settled = False
    try:
        with db.tx() as con:
            con.execute(
                """INSERT INTO payments (invoice_id, stripe_event_id, stripe_session_id,
                      amount_cents, kind) VALUES (?,?,?,?,?)""",
                (invoice_id, event["id"], session["id"], amount, kind),
            )
            if not amount_ok:
                log.error(
                    "stripe session %s underpaid invoice %s: got %s, expected %s (kind=%s) — "
                    "recorded but NOT marking paid",
                    session["id"],
                    invoice_id,
                    amount,
                    expected,
                    kind,
                )
                alerts.security_alert(
                    f"Underpaid invoice {invoice_id}: received {amount}¢ < {expected}¢ "
                    f"({kind}) — payment recorded, needs manual review."
                )
            else:
                # The status advance is CONDITIONAL on the obligation not already being
                # met, and that is the idempotency guard against a SECOND DISTINCT
                # checkout session (two tabs, a double-click) for the same invoice+kind.
                # UNIQUE(stripe_event_id) only catches Stripe RE-DELIVERIES of one event;
                # two independently-completed sessions have distinct event AND session
                # ids, so both INSERT. db.tx() serializes the two writers (deferred SQLite
                # write lock), so the second UPDATE reads the first's committed status:
                # rowcount 0 means the kind was already settled -> the client was charged
                # twice. The payment row still stands (never hide a real charge), but the
                # invoice/project must NOT re-advance and the operator is alerted to refund.
                if kind == "deposit":
                    changed = con.execute(
                        "UPDATE invoices SET status='deposit_paid' "
                        "WHERE id=? AND status NOT IN ('deposit_paid','paid')",
                        (invoice_id,),
                    ).rowcount
                else:
                    changed = con.execute(
                        "UPDATE invoices SET status='paid', paid_at=datetime('now') "
                        "WHERE id=? AND status != 'paid'",
                        (invoice_id,),
                    ).rowcount
                already_settled = changed == 0
            # Payment landed → advance the project to Retainer Paid (the funnel's
            # money gate). Only moves forward from pre-payment stages; never rewinds
            # a project already at session planning / closed / archived.
            if amount_ok and not already_settled:
                con.execute(
                    """UPDATE projects SET status='retainer_paid',
                          stage_changed_at=datetime('now') WHERE id=?
                          AND status IN ('inquiry_received','consultation_call',
                                         'proposal_sent','contract_signed')""",
                    (d["project_id"],),
                )
    except db.sqlite3.IntegrityError:
        duplicate = True  # Stripe retries. Workflow calls below are idempotent too.
    if not amount_ok:
        # Money arrived but doesn't cover what this kind owes: the payment row stands
        # (a real charge is never dropped) and the operator has been alerted, but we
        # deliberately do NOT advance the invoice/project or fire the payment
        # workflows. An underpayment is not a settled deposit/balance, and letting the
        # funnel auto-advance to retainer_paid or a client-facing "deposit received"
        # workflow fire is the exact false-settlement this reconciliation guards.
        return {"ok": True, "underpaid": True, "duplicate": duplicate}
    if already_settled:
        # A second distinct paid session for an obligation already settled = a double
        # charge. The payment is on record (for the receipt + refund trail); alert the
        # operator to refund, but do NOT re-fire the payment workflows or re-advance.
        log.error(
            "duplicate paid session %s for invoice %s (%s): already settled — "
            "recorded, likely double charge needing a refund",
            session["id"],
            invoice_id,
            kind,
        )
        alerts.security_alert(
            f"Possible double charge on invoice {invoice_id} ({kind}): a second payment of "
            f"{amount}¢ arrived after it was already settled — recorded, needs a refund."
        )
        return {"ok": True, "duplicate_charge": True}
    trigger_key = "deposit_paid" if kind == "deposit" else "invoice_paid"
    event_kind = "payment"
    workflows.record_project_event(
        d["project_id"],
        event_kind,
        f"Payment received: {kind} on invoice {d['title']}",
        ref_kind="invoice",
        ref_id=invoice_id,
        dedupe_key=f"{trigger_key}:{event['id']}",
    )
    workflows.fire_workflow(trigger_key, d["project_id"], ref_kind="invoice", ref_id=invoice_id)
    if trigger_key == "deposit_paid":
        workflows.fire_workflow(
            "status:retainer_paid", d["project_id"], ref_kind="invoice", ref_id=invoice_id
        )
    if duplicate:
        return {"ok": True, "duplicate": True}
    jobs.enqueue("notion_sync_invoice", {"invoice_id": invoice_id})
    log.info(
        "invoice %s payment recorded: %s %s cents (event %s)",
        invoice_id,
        kind,
        session["amount_total"],
        event["id"],
    )
    return {"ok": True}
