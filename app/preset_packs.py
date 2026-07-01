"""Niche starter packs for hosted Mise onboarding and retention."""

from __future__ import annotations

import json

from . import db

PRESET_PACKS = {
    "wedding": {
        "name": "Wedding Photographer",
        "description": "Packages, inquiry form, and follow-up rules for wedding bookings.",
        "packages": [
            (
                "essential-wedding",
                "Essential Wedding",
                250000,
                "Ceremony, portraits, and gallery delivery.",
            ),
            (
                "full-day-wedding",
                "Full Day Wedding",
                420000,
                "Full-day wedding story with engagement session.",
            ),
        ],
        "tags": [("Wedding", "#7C2F38"), ("Needs timeline", "#9a7a2c")],
        "workflow_rules": [
            ("Proposal follow-up", "proposal_sent", "Check in on unsigned proposal", 2),
            ("Contract nudge", "contract_sent", "Confirm the couple signed the agreement", 2),
            ("Final timeline", "contract_signed", "Send final timeline questionnaire", 21),
            ("Gallery review ask", "gallery_published", "Ask for review and favorite images", 3),
        ],
        "forms": [
            (
                "wedding-inquiry",
                "Wedding Inquiry",
                "lead",
                [
                    ("Wedding date", "date", 1, None),
                    ("Venue", "short_text", 0, None),
                    ("What matters most?", "long_text", 0, None),
                ],
            )
        ],
    },
    "fnb": {
        "name": "Food & Beverage",
        "description": "Restaurant/content-day packages and production follow-ups.",
        "packages": [
            ("menu-refresh", "Menu Refresh", 65000, "Menu and hero images for a seasonal update."),
            (
                "restaurant-launch",
                "Restaurant Launch",
                140000,
                "Launch-ready gallery for web, PR, and social.",
            ),
        ],
        "tags": [("Food & Beverage", "#2f5c45"), ("Retainer prospect", "#2f6d8a")],
        "workflow_rules": [
            ("Shot-list check", "proposal_sent", "Confirm menu, props, and shot list", 1),
            ("Usage notes", "invoice_paid", "Send usage rights and delivery notes", 1),
            (
                "Gallery usage follow-up",
                "gallery_published",
                "Check which images are being used",
                7,
            ),
        ],
        "forms": [
            (
                "fnb-content-inquiry",
                "Restaurant Content Inquiry",
                "lead",
                [
                    ("Target date", "date", 0, None),
                    ("Shoot type", "dropdown", 1, ["Menu refresh", "Launch", "Retainer"]),
                    ("Dishes / setups", "short_text", 0, None),
                    ("Usage needs", "long_text", 0, None),
                ],
            )
        ],
    },
    "portrait": {
        "name": "Portrait Studio",
        "description": "Simple portrait packages and client-prep reminders.",
        "packages": [
            (
                "portrait-mini",
                "Portrait Mini",
                25000,
                "Quick portrait session with a proofing gallery.",
            ),
            (
                "brand-portrait",
                "Brand Portrait",
                85000,
                "Personal-brand portraits with commercial usage.",
            ),
        ],
        "tags": [("Portrait", "#2f6d8a"), ("Fast delivery", "#9a7a2c")],
        "workflow_rules": [
            ("Prep guide", "contract_signed", "Send wardrobe and location prep guide", 2),
            ("Delivery follow-up", "gallery_published", "Ask client to pick favorites", 2),
        ],
        "forms": [],
    },
}


def install_pack(key: str) -> dict:
    pack = PRESET_PACKS[key]
    counts = {"packages": 0, "workflow_rules": 0, "tags": 0, "forms": 0}
    with db.tx() as con:
        for slug, name, price_cents, description in pack["packages"]:
            cur = con.execute(
                """INSERT OR IGNORE INTO packages
                   (slug, name, price_cents, description)
                   VALUES (?,?,?,?)""",
                (slug, name, price_cents, description),
            )
            counts["packages"] += cur.rowcount
        for name, trigger, title, delay_days in pack["workflow_rules"]:
            cur = con.execute(
                """INSERT OR IGNORE INTO workflow_rules
                   (name, trigger_key, action_key, task_title, delay_days)
                   VALUES (?,?, 'task', ?, ?)""",
                (name, trigger, title, delay_days),
            )
            counts["workflow_rules"] += cur.rowcount
        for name, color in pack["tags"]:
            cur = con.execute(
                "INSERT OR IGNORE INTO tags (name, color) VALUES (?,?)",
                (name, color),
            )
            counts["tags"] += cur.rowcount
        for slug, title, kind, fields in pack["forms"]:
            cur = con.execute(
                """INSERT OR IGNORE INTO forms (slug, title, kind, intro)
                   VALUES (?,?,?,?)""",
                (slug, title, kind, pack["description"]),
            )
            if cur.rowcount:
                counts["forms"] += 1
            form_id = con.execute("SELECT id FROM forms WHERE slug=?", (slug,)).fetchone()["id"]
            existing = {
                r["label"]
                for r in con.execute("SELECT label FROM form_fields WHERE form_id=?", (form_id,))
            }
            for i, (label, ftype, required, options) in enumerate(fields):
                if label in existing:
                    continue
                con.execute(
                    """INSERT INTO form_fields
                       (form_id, label, ftype, required, options, sort_order)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        form_id,
                        label,
                        ftype,
                        required,
                        json.dumps(options) if options else None,
                        i,
                    ),
                )
    return counts
