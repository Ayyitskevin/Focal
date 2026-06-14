"""Push invoice money status to the Notion Session page.

Keeps Odysseus automations (balance_chaser, digest REVENUE) accurate with zero
Odysseus changes. Property names match Odysseus P_SESSION — its API contract,
do not rename.
"""

import json
import logging
import urllib.request

from . import config, db

log = logging.getLogger("mise.notion")


def _patch_page(page_id: str, props: dict) -> None:
    req = urllib.request.Request(
        f"https://api.notion.com/v1/pages/{page_id}", method="PATCH",
        data=json.dumps({"properties": props}).encode(),
        headers={"Authorization": f"Bearer {config.NOTION_TOKEN}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def sync_invoice(invoice_id: int) -> None:
    d = db.one("""SELECT i.*, p.notion_page_id FROM invoices i
                  JOIN projects p ON p.id=i.project_id WHERE i.id=?""", (invoice_id,))
    if not d:
        raise ValueError(f"invoice {invoice_id} not found")
    if not config.NOTION_TOKEN or not d["notion_page_id"]:
        log.info("notion sync skipped for invoice %s (token=%s page=%s)",
                 invoice_id, bool(config.NOTION_TOKEN), bool(d["notion_page_id"]))
        return
    _patch_page(d["notion_page_id"], {
        "Invoice Amount": {"number": d["total_cents"] / 100},
        "Deposit Amount": {"number": d["deposit_cents"] / 100},
        "Invoice Paid": {"checkbox": d["status"] == "paid"},
        "Deposit Paid": {"checkbox": bool(d["deposit_cents"])
                         and d["status"] in ("deposit_paid", "paid")},
    })
    log.info("notion session synced from invoice %s (%s)", invoice_id, d["status"])


def sync_gallery(gallery_id: int) -> None:
    d = db.one("""SELECT g.slug, g.published, p.notion_page_id FROM galleries g
                  JOIN projects p ON p.id=g.project_id WHERE g.id=?""", (gallery_id,))
    if not d:
        raise ValueError(f"gallery {gallery_id} not found or not linked to a project")
    if not config.NOTION_TOKEN or not d["notion_page_id"] or not d["published"]:
        log.info("notion gallery sync skipped for %s (token=%s page=%s published=%s)",
                 gallery_id, bool(config.NOTION_TOKEN),
                 bool(d["notion_page_id"]), bool(d["published"]))
        return
    _patch_page(d["notion_page_id"],
                {"Gallery URL": {"url": f"{config.BASE_URL}/g/{d['slug']}"}})
    log.info("notion session gallery URL set from gallery %s", gallery_id)
