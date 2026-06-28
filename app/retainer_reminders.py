"""Retainer renewal nudge — an internal heads-up to Kevin (Telegram, never the client) when a
recurring plan's renewal date is approaching and the nudge hasn't fired yet. Fired off the same
recurring sweep as the other reminders (app/scheduler.py).

This fills the retainer-lifecycle gap: a plan with a fixed term (renews_on set) would otherwise
lapse silently — the scheduler keeps generating monthly drafts with no signal that the term is
ending. So this proactively flags an upcoming renewal so the operator can renew, renegotiate, or
let it end (optionally with pause_at_term to stop generation).

One-shot per plan via the nudged_renewal flag, mirroring contract_reminders' nudged_unsigned:
update_plan resets the flag ONLY when renews_on actually changes, and the Renew action clears it,
so once nudged a plan never re-nudges until its date moves. The whole sweep no-ops unless Telegram
is configured, and it never sets the flag when disabled — so enabling alerts later still catches
plans already inside the window. Evergreen plans (renews_on NULL) are never nudged.
"""

import logging

from . import alerts, config, db

log = logging.getLogger("mise.retainer_reminders")


def _due() -> list["db.sqlite3.Row"]:
    """Active, non-deleted plans whose renewal date is within the nudge window and whose nudge
    hasn't fired yet. Evergreen plans (renews_on NULL) are excluded by the IS NOT NULL guard."""
    return db.all_(
        f"""SELECT rp.id, rp.title, rp.renews_on,
                   c.name AS client_name, c.company,
                   CAST(julianday(rp.renews_on) - julianday('now') AS INTEGER) AS days_left
            FROM recurring_plans rp
            JOIN projects p ON p.id = rp.project_id
            JOIN clients c ON c.id = p.client_id
            WHERE rp.active=1
              AND rp.deleted_at IS NULL
              AND rp.nudged_renewal = 0
              AND rp.renews_on IS NOT NULL
              AND rp.renews_on <= date('now', '+{int(config.RETAINER_RENEWAL_NUDGE_DAYS)} days')
            ORDER BY rp.renews_on ASC"""
    )


def sweep() -> None:
    """Nudge once per plan entering its renewal window. Best-effort per row — a send failure
    leaves the flag unset so the next sweep retries; never blocks the loop."""
    if not alerts.is_enabled():
        return
    for rp in _due():
        who = rp["company"] or rp["client_name"]
        try:
            alerts.notify(
                f"Retainer renewal coming up — {rp['title']} · {who} "
                f"(renews {rp['renews_on']}, {rp['days_left']}d). {config.BASE_URL}"
                f"/admin/studio/recurring/{rp['id']}"
            )
            db.run("UPDATE recurring_plans SET nudged_renewal=1 WHERE id=?", (rp["id"],))
            log.info("retainer %s renewal nudge sent (%sd left)", rp["id"], rp["days_left"])
        except Exception as e:
            log.error("retainer %s renewal nudge failed: %s", rp["id"], e)
