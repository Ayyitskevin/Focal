#!/usr/bin/env python3
"""Seed a non-expiring reviewer demo studio (Conductor plan T3).

App Review and TestFlight need a studio a reviewer can sign into that stays
populated and never lapses mid-review. This script, run against a HOSTED control
DB, idempotently:

  1. creates a demo tenant (slug + owner from env; password NEVER hardcoded),
  2. marks its subscription ``plan_status='active'`` so the trial-expiry sweep
     skips it forever — no schema flag / migration needed (a ``trialing`` demo
     would 402 after ``MISE_SAAS_TRIAL_DAYS``), and
  3. seeds a realistic studio inside the tenant DB: the existing onboarding
     preset (client, gallery, project, proposal, contract, invoice) plus one
     event type and one upcoming booking, so the Calendar/Bookings screens are
     not empty for the store screenshots.

It touches ONLY the demo tenant. It does not deploy, call Stripe, or write to any
other tenant. Setting ``plan_status`` is money-adjacent, so this ships as a
reviewed PR (red-light) even though it's operator tooling.

Usage (staging/local hosted stack):

    MISE_SAAS_MODE=true \\
    MISE_SAAS_ROOT_DOMAIN=mise.example.com \\
    MISE_SAAS_CONTROL_DB_PATH=/data/saas-control.db \\
    MISE_SAAS_TENANT_DATA_DIR=/data/tenants \\
    MISE_SECRET_KEY=... MISE_ADMIN_PASSWORD=... \\
    MISE_DEMO_TENANT_PASSWORD='<reviewer sign-in password>' \\
    python -m scripts.seed_demo_tenant

Env knobs (all optional except the password):
    MISE_DEMO_TENANT_SLUG      default 'demo-tour'
    MISE_DEMO_TENANT_NAME      default 'Mise Demo Studio'
    MISE_DEMO_TENANT_EMAIL     default 'reviewer@demo.mise.local'
    MISE_DEMO_TENANT_PRESET    'wedding' (default) or 'fnb'
    MISE_DEMO_TENANT_PASSWORD  required; the App Review sign-in password
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db, saas, saas_demo, security  # noqa: E402

_EVENT_SLUG = "demo-consult"


def _seed_scheduling(client_email: str, client_name: str) -> bool:
    """Add one event type + one upcoming confirmed booking for the demo client.

    Idempotent on the event-type slug, so re-running the seed does not pile up
    bookings. Returns True when it created the booking this run."""
    if db.one("SELECT id FROM event_types WHERE slug=?", (_EVENT_SLUG,)):
        return False
    client = db.one("SELECT id FROM clients WHERE email=?", (client_email,))
    project = (
        db.one(
            "SELECT id FROM projects WHERE client_id=? ORDER BY id LIMIT 1",
            (client["id"],),
        )
        if client
        else None
    )
    with db.tx() as con:
        event_type_id = con.execute(
            """INSERT INTO event_types
               (slug, name, description, duration_min, location, active, position)
               VALUES (?,?,?,?,?,1,0)""",
            (
                _EVENT_SLUG,
                "Consultation call",
                "A short planning call before the shoot.",
                30,
                "Google Meet",
            ),
        ).lastrowid
        # Weekday 9-5 availability so the event type reads as bookable if a
        # reviewer opens the scheduling flow. (weekday: 0=Mon .. 4=Fri)
        for weekday in range(5):
            con.execute(
                """INSERT INTO availability_rules
                   (event_type_id, weekday, start_min, end_min) VALUES (?,?,?,?)""",
                (event_type_id, weekday, 540, 1020),
            )
        # One confirmed booking ~10 days out at 15:00 UTC, linked to the demo
        # client/project so it shows in the owner's Bookings agenda.
        con.execute(
            """INSERT INTO bookings
               (token, event_type_id, name, email, start_utc, end_utc, tz, status,
                client_id, project_id)
               VALUES (?,?,?,?,
                       datetime('now','+10 days','start of day','+15 hours'),
                       datetime('now','+10 days','start of day','+15 hours','+30 minutes'),
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
    return True


def seed_demo_tenant(
    *,
    slug: str,
    studio_name: str,
    owner_email: str,
    password: str,
    preset: str,
) -> dict:
    """Create-or-reuse the demo tenant, force it active, and seed its studio.

    Returns a summary dict. Safe to run repeatedly."""
    if not config.SAAS_MODE:
        raise SystemExit(
            "refusing to seed: MISE_SAAS_MODE is not true. This script only "
            "makes sense against a hosted control DB, never single-tenant."
        )
    if len(password or "") < 8:
        raise SystemExit("MISE_DEMO_TENANT_PASSWORD must be at least 8 characters.")

    saas.migrate_control()
    tenant = saas.tenant_by_slug(slug)
    created = False
    if tenant is None:
        tenant = saas.create_tenant(
            slug,
            studio_name,
            owner_email,
            password,
            signup_source="reviewer-demo",
        )
        created = True
    # Non-expiring: an 'active' tenant is exempt from the trial sweep. Done every
    # run so an older trialing demo is repaired in place.
    saas.update_tenant_billing(tenant["id"], plan_status="active")
    saas.ensure_tenant_database(tenant)

    with saas.tenant_runtime(tenant):
        preset_result = saas_demo.seed_preset(preset)
        client_row = db.one(
            "SELECT id, name FROM clients WHERE email=?",
            (saas_demo.PRESETS[preset_result["preset"]]["email"],),
        )
        booking_created = (
            _seed_scheduling(
                saas_demo.PRESETS[preset_result["preset"]]["email"],
                client_row["name"] if client_row else studio_name,
            )
            if client_row
            else False
        )

    return {
        "slug": slug,
        "tenant_id": tenant["id"],
        "tenant_created": created,
        "plan_status": "active",
        "preset": preset_result["preset"],
        "studio_seeded": preset_result.get("created", False),
        "booking_seeded": booking_created,
    }


def main() -> None:
    password = os.environ.get("MISE_DEMO_TENANT_PASSWORD", "")
    summary = seed_demo_tenant(
        slug=os.environ.get("MISE_DEMO_TENANT_SLUG", "demo-tour"),
        studio_name=os.environ.get("MISE_DEMO_TENANT_NAME", "Mise Demo Studio"),
        owner_email=os.environ.get("MISE_DEMO_TENANT_EMAIL", "reviewer@demo.mise.local"),
        password=password,
        preset=os.environ.get("MISE_DEMO_TENANT_PRESET", "wedding"),
    )
    host = (
        f"{summary['slug']}.{config.SAAS_ROOT_DOMAIN}"
        if config.SAAS_ROOT_DOMAIN
        else summary["slug"]
    )
    print("Demo studio ready.")
    print(f"  studio URL : https://{host}")
    print(f"  owner email: {os.environ.get('MISE_DEMO_TENANT_EMAIL', 'reviewer@demo.mise.local')}")
    print("  password   : from MISE_DEMO_TENANT_PASSWORD (not printed)")
    print(f"  plan_status: {summary['plan_status']} (never expires)")
    print(f"  preset     : {summary['preset']}  ·  booking seeded: {summary['booking_seeded']}")


if __name__ == "__main__":
    main()
