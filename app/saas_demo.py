"""Trial demo data for hosted Mise onboarding."""

import json

from . import db, security

PRESETS = {
    "fnb": {
        "client_name": "Mara Chen",
        "company": "Curate Demo Kitchen",
        "email": "demo+fnb@mise.local",
        "project": "Seasonal Menu Content Day",
        "gallery": "Seasonal Menu Preview",
        "status": "proposal_sent",
        "proposal_title": "Menu Content Day Proposal",
        "proposal_intro": "A fast, polished content day for a chef-led restaurant launch.",
        "items": [
            {"label": "Half-day menu photography", "qty": 1, "unit_cents": 65000},
            {"label": "Social crop delivery", "qty": 1, "unit_cents": 15000},
        ],
        "contract_title": "Restaurant Content Agreement",
        "invoice_title": "Deposit - Menu Content Day",
    },
    "wedding": {
        "client_name": "Harper Lane",
        "company": "",
        "email": "demo+wedding@mise.local",
        "project": "Harper and Lane Wedding Story",
        "gallery": "Spring Wedding Preview",
        "status": "contract_signed",
        "proposal_title": "Wedding Story Collection",
        "proposal_intro": "A complete wedding-day story with a polished proofing gallery.",
        "items": [
            {"label": "Wedding photography collection", "qty": 1, "unit_cents": 200000},
            {"label": "Engagement session", "qty": 1, "unit_cents": 35000},
        ],
        "contract_title": "Wedding Services Agreement",
        "invoice_title": "Retainer - Wedding Story Collection",
    },
}


def _total(items: list[dict]) -> int:
    return sum(int(i["qty"]) * int(i["unit_cents"]) for i in items)


def seed_preset(preset: str) -> dict:
    key = preset if preset in PRESETS else "fnb"
    data = PRESETS[key]
    existing = db.one("SELECT id FROM clients WHERE email=?", (data["email"],))
    if existing:
        return {"preset": key, "created": False, "client_id": existing["id"]}

    items_json = json.dumps(data["items"])
    total = _total(data["items"])
    deposit = max(0, total // 2)
    with db.tx() as con:
        client_id = con.execute(
            """INSERT INTO clients (name, company, email, notes, market)
               VALUES (?,?,?,?,?)""",
            (
                data["client_name"],
                data["company"] or None,
                data["email"],
                "Demo client created by the Mise hosted onboarding preset.",
                "demo",
            ),
        ).lastrowid
        gallery_id = con.execute(
            """INSERT INTO galleries
               (slug, title, client_name, pin, published, client_id, captions, type, require_pin)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                security.new_slug(),
                data["gallery"],
                data["client_name"],
                security.new_pin(),
                1,
                client_id,
                "Hero selects and caption notes will appear here after upload.",
                "gallery",
                1,
            ),
        ).lastrowid
        con.execute(
            "INSERT INTO sections (gallery_id, name, position) VALUES (?,?,?)",
            (gallery_id, "Highlights", 0),
        )
        project_id = con.execute(
            """INSERT INTO projects
               (client_id, title, status, gallery_id, notes, stage_changed_at)
               VALUES (?,?,?,?,?,datetime('now'))""",
            (
                client_id,
                data["project"],
                data["status"],
                gallery_id,
                "Demo project: proposal, contract, invoice, and gallery are pre-wired.",
            ),
        ).lastrowid
        proposal_status = "accepted" if key == "wedding" else "sent"
        contract_status = "signed" if key == "wedding" else "draft"
        invoice_status = "sent" if key == "wedding" else "draft"
        con.execute(
            """INSERT INTO proposals
               (project_id, slug, title, intro, line_items, total_cents,
                status, sent_at, accepted_at)
               VALUES (?,?,?,?,?,?,?,datetime('now'),
                       CASE WHEN ?='accepted' THEN datetime('now') ELSE NULL END)""",
            (
                project_id,
                security.new_slug(),
                data["proposal_title"],
                data["proposal_intro"],
                items_json,
                total,
                proposal_status,
                proposal_status,
            ),
        )
        con.execute(
            """INSERT INTO contracts
               (project_id, slug, title, body, body_sha256, status,
                sent_at, signed_at, signer_name)
               VALUES (?,?,?,?,?,?,CASE WHEN ?='signed' THEN datetime('now') ELSE NULL END,
                       CASE WHEN ?='signed' THEN datetime('now') ELSE NULL END,?)""",
            (
                project_id,
                security.new_slug(),
                data["contract_title"],
                "This demo agreement shows where your signed client contract lives in Mise.",
                None,
                contract_status,
                contract_status,
                contract_status,
                data["client_name"] if contract_status == "signed" else None,
            ),
        )
        con.execute(
            """INSERT INTO invoices
               (project_id, slug, title, line_items, total_cents, deposit_cents, status, sent_at)
               VALUES (?,?,?,?,?,?,?,CASE WHEN ?='sent' THEN datetime('now') ELSE NULL END)""",
            (
                project_id,
                security.new_slug(),
                data["invoice_title"],
                items_json,
                total,
                deposit,
                invoice_status,
                invoice_status,
            ),
        )
    return {"preset": key, "created": True, "client_id": client_id, "project_id": project_id}
