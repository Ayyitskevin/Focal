#!/usr/bin/env python3
"""Seed a non-expiring reviewer demo studio (Conductor plan T3).

App Review and TestFlight need a studio a reviewer can sign into that stays
populated and never lapses. This script, run against a HOSTED control DB,
idempotently and convergently:

  1. creates (or safely reuses) a demo tenant identified by
     ``signup_source='reviewer-demo'`` — it REFUSES a slug that already belongs
     to a non-demo tenant, so it can never reactivate or inject data into a real
     studio;
  2. grants non-expiring access WITHOUT inventing revenue: it keeps the tenant in
     ``plan_status='trialing'`` with a far-future ``trial_ends_at``. A trialing
     tenant has full access (``tenant_has_access`` = ``trial_ends_at >= now``) but
     is counted as trial *pipeline*, never as paid MRR — unlike ``active``, which
     ``ops_snapshot`` books as $20/mo ``active_mrr_cents``; and
  3. seeds a realistic studio inside the tenant DB: the onboarding preset
     (client, gallery, project, proposal, contract, invoice) plus an owner task
     and a freshly-dated upcoming booking, refreshed on every run so the demo
     never decays past its booking date.

It touches ONLY the demo tenant. It does not deploy, call Stripe, or write to any
other tenant. Because it writes tenant subscription state, it ships as a reviewed
PR (red-light).

Usage (staging/local hosted stack):

    MISE_SAAS_MODE=true \\
    MISE_SAAS_ROOT_DOMAIN=mise.example.com \\
    MISE_SAAS_CONTROL_DB_PATH=/data/saas-control.db \\
    MISE_SAAS_TENANT_DATA_DIR=/data/tenants \\
    MISE_SECRET_KEY=... MISE_ADMIN_PASSWORD=... \\
    MISE_DEMO_TENANT_PRESET=wedding \\
    MISE_DEMO_TENANT_PASSWORD='<reviewer sign-in password>' \\
    python -m scripts.seed_demo_tenant

Env knobs:
    MISE_DEMO_TENANT_SLUG      default 'demo-tour'
    MISE_DEMO_TENANT_NAME      default 'Mise Demo Studio'
    MISE_DEMO_TENANT_EMAIL     default 'reviewer@demo.mise.local'
    MISE_DEMO_TENANT_PRESET    REQUIRED to be 'wedding' or 'fnb' (no default niche;
                               'neutral' is rejected — it has no seed preset yet,
                               see docs/NICHE-STORY-DECISION.md / T10)
    MISE_DEMO_TENANT_PASSWORD  required; the App Review sign-in password (>= 8 chars)
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db, passwords, saas, saas_demo, security  # noqa: E402

_DEMO_SOURCE = "reviewer-demo"
_EVENT_SLUG = "demo-consult"
_VALID_PRESETS = ("wedding", "fnb")
# A trialing tenant with this end date never lapses (access = trial_ends_at >= now)
# yet is never counted as paid MRR. Fixed literal so the script needs no wall clock.
_FAR_FUTURE = saas._iso(dt.datetime(2099, 1, 1, tzinfo=dt.UTC))


def _reset_demo_billing_and_credentials(
    tenant_id: int, *, studio_name: str, owner_email: str, password: str
) -> None:
    """Force the demo tenant to a non-expiring trial and (re)assert the advertised
    reviewer credentials, so a rerun with a new password/name/email converges.

    Uses a trialing far-future end date on purpose: it grants full access without
    booking fake ``active`` MRR."""
    with saas.control_connect() as con:
        con.execute(
            """UPDATE tenants
               SET plan_status='trialing', trial_ends_at=?,
                   studio_name=?, owner_email=?, admin_password_hash=?, updated_at=?
               WHERE id=?""",
            (
                _FAR_FUTURE,
                studio_name.strip(),
                owner_email.strip().lower(),
                passwords.hash_password(password),
                saas._iso(saas._now()),
                tenant_id,
            ),
        )


def _seed_owner_task(project_id: int | None) -> None:
    """One open owner task so the dashboard's task surface isn't empty. Idempotent
    on the title."""
    title = "Review the demo shot list before the shoot"
    if db.one("SELECT id FROM tasks WHERE title=?", (title,)):
        return
    db.run(
        "INSERT INTO tasks (title, due_date, project_id) VALUES (?, date('now','+5 days'), ?)",
        (title, project_id),
    )


def _converge_upcoming_booking(client_email: str, client_name: str) -> None:
    """Ensure exactly one confirmed booking dated in the near future for the demo
    client. Rebuilt every run so the demo never decays past a stale booking date
    (the native agenda hides bookings whose ``start_utc`` is in the past)."""
    client = db.one("SELECT id FROM clients WHERE email=?", (client_email,))
    project = (
        db.one("SELECT id FROM projects WHERE client_id=? ORDER BY id LIMIT 1", (client["id"],))
        if client
        else None
    )
    with db.tx() as con:
        event = con.execute("SELECT id FROM event_types WHERE slug=?", (_EVENT_SLUG,)).fetchone()
        if event is None:
            event_type_id = con.execute(
                """INSERT INTO event_types
                   (slug, name, description, duration_min, location, active, position)
                   VALUES (?,?,?,?,?,1,0)""",
                (_EVENT_SLUG, "Consultation call", "A short planning call.", 30, "Google Meet"),
            ).lastrowid
            for weekday in range(5):  # Mon-Fri 9-5, so the event reads as bookable
                con.execute(
                    """INSERT INTO availability_rules
                       (event_type_id, weekday, start_min, end_min) VALUES (?,?,?,?)""",
                    (event_type_id, weekday, 540, 1020),
                )
        else:
            event_type_id = event["id"]
        # Convergent: drop any prior demo booking (past or future) and insert one
        # fresh future slot, so re-running always leaves the agenda populated.
        con.execute("DELETE FROM bookings WHERE event_type_id=?", (event_type_id,))
        con.execute(
            """INSERT INTO bookings
               (token, event_type_id, name, email, start_utc, end_utc, tz, status,
                client_id, project_id)
               VALUES (?,?,?,?,
                       datetime('now','+14 days','start of day','+15 hours'),
                       datetime('now','+14 days','start of day','+15 hours','+30 minutes'),
                       'America/New_York','confirmed',?,?)""",
            (
                security.new_slug(),
                event_type_id,
                client_name,
                client_email,
                client["id"] if client else None,
                project["id"] if project else None,
            ),
        )


def seed_demo_tenant(
    *,
    slug: str,
    studio_name: str,
    owner_email: str,
    password: str,
    preset: str,
) -> dict:
    """Create-or-safely-reuse the demo tenant, grant non-expiring trial access, and
    seed a convergent studio. Safe to run repeatedly; refuses to touch a non-demo
    tenant at the same slug."""
    if not config.SAAS_MODE:
        raise SystemExit(
            "refusing to seed: MISE_SAAS_MODE is not true. This script only makes "
            "sense against a hosted control DB, never single-tenant."
        )
    if preset not in _VALID_PRESETS:
        raise SystemExit(
            f"MISE_DEMO_TENANT_PRESET must be one of {_VALID_PRESETS}; got {preset!r}. "
            "('neutral' has no seed preset — see docs/NICHE-STORY-DECISION.md / T10.)"
        )
    if len(password or "") < 8:
        raise SystemExit("MISE_DEMO_TENANT_PASSWORD must be at least 8 characters.")

    saas.migrate_control()
    tenant = saas.tenant_by_slug(slug)
    created = False
    if tenant is not None and (tenant.get("signup_source") or "") != _DEMO_SOURCE:
        # Fail closed: a real studio owns this slug. Never reactivate it or inject
        # demo data. The operator must pick a different MISE_DEMO_TENANT_SLUG.
        raise SystemExit(
            f"refusing to seed: slug {slug!r} already belongs to a non-demo tenant "
            f"(signup_source={tenant.get('signup_source')!r}). Choose another "
            "MISE_DEMO_TENANT_SLUG."
        )
    if tenant is None:
        tenant = saas.create_tenant(
            slug, studio_name, owner_email, password, signup_source=_DEMO_SOURCE
        )
        created = True
    saas.ensure_tenant_database(tenant)
    _reset_demo_billing_and_credentials(
        tenant["id"], studio_name=studio_name, owner_email=owner_email, password=password
    )

    with saas.tenant_runtime(tenant):
        preset_result = saas_demo.seed_preset(preset)
        demo_email = saas_demo.PRESETS[preset_result["preset"]]["email"]
        client_row = db.one("SELECT id, name FROM clients WHERE email=?", (demo_email,))
        project_row = (
            db.one(
                "SELECT id FROM projects WHERE client_id=? ORDER BY id LIMIT 1", (client_row["id"],)
            )
            if client_row
            else None
        )
        _seed_owner_task(project_row["id"] if project_row else None)
        if client_row:
            _converge_upcoming_booking(demo_email, client_row["name"])

    return {
        "slug": slug,
        "tenant_id": tenant["id"],
        "tenant_created": created,
        "plan_status": "trialing",
        "non_expiring": True,
        "preset": preset_result["preset"],
    }


def main() -> None:
    summary = seed_demo_tenant(
        slug=os.environ.get("MISE_DEMO_TENANT_SLUG", "demo-tour"),
        studio_name=os.environ.get("MISE_DEMO_TENANT_NAME", "Mise Demo Studio"),
        owner_email=os.environ.get("MISE_DEMO_TENANT_EMAIL", "reviewer@demo.mise.local"),
        password=os.environ.get("MISE_DEMO_TENANT_PASSWORD", ""),
        preset=os.environ.get("MISE_DEMO_TENANT_PRESET", ""),
    )
    host = (
        f"{summary['slug']}.{config.SAAS_ROOT_DOMAIN}"
        if config.SAAS_ROOT_DOMAIN
        else summary["slug"]
    )
    print("Demo studio ready (non-expiring trial; not counted as paid MRR).")
    print(f"  studio URL : https://{host}")
    print(f"  owner email: {os.environ.get('MISE_DEMO_TENANT_EMAIL', 'reviewer@demo.mise.local')}")
    print("  password   : from MISE_DEMO_TENANT_PASSWORD (not printed)")
    print(f"  preset     : {summary['preset']}")


if __name__ == "__main__":
    main()
