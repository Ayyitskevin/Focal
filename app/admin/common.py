"""Shared admin helpers (start of module splits for large admin files)."""

import datetime as dt
import statistics
from pathlib import Path

from fastapi.responses import RedirectResponse

from .. import clients as client_tree
from .. import db

CADENCE_SOON_DAYS = 14


def initials(name: str, empty: str = "?") -> str:
    """Avatar initials for a display name; ``empty`` is the fallback when there is
    no name (callers differ: '?' for a client card, '#' for an inbox lead)."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return empty
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def flash_redirect(base_path: str, msg: str = "", err: str = "") -> RedirectResponse:
    """303 back to ``base_path`` carrying optional ?msg= / &err= flash params.
    Params are intentionally NOT URL-encoded, preserving the pre-existing admin
    flash convention shared by these console pages."""
    q = []
    if msg:
        q.append(f"msg={msg}")
    if err:
        q.append(f"err={err}")
    suffix = ("?" + "&".join(q)) if q else ""
    return RedirectResponse(f"{base_path}{suffix}", status_code=303)


_STATUS_STYLE = {
    "Delivered": ("#2f7d57", "#e1f2e9"),
    "Proofing": ("#9a7a2c", "#f7ecd2"),
    "Draft": ("#5C6A5E", "#ecefe6"),
    "Expiring": ("#7C2F38", "#f3e3e5"),
}


def parse_form_cents(form, key: str) -> int:
    """Form dollar field → integer cents (empty/missing → 0). Raises ValueError on
    non-numeric input so each route can 400 with its own field-specific message."""
    return round(float(form.get(key) or "0") * 100)


def open_invoice_balance():
    """Count + total cents still owed across all currently-open invoices — the AR
    figure behind the studio/reports/activity/financials 'outstanding' widgets.
    A deposit_paid invoice owes (total - deposit); sent/viewed owe the full total."""
    return db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(CASE
             WHEN status='deposit_paid' THEN total_cents - deposit_cents
             ELSE total_cents END), 0) AS cents
           FROM invoices WHERE status IN ('sent','viewed','deposit_paid')"""
    )


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def fmt_size(n: int) -> str:
    if n <= 0:
        return "—"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


def short_date(stored: str) -> str:
    """'2026-06-18 12:00:00' → 'Jun 18'. Tolerates a bare date or junk."""
    if not stored:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(stored[:19], fmt).strftime("%b %-d")
        except ValueError:
            continue
    return stored


def today() -> dt.date:
    """Studio wall-clock today (local). Monkeypatchable."""
    return dt.date.today()


def _dateish(value: str | None) -> dt.date | None:
    """Parse a stored YYYY-MM-DD-ish value, tolerating timestamp suffixes."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def shoot_cadence(
    client_id: int,
    *,
    today_date: dt.date | None = None,
    include_children: bool = True,
) -> dict:
    """Derived repeat-client cadence for a client or company group.

    Uses prior shoot dates only: median gap between historical shoots projects
    the next due date. A future shoot suppresses the due nudge because the next
    action is already scheduled. Read-only; no persistence or automation.
    """
    today_date = today_date or today()
    if include_children:
        group_ids = [client_id, *client_tree.descendant_ids(client_id)]
    else:
        group_ids = [client_id]
    ph = ",".join("?" * len(group_ids))
    rows = db.all_(
        f"""SELECT p.shoot_date
            FROM projects p
            WHERE p.client_id IN ({ph}) AND p.shoot_date IS NOT NULL
                  AND p.shoot_date <= ?
            ORDER BY p.shoot_date""",
        (*group_ids, today_date.isoformat()),
    )
    dates = sorted({d for d in (_dateish(r["shoot_date"]) for r in rows) if d is not None})
    last_shoot = dates[-1] if dates else None
    next_row = db.one(
        f"""SELECT MIN(shoot_date) AS d
            FROM projects
            WHERE client_id IN ({ph}) AND shoot_date IS NOT NULL
                  AND shoot_date >= ?""",
        (*group_ids, today_date.isoformat()),
    )
    next_shoot = _dateish(next_row["d"] if next_row else None)
    cadence = {
        "status": "none",
        "label": "no shoots logged",
        "tone": "muted",
        "last_shoot": last_shoot.isoformat() if last_shoot else None,
        "next_shoot": next_shoot.isoformat() if next_shoot else None,
        "typical_days": None,
        "due_on": None,
        "overdue_days": 0,
        "n_shoots": len(dates),
    }
    if next_shoot:
        cadence.update(
            {
                "status": "scheduled",
                "label": f"scheduled {next_shoot.isoformat()}",
                "tone": "ok",
            }
        )
        return cadence
    if len(dates) < 2:
        if last_shoot:
            cadence.update(
                {
                    "status": "one_shoot",
                    "label": f"last shoot {last_shoot.isoformat()}",
                    "tone": "muted",
                }
            )
        return cadence
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    if not gaps:
        return cadence
    typical_days = max(1, round(statistics.median(gaps)))
    due_on = last_shoot + dt.timedelta(days=typical_days)
    overdue_days = (today_date - due_on).days
    cadence.update(
        {
            "status": "steady",
            "label": f"due {due_on.isoformat()}",
            "tone": "muted",
            "typical_days": typical_days,
            "due_on": due_on.isoformat(),
            "overdue_days": max(overdue_days, 0),
        }
    )
    if overdue_days >= 0:
        if overdue_days:
            label = f"due for a shoot ({overdue_days}d overdue)"
        else:
            label = "due for a shoot today"
        cadence.update({"status": "due", "label": label, "tone": "warn"})
    elif due_on <= today_date + dt.timedelta(days=CADENCE_SOON_DAYS):
        cadence.update(
            {
                "status": "due_soon",
                "label": f"due soon {due_on.isoformat()}",
                "tone": "warn",
            }
        )
    return cadence


def client_cadence_hints(client_rows) -> dict[int, tuple[str, str]]:
    """Compact cadence labels for the client list, keyed by client id."""
    today_date = today()
    hints = {}
    for c in client_rows:
        cadence = shoot_cadence(c["id"], today_date=today_date, include_children=True)
        if cadence["status"] in {"due", "due_soon"}:
            hints[c["id"]] = ("warn", cadence["label"])
        elif cadence["status"] == "scheduled":
            hints[c["id"]] = ("ok", cadence["label"])
        elif cadence["last_shoot"]:
            hints[c["id"]] = ("muted", f"last {cadence['last_shoot']}")
        else:
            hints[c["id"]] = ("muted", "no cadence")
    return hints


def gallery_card(g, today_iso: str, soon_iso: str) -> dict:
    exp = g["expires_at"]
    expired = bool(exp and exp < today_iso)
    expiring_soon = bool(exp and not expired and exp <= soon_iso)
    if not g["published"]:
        status = "Draft"
    elif expired or expiring_soon:
        status = "Expiring"
    elif g["n_proof"] and g["n_proof_pending"]:
        status = "Proofing"
    else:
        status = "Delivered"
    color, bg = _STATUS_STYLE[status]
    if status == "Expiring":
        if expired:
            date_label = "expired"
        else:
            days = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(today_iso)).days
            date_label = f"{days} day{'s' if days != 1 else ''}"
        date_color = "#7C2F38"
    else:
        date_label = short_date(g["created_at"])
        date_color = "#8A9183"
    n = g["n_assets"]
    photos = f"{n} photo{'s' if n != 1 else ''}" if n else "No photos yet"
    return {
        "id": g["id"],
        "title": g["title"],
        "client": g["client_name"] or "—",
        "cover_asset_id": g["cover_asset_id"],
        "pin": g["pin"],
        "status": status,
        "status_lc": status.lower(),
        "status_color": color,
        "status_bg": bg,
        "photos": photos,
        "favs": g["n_fav"],
        "date": date_label,
        "date_color": date_color,
    }


def _clients_with_hints() -> tuple[list, dict]:
    """Clients with per-client project counts + portal engagement, plus a friendly
    "visited Xh ago" / "never visited" hint keyed by client id so the template
    stays declarative. Portal engagement (Phase 2) is otherwise invisible from the
    studio. last_visit is stored UTC; compared against a UTC 'now' here."""
    clients = db.all_("""SELECT c.*,
                         (SELECT COUNT(*) FROM projects p WHERE p.client_id=c.id) AS n_projects,
                         (SELECT po.published FROM portals po WHERE po.client_id=c.id) AS portal_published,
                         (SELECT po.visits FROM portals po WHERE po.client_id=c.id) AS portal_visits,
                         (SELECT po.last_visit FROM portals po WHERE po.client_id=c.id) AS portal_last_visit
                         FROM clients c ORDER BY c.name""")
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    hints = {}
    for c in clients:
        if c["portal_published"] is None:
            hints[c["id"]] = ("muted", "no portal")
        elif not c["portal_last_visit"]:
            hints[c["id"]] = ("muted", "never visited")
        else:
            try:
                last = dt.datetime.fromisoformat(c["portal_last_visit"])
            except ValueError:
                hints[c["id"]] = ("muted", "visited (date unknown)")
                continue
            delta = now - last
            if delta.total_seconds() < 60:
                hint = "just now"
            elif delta.total_seconds() < 3600:
                hint = f"{int(delta.total_seconds() // 60)}m ago"
            elif delta.total_seconds() < 86400:
                hint = f"{int(delta.total_seconds() // 3600)}h ago"
            elif delta.days < 30:
                hint = f"{delta.days}d ago"
            else:
                hint = last.date().isoformat()
            hints[c["id"]] = ("ok", f"👁 {hint}")
    return clients, hints
