"""Commercial-spine derivations (F&B operator surfaces, ADRs 0039–0046).

These read-only computations — the company next-action ranking, the studio
commercial action queue, AR chase assist + cadence, and project closeout
readiness — were extracted verbatim from ``app/admin/studio.py`` so that both
the HTML admin routes and the native ``/api/v1`` router can call one
implementation instead of the API reaching into an admin module (queue S7;
IOS-API-V1 backend note 6). Behavior is unchanged.

Nothing here mutates state: no tasks, sends, charges, publishes, or closes.

Import note: ``app/admin/studio.py`` re-imports the six functions it still
calls from here, and a few functions here need studio's un-moved helpers
(``_today``, ``get_client``, ``_group_ids``, ``_company_billing_readiness``,
``AR_CHASE_SUBJECT_PREFIX``). To keep the two modules acyclic, this module's
top level imports NO studio; the three functions that need it import
``studio`` lazily inside their bodies. So ``commercial`` always loads fully on
first import regardless of order, and studio's re-import can never see a
half-initialized ``commercial``.
"""

from __future__ import annotations

import datetime as dt

from fastapi import HTTPException

from . import config, db
from .admin import common


def _format_cents(cents: int) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def _company_overdue_rows(group_ids: list[int], today: dt.date) -> list[dict]:
    """Past-due issued invoices for a company group with an actual open balance.

    The due boundary follows the studio wall-clock. Owed is total minus recorded payments, with a
    deposit-paid fallback for old rows that have status but no payment event. Pure read; used by
    the company view, Activity action queue, and AR chase assist so those surfaces do not drift.
    """
    if not group_ids:
        return []
    ph = ",".join("?" * len(group_ids))
    rows = db.all_(
        f"""SELECT i.id, i.slug, i.title, i.status, i.due_date, i.total_cents,
                   i.deposit_cents, p.id AS project_id, p.title AS project_title,
                   c.id AS client_id, c.name AS client_name, c.company, c.email AS client_email,
                   c.billing_email,
                   (SELECT COALESCE(SUM(pm.amount_cents), 0)
                    FROM payments pm WHERE pm.invoice_id=i.id) AS paid_cents
            FROM invoices i
            JOIN projects p ON p.id=i.project_id
            JOIN clients c ON c.id=p.client_id
            WHERE p.client_id IN ({ph})
              AND i.status IN ('sent','viewed','deposit_paid')
              AND i.due_date IS NOT NULL
              AND i.due_date < ?
            ORDER BY i.due_date, i.id""",
        (*group_ids, today.isoformat()),
    )
    overdue: list[dict] = []
    for row in rows:
        item = dict(row)
        paid_cents = item["paid_cents"] or 0
        if not paid_cents and item["status"] == "deposit_paid":
            paid_cents = item["deposit_cents"] or 0
        owed_cents = max((item["total_cents"] or 0) - paid_cents, 0)
        if owed_cents <= 0:
            continue
        item["paid_cents"] = paid_cents
        item["owed_cents"] = owed_cents
        overdue.append(item)
    return overdue


def _company_ar_contact(c, rows: list[dict]) -> str:
    for value in (c["billing_email"], c["email"]):
        if value:
            return value
    for row in rows:
        for value in (row["billing_email"], row["client_email"]):
            if value:
                return value
    return ""


def _ar_chase_history(client_id: int, today: dt.date | None = None) -> dict:
    """Latest manual AR chase send for a company, derived from the existing send log.

    No schema or task lifecycle: the assist's default subject prefix is the marker that separates
    company AR follow-ups from other catch-all `emails_log` rows.
    """
    from .admin import studio

    today = today or studio._today()
    row = db.one(
        """SELECT id, to_email, subject, created_at
           FROM emails_log
           WHERE doc_kind='other' AND doc_id=? AND subject LIKE ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (client_id, f"{studio.AR_CHASE_SUBJECT_PREFIX}%"),
    )
    if not row:
        return {
            "status": "never",
            "followup_due": True,
            "days_since": None,
            "last_sent_at": None,
            "last_to": "",
            "next_due_on": None,
            "action_meta": "never chased",
            "detail": "No AR chase logged for this company.",
        }
    try:
        sent_on = dt.date.fromisoformat((row["created_at"] or "")[:10])
    except (TypeError, ValueError):
        sent_on = today
    days_since = max((today - sent_on).days, 0)
    next_due_on = sent_on + dt.timedelta(days=config.AR_CHASE_FOLLOWUP_DAYS)
    followup_due = days_since >= config.AR_CHASE_FOLLOWUP_DAYS
    if days_since == 0:
        label = "last chased today"
    elif days_since == 1:
        label = "last chased yesterday"
    else:
        label = f"last chased {days_since}d ago"
    if followup_due:
        detail = f"{label}; follow-up due."
        status = "due"
    else:
        detail = f"{label}; next follow-up {next_due_on.isoformat()}."
        status = "recent"
    return {
        "status": status,
        "followup_due": followup_due,
        "days_since": days_since,
        "last_sent_at": row["created_at"],
        "last_to": row["to_email"],
        "next_due_on": next_due_on.isoformat(),
        "action_meta": label,
        "detail": detail,
    }


def _ar_chase_message(c, rows: list[dict]) -> str:
    if not rows:
        return ""
    company_name = c["company"] or c["name"]
    first_name = ((c["name"] or "").strip().split() or ["there"])[0]
    lines = [
        f"Hi {first_name},",
        "",
        f"Quick follow-up on the open balance for {company_name}.",
        "",
        "Outstanding:",
    ]
    for row in rows:
        title = row["title"] or f"Invoice #{row['id']}"
        if row["client_name"] and row["client_name"] != c["name"]:
            title = f"{title} ({row['client_name']})"
        lines.extend(
            [
                f"- {title}, due {row['due_date']}: {_format_cents(row['owed_cents'])}",
                f"  {config.BASE_URL}/i/{row['slug']}",
            ]
        )
    lines.extend(
        [
            "",
            f"Total open: {_format_cents(sum(row['owed_cents'] for row in rows))}",
            "",
            "If this is already in process, thank you. Otherwise you can pay securely "
            "from the invoice link above, or reply here if AP needs anything else.",
            "",
            "Best,",
            "Kevin",
        ]
    )
    return "\n".join(lines)


def _ar_chase_context(client_id: int, invoice_id: int | None = None) -> dict:
    from .admin import studio

    c = studio.get_client(client_id)
    rows = _company_overdue_rows(studio._group_ids(client_id), studio._today())
    if invoice_id is not None:
        rows = [row for row in rows if row["id"] == invoice_id]
        if not rows:
            raise HTTPException(
                status_code=404, detail="overdue invoice not found for this company"
            )
    company_name = c["company"] or c["name"]
    return {
        "c": c,
        "rows": rows,
        "owed_cents": sum(row["owed_cents"] for row in rows),
        "email_to": _company_ar_contact(c, rows),
        "email_subject": f"{studio.AR_CHASE_SUBJECT_PREFIX}{company_name}",
        "email_message": _ar_chase_message(c, rows),
        "ar_history": _ar_chase_history(client_id),
        "statement_href": f"/admin/studio/companies/{client_id}/statement",
        "invoice_id": invoice_id,
        "base_url": config.BASE_URL,
    }


def _project_closeout(project_id: int, p) -> dict:
    """Read-only project closeout checklist.

    Pulls together the commercial spine without mutating it: shot list, deliverables,
    licence, invoice, open AR, gallery, and workspace. The panel points the operator to
    the owning surface for each gap; it never sends, charges, publishes, or closes.
    """

    def row(key: str, title: str, tone: str, label: str, href: str | None = None) -> dict:
        badges = {"ok": "Ready", "warn": "Needs attention", "gap": "Missing"}
        return {
            "key": key,
            "title": title,
            "tone": tone,
            "badge": badges[tone],
            "label": label,
            "href": href,
        }

    rows = []
    shots_n = db.one(
        "SELECT COUNT(*) AS n FROM shot_list WHERE project_id=? AND deleted_at IS NULL",
        (project_id,),
    )["n"]
    rows.append(
        row(
            "shots",
            "Shot list",
            "ok" if shots_n else "gap",
            f"{shots_n} shot{'s' if shots_n != 1 else ''} planned"
            if shots_n
            else "No shot list yet",
            "#shot-list",
        )
    )

    deliverables = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(spec_qty), 0) AS spec,
                  COALESCE(SUM(delivered_qty), 0) AS done
           FROM project_deliverables
           WHERE project_id=? AND deleted_at IS NULL""",
        (project_id,),
    )
    if not deliverables["n"]:
        rows.append(
            row("deliverables", "Deliverables", "gap", "No deliverable spec", "#deliverables")
        )
    elif not deliverables["spec"]:
        rows.append(
            row(
                "deliverables",
                "Deliverables",
                "warn",
                f"{deliverables['n']} line{'s' if deliverables['n'] != 1 else ''}, no count",
                "#deliverables",
            )
        )
    else:
        done = deliverables["done"]
        spec = deliverables["spec"]
        rows.append(
            row(
                "deliverables",
                "Deliverables",
                "ok" if done >= spec else "warn",
                f"{done}/{spec} delivered",
                "#deliverables",
            )
        )

    licenses = db.all_(
        """SELECT id, title, status FROM licenses
           WHERE deleted_at IS NULL
             AND (project_id=? OR invoice_id IN (SELECT id FROM invoices WHERE project_id=?))
           ORDER BY (status='active') DESC, id DESC""",
        (project_id, project_id),
    )
    active_license = next((lic for lic in licenses if lic["status"] == "active"), None)
    first_license = licenses[0] if licenses else None
    if active_license:
        rows.append(
            row(
                "license",
                "Usage licence",
                "ok",
                f"Active: {active_license['title']}",
                f"/admin/studio/licenses/{active_license['id']}",
            )
        )
    elif first_license:
        rows.append(
            row(
                "license",
                "Usage licence",
                "warn",
                f"{first_license['status']}: {first_license['title']}",
                f"/admin/studio/licenses/{first_license['id']}",
            )
        )
    else:
        rows.append(row("license", "Usage licence", "gap", "No project licence", "#invoices"))

    invoice_rows = db.all_("SELECT * FROM invoices WHERE project_id=? ORDER BY id", (project_id,))
    issued = [inv for inv in invoice_rows if inv["status"] != "draft"]
    drafts = [inv for inv in invoice_rows if inv["status"] == "draft"]
    if issued:
        latest = issued[-1]
        tone = "ok" if any(inv["status"] == "paid" for inv in issued) else "warn"
        rows.append(
            row(
                "invoice",
                "Invoice",
                tone,
                f"Latest issued: {latest['status']}",
                f"/admin/studio/invoices/{latest['id']}",
            )
        )
    elif drafts:
        rows.append(
            row(
                "invoice",
                "Invoice",
                "warn",
                "Draft invoice not sent",
                f"/admin/studio/invoices/{drafts[-1]['id']}",
            )
        )
    else:
        rows.append(row("invoice", "Invoice", "gap", "No invoice", "#invoices"))

    paid_by_invoice = {
        r["invoice_id"]: r["paid_cents"]
        for r in db.all_(
            """SELECT invoice_id, COALESCE(SUM(amount_cents), 0) AS paid_cents
               FROM payments
               WHERE invoice_id IN (SELECT id FROM invoices WHERE project_id=?)
               GROUP BY invoice_id""",
            (project_id,),
        )
    }
    owed_cents = sum(
        max((inv["total_cents"] or 0) - paid_by_invoice.get(inv["id"], 0), 0) for inv in issued
    )
    if issued:
        rows.append(
            row(
                "ar",
                "Open AR",
                "ok" if owed_cents == 0 else "warn",
                "No open balance" if owed_cents == 0 else f"{owed_cents / 100:,.2f} outstanding",
                "#invoices",
            )
        )
    else:
        rows.append(row("ar", "Open AR", "gap", "No issued invoice to settle", "#invoices"))

    gallery = None
    if p["gallery_id"]:
        gallery = db.one(
            "SELECT id, title, published FROM galleries WHERE id=?", (p["gallery_id"],)
        )
    if gallery and gallery["published"]:
        rows.append(
            row(
                "gallery",
                "Gallery",
                "ok",
                f"Published: {gallery['title']}",
                f"/admin/galleries/{gallery['id']}",
            )
        )
    elif gallery:
        rows.append(
            row(
                "gallery",
                "Gallery",
                "warn",
                f"Linked draft: {gallery['title']}",
                f"/admin/galleries/{gallery['id']}",
            )
        )
    else:
        rows.append(row("gallery", "Gallery", "gap", "No linked gallery", "#project-details"))

    if p["workspace_published"] and p["workspace_slug"]:
        rows.append(
            row(
                "workspace",
                "Workspace",
                "ok",
                "Client workspace is live",
                f"{config.BASE_URL}/w/{p['workspace_slug']}",
            )
        )
    else:
        rows.append(row("workspace", "Workspace", "warn", "Client workspace not published", None))

    ok = sum(1 for item in rows if item["tone"] == "ok")
    warn = sum(1 for item in rows if item["tone"] == "warn")
    gap = sum(1 for item in rows if item["tone"] == "gap")
    return {
        "rows": rows,
        "ok": ok,
        "warn": warn,
        "gap": gap,
        "total": len(rows),
        "ready": warn == 0 and gap == 0,
    }


def _company_next_actions(
    client_id: int,
    group_ids: list[int],
    cadence: dict,
    overdue_rows,
    active_projects,
    retainers,
    ar_history: dict | None = None,
    billing_readiness: dict | None = None,
) -> list[dict]:
    """Read-only ranked action hints for the company command view.

    This derives from existing project, invoice, licence, cadence, and retainer state. It creates
    no task rows and performs no side effects; every item links to the surface that owns the fix.
    """
    actions: list[dict] = []
    seen_hrefs: set[str] = set()

    def add(
        rank: int,
        tone: str,
        title: str,
        label: str,
        href: str,
        meta: str | None = None,
    ) -> bool:
        if href in seen_hrefs:
            return False
        seen_hrefs.add(href)
        actions.append(
            {
                "rank": rank,
                "tone": tone,
                "title": title,
                "label": label,
                "href": href,
                "meta": meta,
            }
        )
        return True

    if billing_readiness and billing_readiness.get("needs_ap_action"):
        add(
            5,
            "warn",
            "Add billing email",
            billing_readiness["action_label"],
            f"/admin/studio/companies/{client_id}#billing-readiness",
            "billing readiness",
        )

    if overdue_rows:
        ar_history = ar_history or _ar_chase_history(client_id)
        owed_cents = sum(r["owed_cents"] or 0 for r in overdue_rows)
        href = f"/admin/studio/companies/{client_id}/ar-chase"
        if len(overdue_rows) == 1:
            href += f"?invoice_id={overdue_rows[0]['id']}"
            label = f"{overdue_rows[0]['title'] or 'Invoice'} · ${owed_cents / 100:,.0f} owed"
        else:
            label = f"{len(overdue_rows)} past due · ${owed_cents / 100:,.0f} owed"
        title = "Chase past-due invoice"
        rank = 10
        if ar_history["status"] == "recent":
            title = "Past-due invoice chased recently"
            rank = 25
        add(
            rank,
            "warn",
            title,
            label,
            href,
            ar_history["action_meta"],
        )

    ph = ",".join("?" * len(group_ids))
    draft = db.one(
        f"""SELECT i.id, i.title, i.total_cents, c.name AS client_name
            FROM invoices i
            JOIN projects p ON p.id=i.project_id
            JOIN clients c ON c.id=p.client_id
            WHERE p.client_id IN ({ph}) AND i.status='draft'
            ORDER BY i.created_at DESC, i.id DESC
            LIMIT 1""",
        group_ids,
    )
    if draft:
        add(
            20,
            "warn",
            "Send draft invoice",
            f"{draft['client_name']} · {draft['title'] or 'Draft invoice'}",
            f"/admin/studio/invoices/{draft['id']}",
            "money",
        )

    for r in retainers:
        if not r["behind"]:
            continue
        behind = ", ".join(r["behind"][:2])
        if len(r["behind"]) > 2:
            behind = f"{behind} +{len(r['behind']) - 2} more"
        add(
            30,
            "warn",
            "Catch up retainer quota",
            f"{r['client_name']} · {behind}",
            f"/admin/studio/recurring/{r['id']}",
            r["title"],
        )

    project_rank = {
        "ar": 35,
        "invoice": 40,
        "license": 45,
        "deliverables": 50,
        "gallery": 60,
        "workspace": 70,
        "shots": 80,
    }
    project_titles = {
        "ar": "Settle project balance",
        "invoice": "Issue project invoice",
        "license": "Record usage licence",
        "deliverables": "Update deliverables",
        "gallery": "Publish delivery gallery",
        "workspace": "Publish client workspace",
        "shots": "Build shot list",
    }
    for p in active_projects:
        closeout = _project_closeout(p["id"], p)
        gaps = [
            item
            for item in closeout["rows"]
            if item["tone"] != "ok" and not (item["key"] == "ar" and item["tone"] == "gap")
        ]
        gaps.sort(key=lambda item: project_rank.get(item["key"], 99))
        for item in gaps:
            href = item["href"]
            if href and href.startswith("#"):
                href = f"/admin/studio/projects/{p['id']}{href}"
            elif not href:
                href = f"/admin/studio/projects/{p['id']}"
            if add(
                project_rank.get(item["key"], 99),
                item["tone"],
                project_titles.get(item["key"], item["title"]),
                f"{p['title']} · {item['label']}",
                href,
                p["client_name"],
            ):
                break

    if cadence["status"] in {"due", "due_soon"}:
        add(
            90 if cadence["status"] == "due" else 95,
            "warn",
            "Schedule repeat shoot",
            cadence["label"],
            f"/admin/studio/clients/{client_id}",
            "derived cadence",
        )

    actions.sort(key=lambda item: (item["rank"], item["title"], item["label"]))
    return actions[:6]


def _ctx_commercial_actions(today: dt.date) -> list[dict]:
    """Studio-wide commercial action queue.

    Rolls up the top derived company action per root client into the Activity page. This reuses the
    company-view ranking and remains read-only: no tasks, sends, charges, publishes, or closes.
    """
    from .admin import recurring, studio

    period = recurring._period()
    actions: list[dict] = []
    roots = db.all_(
        "SELECT id, name, company FROM clients WHERE parent_id IS NULL ORDER BY company, name"
    )
    for c in roots:
        group_ids = studio._group_ids(c["id"])
        ph = ",".join("?" * len(group_ids))
        overdue_rows = _company_overdue_rows(group_ids, today)
        billing_readiness = studio._company_billing_readiness(c["id"], group_ids, overdue_rows)
        active_projects = db.all_(
            f"""SELECT p.*, c.name AS client_name
                FROM projects p JOIN clients c ON c.id=p.client_id
                WHERE p.client_id IN ({ph}) AND p.status NOT IN ('project_closed','archived')
                ORDER BY p.shoot_date IS NULL, p.shoot_date, p.created_at DESC""",
            group_ids,
        )
        plan_rows = db.all_(
            f"""SELECT rp.*, c.name AS client_name
                FROM recurring_plans rp
                JOIN projects p ON p.id=rp.project_id
                JOIN clients c ON c.id=p.client_id
                WHERE p.client_id IN ({ph}) AND rp.active=1 AND rp.deleted_at IS NULL
                ORDER BY c.name, rp.title""",
            group_ids,
        )
        retainers = []
        for rp in plan_rows:
            ov = recurring.compute_overage(rp, period)
            retainers.append(
                {
                    "id": rp["id"],
                    "title": rp["title"],
                    "client_name": rp["client_name"],
                    "behind": [
                        line["label"] for line in ov["lines"] if line["done"] < line["target"]
                    ],
                }
            )
        cadence = common.shoot_cadence(c["id"], today_date=today, include_children=True)
        ranked = _company_next_actions(
            c["id"],
            group_ids,
            cadence,
            overdue_rows,
            active_projects,
            retainers,
            _ar_chase_history(c["id"], today),
            billing_readiness,
        )
        if not ranked:
            continue
        first = dict(ranked[0])
        first.update(
            {
                "company_id": c["id"],
                "company_name": c["company"] or c["name"],
                "company_href": f"/admin/studio/companies/{c['id']}",
            }
        )
        actions.append(first)
    actions.sort(key=lambda item: (item["rank"], item["company_name"], item["title"]))
    return actions[:8]
