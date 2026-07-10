"""Hosted MicroSaaS control plane for Mise.

Product data remains in the existing Mise schema. This module adds a small
control database for tenants and switches requests/jobs into a tenant-specific
SQLite database and file-storage root.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import secrets
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import config, db, mailer, passwords, security, urls
from .render import templates

log = logging.getLogger("mise.saas")
router = APIRouter()

_TENANT_CTX: ContextVar[dict | None] = ContextVar("mise_tenant", default=None)
_MIGRATED_TENANT_DBS: set[str] = set()
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_ATTRIBUTION_RE = re.compile(r"[^a-zA-Z0-9_.:/@?=& -]")
_DEFAULT_BRAND_ACCENT = "#2f5c45"
_RESERVED_SLUGS = {
    "admin",
    "api",
    "app",
    "assets",
    "billing",
    "book",
    "cdn",
    "dashboard",
    "demo",
    "docs",
    "forms",
    "g",
    "i",
    "login",
    "media",
    "portal",
    "pricing",
    "static",
    "status",
    "support",
    "webhooks",
    "www",
}


def _stripe():
    import stripe

    # Pin the API version to the tested contract (ADR: config.STRIPE_API_VERSION),
    # so an SDK bump can't silently shift request/response shapes on the money path.
    if config.STRIPE_API_VERSION:
        stripe.api_version = config.STRIPE_API_VERSION
    return stripe


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Two writers share these columns: _iso() emits offset-aware "…Z", but SQLite's
    # datetime('now') DEFAULTs (created_at, updated_at) emit NAIVE UTC strings.
    # Comparing a naive parse against the aware _now() raises TypeError, so treat
    # naive as the UTC it actually is (Batch C1 — the win-back sweep reads
    # updated_at and would have crashed on every canceled tenant without this).
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def _root_domain() -> str:
    raw = config.SAAS_ROOT_DOMAIN.strip()
    if raw:
        if "://" in raw:
            raw = urlsplit(raw).netloc
        return raw.lower().strip("/")
    parsed = urlsplit(config.BASE_URL)
    return (parsed.netloc or parsed.hostname or "").lower()


def _host_only(host: str) -> str:
    host = (host or "").split(",", 1)[0].strip().lower()
    if host.startswith("["):
        return host
    return host.split(":", 1)[0].rstrip(".")


def _root_host_only() -> str:
    return _host_only(_root_domain())


def platform_url(path: str = "/") -> str:
    path = path if path.startswith("/") else f"/{path}"
    parsed = urlsplit(config.BASE_URL)
    scheme = parsed.scheme or "https"
    host = _root_domain() or parsed.netloc or f"localhost:{config.PORT}"
    return f"{scheme}://{host}{path}"


def tenant_url(slug: str, path: str = "/") -> str:
    path = path if path.startswith("/") else f"/{path}"
    parsed = urlsplit(config.BASE_URL)
    scheme = parsed.scheme or "https"
    root = _root_domain() or parsed.netloc or f"localhost:{config.PORT}"
    return f"{scheme}://{slug}.{root}{path}"


def tenant_slug_from_host(host: str) -> str | None:
    host_only = _host_only(host)
    root = _root_host_only()
    marketing = _host_only(config.SAAS_MARKETING_HOST)
    if not host_only or not root or host_only in {root, marketing, f"www.{root}"}:
        return None
    suffix = f".{root}"
    if not host_only.endswith(suffix):
        tenant = tenant_by_custom_domain(host_only)
        return tenant["slug"] if tenant else None
    slug = host_only[: -len(suffix)]
    return slug if slug and "." not in slug else None


def tenant_data_path(slug: str) -> Path:
    return config.SAAS_TENANT_DATA_DIR / slug


def tenant_db_path(slug: str) -> Path:
    return tenant_data_path(slug) / "mise.db"


def control_connect() -> sqlite3.Connection:
    config.SAAS_CONTROL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.SAAS_CONTROL_DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def migrate_control() -> None:
    config.ensure_dirs()
    with control_connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                studio_name TEXT NOT NULL,
                owner_email TEXT NOT NULL,
                admin_password_hash TEXT NOT NULL,
                plan_status TEXT NOT NULL DEFAULT 'trialing'
                    CHECK (plan_status IN (
                        'trialing','active','past_due','canceled','unpaid','paused',
                        'incomplete','incomplete_expired'
                    )),
                trial_started_at TEXT NOT NULL,
                trial_ends_at TEXT NOT NULL,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                custom_domain TEXT UNIQUE,
                custom_domain_verified_at TEXT,
                brand_accent TEXT NOT NULL DEFAULT '#2f5c45',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
            CREATE INDEX IF NOT EXISTS idx_tenants_subscription
                ON tenants(stripe_subscription_id);
            CREATE TABLE IF NOT EXISTS saas_events (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tenant_feedback (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                page TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS waitlist (
                id INTEGER PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                source TEXT,
                campaign TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS control_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        _ensure_column(con, "tenants", "custom_domain", "custom_domain TEXT")
        _ensure_column(
            con,
            "tenants",
            "custom_domain_verified_at",
            "custom_domain_verified_at TEXT",
        )
        _ensure_column(
            con,
            "tenants",
            "brand_accent",
            "brand_accent TEXT NOT NULL DEFAULT '#2f5c45'",
        )
        _ensure_column(con, "tenants", "signup_source", "signup_source TEXT")
        _ensure_column(con, "tenants", "signup_campaign", "signup_campaign TEXT")
        _ensure_column(con, "tenants", "signup_referrer", "signup_referrer TEXT")
        # A tenant's OWN Stripe credentials for charging its client invoices.
        # NULL/empty = payments off for that studio (fail-closed): the platform
        # operator's Stripe key is never used to charge a studio's client. Populated
        # by the tenant's own Stripe connection (Connect onboarding follow-up).
        _ensure_column(con, "tenants", "client_stripe_secret_key", "client_stripe_secret_key TEXT")
        _ensure_column(
            con, "tenants", "client_stripe_webhook_secret", "client_stripe_webhook_secret TEXT"
        )
        # Rotation grace (ADR 0054): the webhook secret in force before the last
        # connect/disconnect, still accepted for verification so an in-flight checkout
        # (payable ~24h, retried for days) can never lose its payment record.
        _ensure_column(
            con,
            "tenants",
            "client_stripe_webhook_secret_prev",
            "client_stripe_webhook_secret_prev TEXT",
        )
        # One-shot trial-ending reminder stamp (ADR 0060): set when the platform has
        # emailed the owner that a card-less trial is about to end.
        _ensure_column(con, "tenants", "trial_reminder_sent_at", "trial_reminder_sent_at TEXT")
        # Tenant pulse (Batch A2): stamped on every successful tenant admin login —
        # the operator's only usage signal (updated_at tracks billing writes and the
        # launch score tracks setup completeness; neither says "gone quiet").
        _ensure_column(con, "tenants", "last_login_at", "last_login_at TEXT")
        # Operator notes (Batch A4): free-text per studio, operator-only — feedback
        # that arrives by email/DM finally has a home against the tenant it came from.
        _ensure_column(con, "tenants", "notes", "notes TEXT")
        # One-shot win-back stamp (Batch C1): set when the platform has emailed a
        # lapsed-trial or canceled owner their single come-back note.
        _ensure_column(con, "tenants", "winback_sent_at", "winback_sent_at TEXT")
        # Dunning stamps (Batch C2): the decline notice + the grace-ending warning.
        # Cleared when billing recovers to active so a FUTURE decline notifies again.
        _ensure_column(con, "tenants", "dunning_notice_sent_at", "dunning_notice_sent_at TEXT")
        _ensure_column(con, "tenants", "dunning_final_sent_at", "dunning_final_sent_at TEXT")
        # Offboarding tombstone (ADR 0051): set when the owner deletes the studio; the
        # slug is renamed so the address frees up, and the row keeps billing linkage.
        _ensure_column(con, "tenants", "deleted_at", "deleted_at TEXT")
        # Set when the best-effort Stripe cancel on delete FAILED (transient 5xx): the
        # subscription may still be charging, so the operator console surfaces it for a
        # manual cancel until they resolve it. Cleared by the resolve action.
        _ensure_column(con, "tenants", "cancel_failed_at", "cancel_failed_at TEXT")
        # Feedback triage (Batch D2): 'new' is the operator's queue, 'done' is the
        # archive — triaged notes leave the console but are never deleted (C4 exit
        # reasons keep their record value). Existing rows backfill to 'new'.
        _ensure_column(con, "tenant_feedback", "status", "status TEXT NOT NULL DEFAULT 'new'")
        con.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_custom_domain
               ON tenants(custom_domain) WHERE custom_domain IS NOT NULL"""
        )


def _ensure_column(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _meta_get(key: str) -> str | None:
    """Platform-level key/value stamps that aren't per-tenant (Batch D1).

    The tenant sweeps stamp their one-shots on the tenant row; platform-wide
    one-shots (the weekly digest's week key) need a home that isn't a column
    on somebody's tenant.
    """
    with control_connect() as con:
        row = con.execute("SELECT value FROM control_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(key: str, value: str) -> None:
    with control_connect() as con:
        con.execute(
            "INSERT INTO control_meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def tenant_by_slug(slug: str) -> dict | None:
    with control_connect() as con:
        row = con.execute("SELECT * FROM tenants WHERE slug=?", (slug,)).fetchone()
    return _row_to_dict(row)


def tenant_by_id(tenant_id: int) -> dict | None:
    with control_connect() as con:
        row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    return _row_to_dict(row)


def tenant_by_subscription(subscription_id: str) -> dict | None:
    with control_connect() as con:
        row = con.execute(
            "SELECT * FROM tenants WHERE stripe_subscription_id=?", (subscription_id,)
        ).fetchone()
    return _row_to_dict(row)


def tenant_by_custom_domain(host: str) -> dict | None:
    host = _host_only(host)
    if not host:
        return None
    try:
        with control_connect() as con:
            row = con.execute("SELECT * FROM tenants WHERE custom_domain=?", (host,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return _row_to_dict(row)


def list_tenants(*, billable_only: bool = False) -> list[dict]:
    where = ""
    params: tuple = ()
    if billable_only:
        where = "WHERE plan_status IN ('trialing','active','past_due')"
    with control_connect() as con:
        rows = con.execute(f"SELECT * FROM tenants {where} ORDER BY id", params).fetchall()
    return [dict(r) for r in rows]


def tenant_launch_status(tenant: dict) -> dict:
    if not tenant_db_path(tenant["slug"]).exists():
        return {
            "score": 0,
            "complete": False,
            "headline": "Tenant database missing",
            "detail": "Create or repair the tenant database before judging launch readiness.",
            "tone": "block",
        }
    try:
        with tenant_runtime(tenant):
            from . import onboarding as onboarding_state

            setup = onboarding_state.setup_status()
            launch = onboarding_state.launch_plan(setup)
    except sqlite3.Error as exc:
        log.warning("tenant %s launch status unavailable: %s", tenant["slug"], exc)
        return {
            "score": 0,
            "complete": False,
            "headline": "Launch status unavailable",
            "detail": "Open the tenant database before relying on this studio's launch score.",
            "tone": "block",
        }
    score = int(launch["score"])
    complete = bool(setup["complete"])
    if complete:
        tone = "ok"
    elif score >= 50:
        tone = "warn"
    else:
        tone = "needs_work"
    return {
        "score": score,
        "complete": complete,
        "headline": launch["headline"],
        "detail": launch["detail"],
        "tone": tone,
    }


def operator_growth_metrics(counts: dict, source_counts: dict[str, int]) -> dict:
    total = counts["total"]
    source_rows = [
        {"source": source, "count": count}
        for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    top_source = source_rows[0]["source"] if source_rows else "none"
    return {
        "activation_rate": round(counts["launch_ready"] * 100 / total) if total else 0,
        "active_rate": round(counts["active"] * 100 / total) if total else 0,
        "average_launch_score": counts["average_launch_score"],
        "top_source": top_source,
        "source_rows": source_rows,
    }


def operator_tenant_overview() -> dict:
    rows = []
    source_counts: dict[str, int] = {}
    counts = {
        "total": 0,
        "trialing": 0,
        "active": 0,
        "attention": 0,
        "custom_domains_pending": 0,
        "custom_domains_verified": 0,
        "active_mrr_cents": 0,
        "trial_pipeline_cents": 0,
        "support_queue": 0,
        "launch_ready": 0,
        "trials_at_risk": 0,
        "launch_score_total": 0,
        "average_launch_score": 0,
        "card_on_file": 0,
        "no_card_trials": 0,
        "departed": 0,
    }
    attention_statuses = {
        "past_due",
        "unpaid",
        "canceled",
        "paused",
        "incomplete",
        "incomplete_expired",
    }
    for tenant in list_tenants():
        if tenant.get("deleted_at"):
            # Tombstones (ADR 0051) keep billing linkage but are not studios:
            # counting them inflated every headline KPI forever — a deleted
            # studio reads as 'canceled', so the attention/support queue could
            # only ever grow, and growth rates divided by ghosts.
            counts["departed"] += 1
            continue
        billing = tenant_billing_context(tenant)
        launch = tenant_launch_status(tenant)
        domain_state = "none"
        if tenant.get("custom_domain"):
            domain_state = "verified" if tenant.get("custom_domain_verified_at") else "pending"
        counts["total"] += 1
        counts["launch_score_total"] += launch["score"]
        source = tenant.get("signup_source") or "direct"
        source_counts[source] = source_counts.get(source, 0) + 1
        if launch["complete"]:
            counts["launch_ready"] += 1
        has_card = bool(tenant.get("stripe_customer_id"))
        if has_card:
            counts["card_on_file"] += 1
        # Silence = days since the owner last logged in (falling back to signup for
        # a tenant who provisioned and never came back — that IS silence). The only
        # usage pulse the operator has; launch score measures setup, not presence.
        silent_days = _days_since(tenant.get("last_login_at") or tenant.get("created_at"))
        if tenant["plan_status"] == "trialing":
            counts["trialing"] += 1
            counts["trial_pipeline_cents"] += config.SAAS_PRICE_CENTS
            if not has_card:
                # Abandoned-checkout trials: they look healthy until day 14, then
                # hit the paywall — the single biggest conversion leak (ADR 0056/0060).
                counts["no_card_trials"] += 1
            silent = silent_days is not None and silent_days >= SILENT_TRIAL_DAYS
            if silent or (
                not launch["complete"]
                and (
                    billing["tone"] == "block"
                    or billing["trial_days_left"] is None
                    or billing["trial_days_left"] <= 3
                )
            ):
                # A silent trial is at-risk even when launch-ready: setup done and
                # gone quiet converts no better than never-set-up (Batch A2).
                counts["trials_at_risk"] += 1
        if tenant["plan_status"] == "active":
            counts["active"] += 1
            counts["active_mrr_cents"] += config.SAAS_PRICE_CENTS
        if tenant["plan_status"] in attention_statuses or (billing and billing["tone"] == "block"):
            counts["attention"] += 1
        if domain_state == "pending":
            counts["custom_domains_pending"] += 1
        if domain_state == "verified":
            counts["custom_domains_verified"] += 1
        rows.append(
            {
                "tenant": tenant,
                "billing": billing,
                "launch": launch,
                "domain_state": domain_state,
                "tenant_url": tenant_url(tenant["slug"], "/admin/login"),
                "account_url": tenant_url(tenant["slug"], "/admin/account"),
                "data_path": str(tenant_data_path(tenant["slug"])),
                "db_exists": tenant_db_path(tenant["slug"]).exists(),
                "card_on_file": bool(tenant.get("stripe_customer_id")),
                "last_login_at": tenant.get("last_login_at"),
                "silent_days": silent_days,
            }
        )
    counts["support_queue"] = counts["attention"] + counts["custom_domains_pending"]
    if counts["total"]:
        counts["average_launch_score"] = round(counts["launch_score_total"] / counts["total"])
    return {
        "counts": counts,
        "growth": operator_growth_metrics(counts, source_counts),
        "rows": rows,
    }


def operator_launch_checklist(overview: dict | None = None, preflight: dict | None = None) -> dict:
    overview = overview or operator_tenant_overview()
    counts = overview["counts"]
    stripe_ready = bool(
        config.STRIPE_SECRET_KEY
        and config.SAAS_STRIPE_PRICE_ID
        and config.SAAS_STRIPE_WEBHOOK_SECRET
    )
    items = [
        {
            "label": "Hosted preflight is ready",
            "detail": "Environment, pricing, paths, Docker/Caddy assets, and secrets are launch-safe.",
            "done": bool(preflight and preflight.get("ready")),
            "href": "#preflight",
        },
        {
            "label": "Stripe billing is configured",
            "detail": "The $20/month Price ID, secret key, and SaaS webhook secret are present.",
            "done": stripe_ready,
            "href": "#preflight",
        },
        {
            "label": "Public demo and pricing are linked",
            "detail": "Buyers can preview F&B and wedding workflows before starting a trial.",
            "done": True,
            "href": platform_url("/demo"),
        },
        {
            "label": "At least one test studio exists",
            "detail": "Create a trial tenant from /pricing and verify login, onboarding, and billing.",
            "done": counts["total"] > 0,
            "href": platform_url("/pricing"),
        },
        {
            "label": "Support queue is clear",
            "detail": "No past-due billing states or pending custom-domain checks need attention.",
            "done": counts["support_queue"] == 0,
            "href": "#tenants",
        },
    ]
    done = sum(1 for item in items if item["done"])
    remaining = len(items) - done
    if remaining == 0:
        headline = "Launch room is clear"
        detail = "The hosted offer is ready for a public launch pass."
    else:
        headline = f"{remaining} launch check{'s' if remaining != 1 else ''} left"
        detail = "Clear these before pushing paid traffic to the $20/month hosted offer."
    return {
        "done": done,
        "total": len(items),
        "items": items,
        "headline": headline,
        "detail": detail,
    }


def _mailto(owner_email: str, subject: str, body: str) -> str:
    return f"mailto:{quote(owner_email, safe='@.+_-')}?subject={quote(subject)}&body={quote(body)}"


def operator_trial_nudges(overview: dict | None = None) -> list[dict]:
    """Manual lifecycle prompts for the operator.

    This intentionally drafts mailto links instead of sending automation. It
    gives one founder the highest-leverage trial follow-ups while keeping the
    beta product honest and observable.
    """
    overview = overview or operator_tenant_overview()
    nudges: list[dict] = []
    for row in overview["rows"]:
        tenant = row["tenant"]
        billing = row["billing"]
        launch = row["launch"]
        status = tenant["plan_status"]
        days_left = billing["trial_days_left"]
        label = ""
        reason = ""
        priority = 9
        tone = "is-draft"
        if status == "trialing":
            if days_left is None or days_left <= 3:
                if launch["complete"]:
                    label = "Conversion nudge"
                    reason = "trial ends soon and the studio is launch-ready"
                    tone = "is-active"
                else:
                    label = "Trial rescue"
                    reason = "trial ends soon and setup is not launch-ready"
                    tone = "is-declined"
                priority = 0
            elif not launch["complete"] and launch["score"] < 50:
                label = "Setup nudge"
                reason = "setup is still early in the trial"
                priority = 1
                tone = "is-draft"
            elif launch["complete"]:
                label = "Conversion nudge"
                reason = "studio is launch-ready during the trial"
                priority = 2
                tone = "is-active"
        elif status in {"past_due", "unpaid", "incomplete", "incomplete_expired"}:
            label = "Billing recovery"
            reason = "billing needs attention"
            priority = 0
            tone = "is-declined"
        if not label:
            continue
        subject = f"{label}: {tenant['studio_name']} on Mise"
        body = "\n\n".join(
            [
                f"Hi {tenant['studio_name']},",
                f"I was checking your Mise studio and noticed {reason}.",
                f"Studio login: {row['tenant_url']}",
                "Want help getting the last pieces live? Reply here and I can point you to the quickest next step.",
            ]
        )
        nudges.append(
            {
                "tenant": tenant,
                "label": label,
                "reason": reason,
                "tone": tone,
                "priority": priority,
                "days_left": days_left,
                "mailto": _mailto(tenant["owner_email"], subject, body),
                "account_url": row["account_url"],
            }
        )
    return sorted(
        nudges,
        key=lambda item: (
            item["priority"],
            item["days_left"] if item["days_left"] is not None else 99,
            item["tenant"]["created_at"],
        ),
    )


# Days of owner silence before a trialing studio counts as at-risk (Batch A2).
SILENT_TRIAL_DAYS = 5


def touch_tenant_login(tenant_id: int) -> None:
    """Stamp last_login_at on a successful tenant admin login (Batch A2)."""
    with control_connect() as con:
        con.execute("UPDATE tenants SET last_login_at=datetime('now') WHERE id=?", (tenant_id,))


def _days_since(stamp: str | None) -> int | None:
    """Whole days since a control-DB datetime('now') stamp (UTC, naive) — None if unset."""
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return max((datetime.now(UTC).replace(tzinfo=None) - then).days, 0)


def join_waitlist(email: str, source: str | None = None, campaign: str | None = None) -> str:
    """Record an invite-gate rejection's email (Batch A3) — 'new', 'repeat', or 'invalid'.

    Before this, a launch-buzz visitor without an invite code was a DISCARDED
    email: the 403 told them to reply to an invite they don't have. Lower-cased
    and unique so repeat joins are silent no-ops; the caller shows the same
    success either way (never leaks whether an address was already on the list).
    """
    email = (email or "").strip().lower()[:200]
    if "@" not in email or "." not in email.rsplit("@", 1)[-1] or len(email) < 6:
        return "invalid"
    with control_connect() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO waitlist (email, source, campaign) VALUES (?,?,?)",
            (email, (source or "").strip()[:80] or None, (campaign or "").strip()[:80] or None),
        )
        return "new" if cur.rowcount else "repeat"


def waitlist_entries(limit: int = 200) -> list[dict]:
    with control_connect() as con:
        rows = con.execute(
            "SELECT email, source, campaign, created_at FROM waitlist ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def waitlist_export_csv() -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["email", "source", "campaign", "created_at"])
    writer.writeheader()
    for row in waitlist_entries(limit=100000):
        writer.writerow(row)
    return output.getvalue()


FEEDBACK_MAX_CHARS = 2000


def record_tenant_feedback(tenant_id: int, page: str, message: str) -> None:
    """One row per submission from the in-admin Help & feedback form (Batch A1).

    The beta promise is 'every confusion becomes copy, onboarding, or a blocker' —
    that needs a capture path INSIDE the product; before this the only route from a
    confused tenant to the operator was finding the support email on the public
    marketing pages. Length caps here, not in the route, so every caller is bounded.
    """
    with control_connect() as con:
        con.execute(
            "INSERT INTO tenant_feedback (tenant_id, page, message) VALUES (?,?,?)",
            (tenant_id, (page or "").strip()[:200], message.strip()[:FEEDBACK_MAX_CHARS]),
        )


def recent_tenant_feedback(limit: int = 50, status: str | None = None) -> list[dict]:
    """Newest feedback first, tenant-attributed, for the operator console panel.

    status='new' is the console's working queue (Batch D2); None is everything —
    the weekly digest and the archive read all of a week's notes regardless of
    whether they've been triaged.
    """
    where = "AND f.status=?" if status else ""
    params: tuple = (status, limit) if status else (limit,)
    with control_connect() as con:
        rows = con.execute(
            f"""SELECT f.id, f.page, f.message, f.created_at, f.status,
                      t.slug, t.studio_name, t.owner_email
               FROM tenant_feedback f JOIN tenants t ON t.id = f.tenant_id
               WHERE 1=1 {where}
               ORDER BY f.id DESC LIMIT ?""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def departed_needs_cancel() -> list[dict]:
    """Deleted studios whose platform-subscription cancel FAILED and is unresolved.

    These are excluded from operator_tenant_overview (they're tombstones), so this
    is the only console surface for a departure that may still be billing — the
    'never a silent live charge' guarantee delete_tenant_studio promises.
    """
    with control_connect() as con:
        rows = con.execute(
            """SELECT id, studio_name, owner_email, stripe_subscription_id, cancel_failed_at
               FROM tenants WHERE cancel_failed_at IS NOT NULL ORDER BY cancel_failed_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def operator_tenant_export_csv(overview: dict | None = None) -> str:
    overview = overview or operator_tenant_overview()
    output = io.StringIO()
    fieldnames = [
        "id",
        "slug",
        "studio_name",
        "owner_email",
        "plan_status",
        "trial_days_left",
        "launch_score",
        "launch_ready",
        "domain_state",
        "custom_domain",
        "signup_source",
        "signup_campaign",
        "signup_referrer",
        "active_mrr_cents",
        "trial_pipeline_cents",
        "tenant_url",
        "data_path",
        "created_at",
        "updated_at",
        "last_login_at",
        "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in overview["rows"]:
        tenant = row["tenant"]
        billing = row["billing"]
        writer.writerow(
            {
                "id": tenant["id"],
                "slug": tenant["slug"],
                "studio_name": tenant["studio_name"],
                "owner_email": tenant["owner_email"],
                "plan_status": tenant["plan_status"],
                "trial_days_left": billing["trial_days_left"]
                if billing["trial_days_left"] is not None
                else "",
                "launch_score": row["launch"]["score"],
                "launch_ready": "yes" if row["launch"]["complete"] else "no",
                "domain_state": row["domain_state"],
                "custom_domain": tenant.get("custom_domain") or "",
                "signup_source": tenant.get("signup_source") or "direct",
                "signup_campaign": tenant.get("signup_campaign") or "",
                "signup_referrer": tenant.get("signup_referrer") or "",
                "active_mrr_cents": config.SAAS_PRICE_CENTS
                if tenant["plan_status"] == "active"
                else 0,
                "trial_pipeline_cents": config.SAAS_PRICE_CENTS
                if tenant["plan_status"] == "trialing"
                else 0,
                "tenant_url": row["tenant_url"],
                "data_path": row["data_path"],
                "created_at": tenant["created_at"],
                "updated_at": tenant.get("updated_at") or "",
                "last_login_at": tenant.get("last_login_at") or "",
                "notes": tenant.get("notes") or "",
            }
        )
    return output.getvalue()


def current_tenant() -> dict | None:
    return _TENANT_CTX.get()


def current_tenant_id() -> int | None:
    tenant = current_tenant()
    return int(tenant["id"]) if tenant else None


def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise ValueError("Use 3-32 lowercase letters, numbers, or hyphens.")
    if slug in _RESERVED_SLUGS:
        raise ValueError("That studio URL is reserved.")
    return slug


def validate_brand_accent(value: str) -> str:
    value = (value or _DEFAULT_BRAND_ACCENT).strip()
    if not _HEX_COLOR_RE.match(value):
        raise ValueError("Brand accent must be a hex color like #2f5c45.")
    return value.lower()


def sanitize_attribution(value: str | None, *, max_len: int = 80) -> str | None:
    if not isinstance(value, str):
        return None
    value = (value or "").strip()
    if not value:
        return None
    cleaned = _ATTRIBUTION_RE.sub("", value)
    cleaned = " ".join(cleaned.split())
    return cleaned[:max_len] or None


def normalize_custom_domain(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "://" in raw:
        parsed = urlsplit(raw)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Use only the custom domain, not a full URL path.")
        raw = parsed.netloc
    if "/" in raw or "?" in raw or "#" in raw:
        raise ValueError("Use only the custom domain, not a URL path.")
    host = _host_only(raw)
    root = _root_host_only()
    marketing = _host_only(config.SAAS_MARKETING_HOST)
    if host in {root, marketing, f"www.{root}"} or host.endswith(f".{root}"):
        raise ValueError("Use the assigned Mise subdomain for hosted Mise domains.")
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253:
        raise ValueError("Use a real domain such as studio.example.com.")
    if any(not _DOMAIN_LABEL_RE.match(label) for label in labels):
        raise ValueError("Custom domain contains invalid characters.")
    return host


def create_tenant(
    slug: str,
    studio_name: str,
    owner_email: str,
    password: str,
    *,
    signup_source: str | None = None,
    signup_campaign: str | None = None,
    signup_referrer: str | None = None,
) -> dict:
    slug = validate_slug(slug)
    studio_name = (studio_name or "").strip()
    owner_email = (owner_email or "").strip().lower()
    if not studio_name:
        raise ValueError("Studio name is required.")
    if "@" not in owner_email:
        raise ValueError("A valid email is required.")
    if len(password or "") < 8:
        raise ValueError("Use at least 8 characters for the admin password.")
    signup_source = sanitize_attribution(signup_source)
    signup_campaign = sanitize_attribution(signup_campaign)
    signup_referrer = sanitize_attribution(signup_referrer, max_len=160)
    started = _now()
    ends = started + timedelta(days=config.SAAS_TRIAL_DAYS)
    try:
        with control_connect() as con:
            cur = con.execute(
                """INSERT INTO tenants
                   (slug, studio_name, owner_email, admin_password_hash,
                    trial_started_at, trial_ends_at,
                    signup_source, signup_campaign, signup_referrer)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    slug,
                    studio_name,
                    owner_email,
                    passwords.hash_password(password),
                    _iso(started),
                    _iso(ends),
                    signup_source,
                    signup_campaign,
                    signup_referrer,
                ),
            )
            tenant_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("That studio URL is already taken.") from None
    tenant = tenant_by_id(tenant_id)
    ensure_tenant_database(tenant)
    log.info("tenant %s created for %s", slug, owner_email)
    return tenant


def update_tenant_account(
    tenant_id: int,
    *,
    studio_name: str,
    owner_email: str,
    custom_domain: str | None,
    brand_accent: str,
) -> dict:
    studio_name = (studio_name or "").strip()
    owner_email = (owner_email or "").strip().lower()
    if not studio_name:
        raise ValueError("Studio name is required.")
    if "@" not in owner_email:
        raise ValueError("A valid email is required.")
    custom_domain = normalize_custom_domain(custom_domain)
    brand_accent = validate_brand_accent(brand_accent)
    current = tenant_by_id(tenant_id)
    if not current:
        raise ValueError("Tenant not found.")
    existing = tenant_by_custom_domain(custom_domain) if custom_domain else None
    if existing and existing["id"] != tenant_id:
        raise ValueError("That custom domain is already connected to another studio.")
    verified_at = current.get("custom_domain_verified_at")
    if custom_domain != current.get("custom_domain"):
        verified_at = None
    with control_connect() as con:
        con.execute(
            """UPDATE tenants
               SET studio_name=?, owner_email=?, custom_domain=?,
                   custom_domain_verified_at=?, brand_accent=?, updated_at=?
               WHERE id=?""",
            (
                studio_name,
                owner_email,
                custom_domain,
                verified_at,
                brand_accent,
                _iso(_now()),
                tenant_id,
            ),
        )
    return tenant_by_id(tenant_id)


def update_tenant_billing(
    tenant_id: int,
    *,
    plan_status: str | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    con: sqlite3.Connection | None = None,
) -> None:
    updates = ["updated_at=?"]
    params: list = [_iso(_now())]
    if plan_status:
        updates.append("plan_status=?")
        params.append(plan_status)
    if stripe_customer_id:
        updates.append("stripe_customer_id=?")
        params.append(stripe_customer_id)
    if stripe_subscription_id:
        updates.append("stripe_subscription_id=?")
        params.append(stripe_subscription_id)
    params.append(tenant_id)
    sql = f"UPDATE tenants SET {', '.join(updates)} WHERE id=?"
    if con is not None:
        # Join the caller's open transaction (webhook exactly-once: the billing
        # effect must commit atomically with the idempotency marker).
        con.execute(sql, params)
        return
    with control_connect() as fresh:
        fresh.execute(sql, params)


def _payments_status(tenant: dict) -> dict:
    """Masked, render-safe view of the tenant's client-payment connection.

    The raw secret is never sent back to the browser — only a short mask and the
    key mode (live/test) derived from its prefix.
    """
    key = (tenant.get("client_stripe_secret_key") or "").strip()
    webhook = (tenant.get("client_stripe_webhook_secret") or "").strip()
    if key.startswith(("sk_live_", "rk_live_")):
        mode = "live"
    elif key:
        mode = "test"
    else:
        mode = ""
    masked = f"{key[:7]}…{key[-4:]}" if len(key) > 14 else ("configured" if key else "")
    return {
        "connected": bool(key),
        "mode": mode,
        "masked_key": masked,
        "webhook_set": bool(webhook),
    }


def set_tenant_client_stripe(tenant_id: int, secret_key: str, webhook_secret: str) -> None:
    """Write the tenant's OWN client-payment Stripe credentials (ADR 0049/0054).

    Empty values disconnect: features.client_stripe_secret_key() resolves "" and the
    pay button falls back to fail-closed off. When the webhook secret CHANGES
    (including disconnect), the outgoing one is kept as _prev and still accepted for
    webhook verification — a checkout link stays payable ~24h and Stripe retries for
    days, so without this grace a rotation mid-flight would leave a client charged
    with the invoice never marked paid. Deliberately does not stamp updated_at
    (the past_due dunning clock, ADR 0050).
    """
    new_key = secret_key.strip() or None
    new_webhook = webhook_secret.strip() or None
    current = tenant_by_id(tenant_id) or {}
    old_webhook = (current.get("client_stripe_webhook_secret") or "").strip() or None
    prev = (current.get("client_stripe_webhook_secret_prev") or "").strip() or None
    if old_webhook and old_webhook != new_webhook:
        prev = old_webhook
    with control_connect() as con:
        con.execute(
            "UPDATE tenants SET client_stripe_secret_key=?, client_stripe_webhook_secret=?, "
            "client_stripe_webhook_secret_prev=? WHERE id=?",
            (new_key, new_webhook, prev, tenant_id),
        )


def _verify_stripe_secret_key(secret_key: str) -> str | None:
    """Best-effort live check that a key is usable; returns an error message or None.

    Auth (401) and permission (403) rejections are hard errors — both are
    deterministic, and saving such a key would surface as a 500 on the *client's*
    pay click later, the worst possible place. Network/other failures do not block
    the save (format is already validated and Stripe may be briefly down).
    """
    stripe_mod = _stripe()
    try:
        stripe_mod.Account.retrieve(api_key=secret_key)
    except stripe_mod.AuthenticationError:
        return "Stripe rejected that secret key. Copy it exactly from Developers → API keys."
    except stripe_mod.PermissionError:
        return (
            "That key doesn't have enough access to verify. Use your standard secret key "
            "(sk_), or a restricted key that includes at least Account read and "
            "Checkout Sessions write."
        )
    except Exception as exc:
        log.warning("stripe key verification skipped (transient error: %s); saving key", exc)
    return None


def set_tenant_password(tenant_id: int, password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("Use at least 8 characters for the admin password.")
    # Deliberately does NOT stamp updated_at — that column doubles as the
    # past_due dunning clock (ADR 0050) and a password change must not extend it.
    with control_connect() as con:
        con.execute(
            "UPDATE tenants SET admin_password_hash=? WHERE id=?",
            (passwords.hash_password(password), tenant_id),
        )


# ── Password reset (ADR 0051) — stateless, single-use, 2-hour tokens ──────────

_PWRESET_MAX_AGE = 2 * 3600


def _pwreset_fingerprint(tenant: dict) -> str:
    # Binding the token to a digest of the CURRENT hash makes it single-use:
    # once the password changes (by this token or any other means), every
    # outstanding reset link dies, with no server-side token table.
    return hashlib.sha256((tenant["admin_password_hash"] or "").encode()).hexdigest()[:16]


def make_password_reset_token(tenant: dict) -> str:
    return security.sign_scoped("pwreset", f"{tenant['id']}:{_pwreset_fingerprint(tenant)}")


def redeem_password_reset_token(token: str) -> dict | None:
    """Tenant for a valid, unexpired, still-unused reset token — else None."""
    payload = security.unsign_scoped("pwreset", token, max_age=_PWRESET_MAX_AGE)
    if not payload or ":" not in payload:
        return None
    tenant_id_s, fingerprint = payload.split(":", 1)
    try:
        tenant = tenant_by_id(int(tenant_id_s))
    except ValueError:
        return None
    if not tenant or tenant.get("deleted_at"):
        return None
    if fingerprint != _pwreset_fingerprint(tenant):
        return None  # password already changed → token spent
    return tenant


# ── Studio export + delete (ADR 0051) — the "your data, your call" promises ───


def build_studio_export(tenant: dict) -> Path:
    """Zip the WHOLE studio: a consistent DB snapshot plus every data/media file.

    The DB goes in via sqlite3's backup API (point-in-time consistent under WAL);
    the live db/-wal/-shm files are skipped in favor of that snapshot, and the
    tenant's ``tmp``/``zips`` scratch dirs are excluded (scratch isn't studio data —
    and the export zip itself is written into ``tmp``, on the same volume as the
    data rather than a possibly-small system tmpfs). Blocking work — callers on the
    event loop must offload (the route uses run_in_threadpool). Returns the temp
    zip path; the caller owns deletion (FileResponse background task), and this
    function unlinks it itself if the build fails.
    """
    slug = tenant["slug"]
    data_dir = tenant_data_path(slug)
    ensure_tenant_database(tenant)
    scratch_dir = data_dir / "tmp"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"mise-export-{slug}-", suffix=".zip", dir=scratch_dir, delete=False
    )
    tmp.close()
    tmp_zip = Path(tmp.name)
    live_db_names = {"mise.db", "mise.db-wal", "mise.db-shm"}
    skip_roots = {"tmp", "zips"}  # scratch: never studio data, and where this zip lives
    try:
        with tempfile.TemporaryDirectory() as snap_dir:
            snapshot = Path(snap_dir) / "mise.db"
            src = sqlite3.connect(tenant_db_path(slug))
            try:
                dst = sqlite3.connect(snapshot)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(snapshot, "mise.db")
                for path in sorted(data_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(data_dir)
                    if str(rel) in live_db_names or rel.parts[0] in skip_roots:
                        continue
                    zf.write(path, str(rel))
    except BaseException:
        tmp_zip.unlink(missing_ok=True)
        raise
    return tmp_zip


def delete_tenant_studio(tenant: dict) -> None:
    """Offboard a studio: tombstone the row, stop billing, park the data in trash.

    Deliberately NOT a hard rm: the data dir moves to .trash/<tombstone> so an
    accidental deletion stays operator-recoverable for the retention window
    (runbook), while the slug frees up immediately. The control row keeps its
    Stripe linkage so any final billing events land on the tombstone, not nowhere.

    Order matters. The tombstone UPDATE commits FIRST (durable, reversible by the
    operator); only then do we fire the *irreversible* Stripe cancel — so a control-DB
    failure can never strand a canceled subscription against a still-live-looking row.
    The tombstone slug carries the tenant id so it can never collide (unique index),
    even on a same-second retry. Idempotent: a row already tombstoned is a no-op.
    """
    if tenant.get("deleted_at"):
        return
    slug = tenant["slug"]
    tombstone = f"{slug}-deleted-{tenant['id']}-{_now().strftime('%Y%m%d%H%M%S')}"
    now = _iso(_now())
    with control_connect() as con:
        con.execute(
            "UPDATE tenants SET slug=?, plan_status='canceled', deleted_at=?, "
            "custom_domain=NULL, updated_at=? WHERE id=? AND deleted_at IS NULL",
            (tombstone, now, now, tenant["id"]),
        )
    # DB is now committed and the studio is logically gone. Deleting must stop the
    # $20 charge — best-effort cancel; on failure the row is already canceled and the
    # operator console surfaces it for manual follow-up (never a silent live charge).
    if config.STRIPE_SECRET_KEY and tenant.get("stripe_subscription_id"):
        try:
            _stripe().Subscription.cancel(
                tenant["stripe_subscription_id"], api_key=config.STRIPE_SECRET_KEY
            )
        except Exception:
            # A swallowed cancel used to be a silent live charge: the tombstone is
            # excluded from the console, so nothing surfaced it. Stamp it (the console
            # lists cancel-failed departures for a manual Stripe cancel) and ping now.
            log.exception("stripe cancel failed for %s (operator follow-up needed)", slug)
            with control_connect() as con:
                con.execute("UPDATE tenants SET cancel_failed_at=? WHERE id=?", (now, tenant["id"]))
            from . import alerts  # lazy: alerts→features would cycle at import time

            alerts.notify(
                f"Stripe cancel FAILED for deleted studio {tenant['studio_name']} "
                f"({slug}) — subscription {tenant['stripe_subscription_id']} may still be "
                "charging. Cancel it in Stripe, then resolve it in /admin/saas."
            )
    data_dir = tenant_data_path(slug)
    if data_dir.exists():
        trash = config.SAAS_TENANT_DATA_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        data_dir.rename(trash / tombstone)
    _MIGRATED_TENANT_DBS.discard(str(tenant_db_path(slug)))
    log.info("tenant %s deleted by owner (tombstone %s)", slug, tombstone)


def operator_update_tenant_status(tenant_id: int, plan_status: str) -> dict:
    allowed = {
        "trialing",
        "active",
        "past_due",
        "canceled",
        "unpaid",
        "paused",
        "incomplete",
        "incomplete_expired",
    }
    if plan_status not in allowed:
        raise ValueError("Unsupported billing status.")
    update_tenant_billing(tenant_id, plan_status=plan_status)
    tenant = tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("Tenant not found.")
    return tenant


def operator_set_domain_verified(tenant_id: int, *, verified: bool) -> dict:
    tenant = tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("Tenant not found.")
    if not tenant.get("custom_domain"):
        raise ValueError("Tenant has no custom domain.")
    verified_at = _iso(_now()) if verified else None
    with control_connect() as con:
        con.execute(
            "UPDATE tenants SET custom_domain_verified_at=?, updated_at=? WHERE id=?",
            (verified_at, _iso(_now()), tenant_id),
        )
    return tenant_by_id(tenant_id)


def mark_custom_domain_verified(tenant: dict, host: str) -> dict:
    host = _host_only(host)
    if not tenant.get("custom_domain") or tenant["custom_domain"] != host:
        return tenant
    if tenant.get("custom_domain_verified_at"):
        return tenant
    with control_connect() as con:
        con.execute(
            "UPDATE tenants SET custom_domain_verified_at=?, updated_at=? WHERE id=?",
            (_iso(_now()), _iso(_now()), tenant["id"]),
        )
    updated = tenant_by_id(tenant["id"])
    return updated or tenant


def ensure_tenant_database(tenant: dict | None) -> None:
    if not tenant:
        return
    slug = tenant["slug"]
    data_path = tenant_data_path(slug)
    for path in (
        data_path,
        data_path / "media",
        data_path / "zips",
        data_path / "tmp",
        data_path / "brand",
        data_path / "receipts",
    ):
        path.mkdir(parents=True, exist_ok=True)
    path_key = str(tenant_db_path(slug))
    if path_key not in _MIGRATED_TENANT_DBS:
        db.migrate(tenant_db_path(slug))
        _MIGRATED_TENANT_DBS.add(path_key)


@contextmanager
def tenant_runtime(tenant_or_slug):
    tenant = tenant_by_slug(tenant_or_slug) if isinstance(tenant_or_slug, str) else tenant_or_slug
    if not tenant:
        raise RuntimeError("tenant not found")
    ensure_tenant_database(tenant)
    tenant_token = _TENANT_CTX.set(dict(tenant))
    db_token = db.set_request_db_path(tenant_db_path(tenant["slug"]))
    dir_tokens = config.set_runtime_dirs(tenant_data_path(tenant["slug"]))
    try:
        yield tenant
    finally:
        config.reset_runtime_dirs(dir_tokens)
        db.reset_request_db_path(db_token)
        _TENANT_CTX.reset(tenant_token)


def tenant_has_access(tenant: dict) -> bool:
    status = tenant["plan_status"]
    if status == "active":
        return True
    if status == "trialing":
        trial_ends_at = _parse_iso(tenant["trial_ends_at"])
        return bool(trial_ends_at and trial_ends_at >= _now())
    if status == "past_due":
        # Dunning grace (ADR 0050): Stripe retries a failed card for days; an instant
        # hard-block turns a transient decline into churn. Access continues for the
        # grace window measured from the status flip (updated_at). Terminal states
        # (unpaid/canceled) still block immediately; missing updated_at blocks too
        # (fail-closed, same as before this grace existed).
        updated_at = _parse_iso(tenant.get("updated_at"))
        if updated_at:
            grace = timedelta(days=config.SAAS_PAST_DUE_GRACE_DAYS)
            return _now() <= updated_at + grace
    return False


def tenant_billing_context(tenant: dict | None) -> dict | None:
    """Small presentation model for hosted billing status banners.

    The middleware remains the enforcement point. This helper only makes the
    same state visible in templates so trial/payment problems are not silent.
    """
    if not tenant:
        return None
    status = tenant["plan_status"]
    access_ok = tenant_has_access(tenant)
    trial_ends_at = _parse_iso(tenant.get("trial_ends_at"))
    days_left = None
    if trial_ends_at:
        seconds_left = (trial_ends_at - _now()).total_seconds()
        days_left = max(0, int(seconds_left // 86400))
    if status == "active":
        tone = "ok"
        message = "Hosted plan active at $20/month."
    elif status == "trialing" and access_ok:
        if days_left == 0:
            message = "Trial ends today. Add billing to keep the studio live."
            tone = "warn"
        elif days_left is not None and days_left <= 3:
            message = (
                f"Trial ends in {days_left} day{'s' if days_left != 1 else ''}. Add billing soon."
            )
            tone = "warn"
        else:
            message = (
                f"Free trial active. {days_left} days left."
                if days_left is not None
                else "Free trial active."
            )
            tone = "ok"
    elif status == "trialing":
        tone = "block"
        message = "Trial ended. Open billing to continue using the hosted studio."
    elif status == "past_due" and access_ok:
        tone = "warn"
        message = (
            "Payment problem — your card was declined and Stripe is retrying. "
            "Open billing to update it and keep the studio live."
        )
    elif status in {"past_due", "unpaid", "incomplete", "incomplete_expired"}:
        tone = "block"
        message = "Billing needs attention. Open billing to restore studio access."
    elif status == "canceled":
        tone = "block"
        message = "Subscription canceled. Open billing to restart the hosted studio."
    else:
        tone = "block"
        message = "Subscription status needs attention. Open billing to continue."
    return {
        "status": status,
        "access_ok": access_ok,
        "trial_ends_at": tenant.get("trial_ends_at"),
        "trial_days_left": days_left,
        "tone": tone,
        "message": message,
    }


# Marketing paths crawlers may index on the platform host (Batch B3). The
# common_headers middleware stamps X-Robots-Tag: noindex on everything outside
# the STUDIO site's INDEXABLE set — which silently overrode the index,follow
# meta these pages have declared since B1 (headers beat meta for noindex).
MARKETING_INDEXABLE = {
    "/",
    "/pricing",
    "/demo",
    "/terms",
    "/privacy",
    "/support",
    "/robots.txt",
    "/sitemap.xml",
}


def _platform_path(path: str) -> bool:
    return (
        path
        in {
            "/",
            "/pricing",
            "/demo",
            "/start-trial",
            "/waitlist",
            "/terms",
            "/privacy",
            "/support",
            "/healthz",
            "/favicon.ico",
            "/robots.txt",
            "/sitemap.xml",
        }
        or path.startswith("/static/")
        # API callers must always receive an API response. Redirecting an unknown
        # root-host request to /pricing would turn auth/discovery failures into an
        # HTML 303 that a native client cannot safely interpret.
        or path == "/api/v1"
        or path.startswith("/api/v1/")
        or path in {"/admin/login", "/admin/logout", "/admin/saas"}
        or path.startswith("/admin/saas/")
        or path in {"/webhooks/stripe", "/webhooks/stripe/saas"}
    )


def _billing_allowed_path(path: str) -> bool:
    return (
        path.startswith("/admin/billing")
        or path.startswith("/admin/account")
        # A locked-out owner must still be able to reset their password (and pay) —
        # and to take their data or leave: export/delete are exactly what an expired
        # or canceling customer needs, and the billing page advertises both.
        or path in {"/admin/login", "/admin/logout", "/admin/forgot", "/admin/reset", "/healthz"}
        or path in {"/admin/export-studio", "/admin/delete-studio"}
        or path.startswith("/static/")
        # Keep the narrow session recovery surface reachable while a hosted studio
        # is billing-locked. Feature/content routes remain blocked with 402.
        or path
        in {
            "/api/v1/tenant",
            "/api/v1/auth/studio/login",
            "/api/v1/auth/refresh",
            "/api/v1/auth/logout",
            "/api/v1/me",
        }
        or path.startswith("/api/v1/auth/sessions")
        or path in {"/webhooks/stripe", "/webhooks/stripe/saas"}
    )


def _api_problem(request: Request, status: int, code: str, title: str, detail: str) -> JSONResponse:
    """Problem response for failures that happen before the mounted API runs."""

    return JSONResponse(
        {
            "type": f"https://mise.example/problems/{code.replace('.', '-')}",
            "title": title,
            "status": status,
            "code": code,
            "detail": detail,
            "request_id": getattr(request.state, "request_id", None),
            "errors": [],
        },
        status_code=status,
        media_type="application/problem+json",
    )


async def tenant_middleware(request: Request, call_next):
    if not config.SAAS_MODE:
        return await call_next(request)
    path = request.url.path
    slug = tenant_slug_from_host(request.headers.get("host", ""))
    if not slug:
        if _platform_path(path):
            return await call_next(request)
        return RedirectResponse("/pricing", status_code=303)

    tenant = tenant_by_slug(slug)
    # A deleted studio's tombstone slug must be as gone as a never-registered one:
    # serving it would re-provision an empty data dir via ensure_tenant_database and
    # let the old password log in to the husk (ADR 0051).
    if not tenant or tenant.get("deleted_at"):
        if path == "/api/v1" or path.startswith("/api/v1/"):
            return _api_problem(
                request,
                404,
                "tenant.not_found",
                "Studio not found",
                "This studio is unavailable.",
            )
        if "text/html" in request.headers.get("accept", ""):
            return templates.TemplateResponse(
                request,
                "saas/unknown_tenant.html",
                {"slug": slug, "root_url": platform_url("/pricing")},
                status_code=404,
            )
        return JSONResponse({"detail": "unknown tenant"}, status_code=404)

    tenant = mark_custom_domain_verified(tenant, request.headers.get("host", ""))
    with tenant_runtime(tenant):
        request.state.tenant = dict(tenant)
        request.state.saas_billing = tenant_billing_context(tenant)
        if not tenant_has_access(tenant) and not _billing_allowed_path(path):
            if path.startswith("/admin"):
                return RedirectResponse("/admin/billing?expired=1", status_code=303)
            # A client following the studio's gallery/invoice link gets a branded,
            # neutral page (never the raw "subscription required" JSON, which both
            # dumps a blob and blames the studio's billing). Non-browser callers keep
            # the JSON 402 contract — mirror the unknown-tenant handling above.
            if path == "/api/v1" or path.startswith("/api/v1/"):
                return _api_problem(
                    request,
                    402,
                    "tenant.subscription_required",
                    "Studio unavailable",
                    "This studio is temporarily unavailable.",
                )
            if "text/html" in request.headers.get("accept", ""):
                return templates.TemplateResponse(
                    request,
                    "saas/studio_unavailable.html",
                    {"studio_name": tenant.get("studio_name") or "This studio"},
                    status_code=402,
                )
            return JSONResponse({"detail": "subscription required"}, status_code=402)
        return await call_next(request)


def _pricing_context(
    error: str | None = None,
    values: dict | None = None,
    *,
    path: str = "/pricing",
    request: Request | None = None,
) -> dict:
    values = dict(values or {})
    if request is not None:
        values.setdefault(
            "signup_source",
            sanitize_attribution(
                request.query_params.get("utm_source") or request.query_params.get("ref")
            )
            or "",
        )
        values.setdefault(
            "signup_campaign",
            sanitize_attribution(request.query_params.get("utm_campaign")) or "",
        )
        values.setdefault(
            "signup_referrer",
            sanitize_attribution(request.headers.get("referer"), max_len=160) or "",
        )
    return {
        "error": error,
        "values": values,
        "price_cents": config.SAAS_PRICE_CENTS,
        "trial_days": config.SAAS_TRIAL_DAYS,
        "root_domain": _root_domain(),
        "canonical_url": platform_url(path),
        "home_url": platform_url("/"),
        "pricing_url": platform_url("/pricing"),
        "demo_url": platform_url("/demo"),
        "invite_required": bool(config.SAAS_INVITE_CODE),
    }


@router.get("/", response_class=HTMLResponse)
async def saas_home(request: Request):
    return templates.TemplateResponse(request, "saas/home.html", _pricing_context(path="/"))


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    return templates.TemplateResponse(
        request, "saas/pricing.html", _pricing_context(path="/pricing", request=request)
    )


@router.get("/demo", response_class=HTMLResponse)
async def demo(request: Request):
    return templates.TemplateResponse(request, "saas/demo.html", _pricing_context(path="/demo"))


_LEGAL_DOCS = {
    "terms": "Terms of Service",
    "privacy": "Privacy Policy",
    "support": "Support",
}


@router.get("/terms", response_class=HTMLResponse)
async def legal_terms(request: Request):
    return _legal_page(request, "terms")


@router.get("/privacy", response_class=HTMLResponse)
async def legal_privacy(request: Request):
    return _legal_page(request, "privacy")


@router.get("/support", response_class=HTMLResponse)
async def legal_support(request: Request):
    return _legal_page(request, "support")


def _legal_page(request: Request, doc: str) -> HTMLResponse:
    ctx = _pricing_context(path=f"/{doc}")
    ctx.update(
        {"doc": doc, "doc_title": _LEGAL_DOCS[doc], "support_email": config.SAAS_SUPPORT_EMAIL}
    )
    return templates.TemplateResponse(request, "saas/legal.html", ctx)


@router.get("/robots.txt", response_class=PlainTextResponse)
async def saas_robots():
    """Host-aware robots.txt (Batch B3).

    In SAAS mode this router shadows the studio site's route on every host, so
    tenant hosts delegate back to it — their rules stay byte-identical to before.
    The platform host finally gets its own file: until now the middleware 303'd
    crawlers to /pricing, which reads as 'no robots.txt' at best.
    """
    if current_tenant() is not None:
        from .public import site

        return await site.robots()
    return f"User-agent: *\nDisallow: /admin\nAllow: /\nSitemap: {platform_url('/sitemap.xml')}\n"


@router.get("/sitemap.xml")
async def saas_sitemap():
    if current_tenant() is not None:
        from .public import site

        return await site.sitemap()
    pages = ["/", "/pricing", "/demo", "/terms", "/privacy", "/support"]
    urls = "".join(f"<url><loc>{platform_url(p)}</loc></url>" for p in pages)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        media_type="application/xml",
    )


@router.post("/start-trial")
async def start_trial(
    request: Request,
    studio_name: str = Form(...),
    owner_email: str = Form(...),
    slug: str = Form(...),
    password: str = Form(...),
    signup_source: str | None = Form(None),
    signup_campaign: str | None = Form(None),
    signup_referrer: str | None = Form(None),
    invite_code: str | None = Form(None),
):
    values = {
        "studio_name": studio_name,
        "owner_email": owner_email,
        "slug": slug,
        "signup_source": signup_source,
        "signup_campaign": signup_campaign,
        "signup_referrer": signup_referrer,
        "invite_code": invite_code,
    }
    # Private-beta gate (ADR 0053): checked before any provisioning happens.
    # Encoded: compare_digest raises TypeError on non-ASCII str, and this is a
    # public endpoint — a pasted smart-quote in the code must 403, not 500.
    if config.SAAS_INVITE_CODE and not secrets.compare_digest(
        (invite_code or "").strip().encode(), config.SAAS_INVITE_CODE.encode()
    ):
        ctx = _pricing_context(
            "Mise is in private beta — that invite code isn't valid. "
            "Reply to your invite email if you need one.",
            values,
            path="/pricing",
        )
        # Batch A3: don't discard the visitor — offer the waitlist right here,
        # with the email they already typed pre-filled.
        ctx["waitlist_offer"] = True
        return templates.TemplateResponse(request, "saas/pricing.html", ctx, status_code=403)
    try:
        tenant = create_tenant(
            slug,
            studio_name,
            owner_email,
            password,
            signup_source=signup_source,
            signup_campaign=signup_campaign,
            signup_referrer=signup_referrer,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "saas/pricing.html",
            _pricing_context(str(exc), values, path="/pricing"),
            status_code=400,
        )

    success_url = tenant_url(tenant["slug"], "/admin/login?trial=1")
    cancel_url = platform_url("/pricing")
    # The welcome email rides on BOTH exits (checkout and no-Stripe): it is the only
    # durable record of the studio URL for someone who abandons checkout (ADR 0053).
    welcome = _welcome_email_task(tenant)
    if config.STRIPE_SECRET_KEY or config.SAAS_STRIPE_PRICE_ID:
        if not (config.STRIPE_SECRET_KEY and config.SAAS_STRIPE_PRICE_ID):
            return templates.TemplateResponse(
                request,
                "saas/pricing.html",
                _pricing_context(
                    "Stripe billing is not fully configured yet.", values, path="/pricing"
                ),
                status_code=503,
            )
        session = create_subscription_checkout(
            tenant,
            trial_days=config.SAAS_TRIAL_DAYS,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        log.info("tenant %s checkout session %s created", tenant["slug"], session.id)
        return RedirectResponse(session.url, status_code=303, background=welcome)

    return RedirectResponse(success_url, status_code=303, background=welcome)


@router.post("/waitlist")
async def waitlist_join(
    request: Request,
    email: str = Form(...),
    signup_source: str | None = Form(None),
    signup_campaign: str | None = Form(None),
):
    """Invite-gate consolation path (Batch A3): the pricing page offers this form
    when a signup is refused for lacking the beta code."""
    if not config.SAAS_MODE:
        raise HTTPException(status_code=404)
    outcome = join_waitlist(email, signup_source, signup_campaign)
    log.info("waitlist join: %s", outcome)  # outcome only — never the address
    ctx = _pricing_context(None, {"owner_email": email}, path="/pricing", request=request)
    if outcome == "invalid":
        ctx["error"] = "That email doesn't look right — double-check it and try again."
        ctx["waitlist_offer"] = True
        return templates.TemplateResponse(request, "saas/pricing.html", ctx, status_code=400)
    # 'new' and 'repeat' read identically: idempotent, and never leaks list membership.
    ctx["waitlisted"] = True
    return templates.TemplateResponse(request, "saas/pricing.html", ctx)


TRIAL_REMINDER_DAYS = 3


def trial_reminder_sweep() -> int:
    """Email owners whose CARD-LESS trial ends within TRIAL_REMINDER_DAYS — once.

    Platform transactional mail to the OWNER (same class as welcome/reset, ADR 0053)
    — deliberately sent OUTSIDE tenant_runtime so it carries platform identity, and
    deliberately NOT client-facing, so the no-auto-send doctrine is untouched. Targets
    only tenants with no stripe_customer_id: trials with a card convert on their own;
    card-less ones hit the day-14 paywall (ADR 0056) and this is their nudge toward it.
    The stamp is written only after a successful send; a failed send retries next sweep.
    """
    if not (config.SAAS_MODE and mailer.configured()):
        return 0
    with control_connect() as con:
        rows = [
            dict(r)
            for r in con.execute(
                """SELECT * FROM tenants
                   WHERE plan_status='trialing' AND deleted_at IS NULL
                     AND stripe_customer_id IS NULL
                     AND trial_reminder_sent_at IS NULL"""
            ).fetchall()
        ]
    sent = 0
    now = _now()
    for tenant in rows:
        ends = _parse_iso(tenant.get("trial_ends_at"))
        if not ends or ends < now or (ends - now).days > TRIAL_REMINDER_DAYS:
            continue
        days_left = max(1, (ends - now).days)
        billing_url = tenant_url(tenant["slug"], "/admin/billing")
        body = (
            f"Your {tenant['studio_name']} trial ends in about {days_left} "
            f"day{'s' if days_left != 1 else ''}.\n\n"
            f"To keep the studio live at $20/month, start the subscription here:\n"
            f"{billing_url}\n\n"
            "Not ready? You can export your entire studio from the same page anytime "
            "- it's your data either way.\n\n"
            f"Questions? {platform_url('/support')}\n"
        )
        try:
            mailer.send(
                tenant["owner_email"],
                f"Your {tenant['studio_name']} trial ends soon",
                body,
            )
        except Exception:
            log.exception("trial reminder failed for %s (will retry next sweep)", tenant["slug"])
            continue
        with control_connect() as con:
            con.execute(
                "UPDATE tenants SET trial_reminder_sent_at=? WHERE id=?",
                (_iso(now), tenant["id"]),
            )
        sent += 1
    if sent:
        log.info("trial reminders sent: %d", sent)
    return sent


# Days after a trial lapses (or a cancel lands) before the single win-back email —
# far enough from the paywall moment to not read as a dunning notice (Batch C1).
WINBACK_DELAY_DAYS = 3


def winback_sweep() -> int:
    """One respectful come-back email to lapsed trials and canceled tenants — once.

    The trial reminder fires only PRE-expiry (and is itself one-shot), so before
    this sweep a tenant whose trial lapsed got no follow-up ever, and canceled
    subscribers got none at all (launch-gap audit, conversion dimension). Same
    doctrine as trial_reminder_sweep: platform lifecycle mail to the OWNER,
    outside tenant_runtime, never client-facing, stamped only after a successful
    send so failures retry next sweep. One email per tenant, ever — this is a
    door held open, not a drip campaign.
    """
    if not (config.SAAS_MODE and mailer.configured()):
        return 0
    with control_connect() as con:
        rows = [
            dict(r)
            for r in con.execute(
                """SELECT * FROM tenants
                   WHERE plan_status IN ('trialing','canceled')
                     AND deleted_at IS NULL AND winback_sent_at IS NULL"""
            ).fetchall()
        ]
    sent = 0
    now = _now()
    for tenant in rows:
        if tenant["plan_status"] == "trialing":
            # Only trials that actually LAPSED (paywall reached, never converted).
            ends = _parse_iso(tenant.get("trial_ends_at"))
            if not ends or (now - ends).days < WINBACK_DELAY_DAYS:
                continue
            what = "trial ended"
        else:
            # Canceled: measured from the status flip (updated_at moves on it).
            flipped = _parse_iso(tenant.get("updated_at")) or _parse_iso(tenant["created_at"])
            if not flipped or (now - flipped).days < WINBACK_DELAY_DAYS:
                continue
            what = "subscription ended"
        billing_url = tenant_url(tenant["slug"], "/admin/billing")
        body = (
            f"Your {tenant['studio_name']} {what} a few days ago — the studio is still "
            "here, data intact, exactly as you left it.\n\n"
            f"Pick it back up anytime ($20/month, restart in one click):\n{billing_url}\n\n"
            "Rather take your work with you? The same page exports your entire studio — "
            "every gallery, contract, and invoice. It's your data either way.\n\n"
            "And if something was missing or confusing, reply to this email — during the "
            "beta every note gets read and answered by a person.\n\n"
            f"Questions? {platform_url('/support')}\n"
        )
        try:
            mailer.send(
                tenant["owner_email"],
                f"Your {tenant['studio_name']} studio is still here",
                body,
            )
        except Exception:
            log.exception("win-back failed for %s (will retry next sweep)", tenant["slug"])
            continue
        with control_connect() as con:
            con.execute(
                "UPDATE tenants SET winback_sent_at=? WHERE id=?", (_iso(now), tenant["id"])
            )
        sent += 1
    if sent:
        log.info("win-back emails sent: %d", sent)
    return sent


# Days before the grace window lapses when the second (final) dunning email fires.
DUNNING_FINAL_WARN_DAYS = 2


def dunning_sweep() -> int:
    """Owner email when a card declines, and once more as the grace runs out.

    Before this, past_due handling relied on Stripe's own retry emails plus an
    in-admin banner the owner only sees by visiting (launch-gap audit) — a studio
    owner mid-shoot-season could lose access without Mise ever telling them.
    Two one-shot emails per decline EPISODE: the notice when past_due lands, the
    final warning DUNNING_FINAL_WARN_DAYS before the ADR 0050 grace window ends.
    Stamps clear when billing recovers to active, so a future decline notifies
    again. Same platform-lifecycle-mail doctrine as the trial/win-back sweeps.
    """
    if not (config.SAAS_MODE and mailer.configured()):
        return 0
    with control_connect() as con:
        # Recovery reset: billing healthy again → this episode is over.
        con.execute(
            """UPDATE tenants SET dunning_notice_sent_at=NULL, dunning_final_sent_at=NULL
               WHERE plan_status='active'
                 AND (dunning_notice_sent_at IS NOT NULL OR dunning_final_sent_at IS NOT NULL)"""
        )
        rows = [
            dict(r)
            for r in con.execute(
                """SELECT * FROM tenants
                   WHERE plan_status='past_due' AND deleted_at IS NULL
                     AND (dunning_notice_sent_at IS NULL OR dunning_final_sent_at IS NULL)"""
            ).fetchall()
        ]
    sent = 0
    now = _now()
    for tenant in rows:
        flipped = _parse_iso(tenant.get("updated_at"))
        grace_ends = flipped + timedelta(days=config.SAAS_PAST_DUE_GRACE_DAYS) if flipped else None
        days_left = max((grace_ends - now).days, 0) if grace_ends else 0
        billing_url = tenant_url(tenant["slug"], "/admin/billing")
        if tenant.get("dunning_notice_sent_at") is None:
            stamp_col = "dunning_notice_sent_at"
            subject = f"Card declined for {tenant['studio_name']} — quick fix"
            body = (
                f"Stripe couldn't charge the card for {tenant['studio_name']} and is "
                "retrying automatically.\n\n"
                f"Your studio stays live for now — update the card here and nothing "
                f"changes:\n{billing_url}\n\n"
                f"Questions? {platform_url('/support')}\n"
            )
        elif (
            tenant.get("dunning_final_sent_at") is None
            and grace_ends is not None
            and (grace_ends - now).days <= DUNNING_FINAL_WARN_DAYS
        ):
            stamp_col = "dunning_final_sent_at"
            subject = f"{tenant['studio_name']} pauses in about {max(days_left, 1)} day{'s' if max(days_left, 1) != 1 else ''}"
            body = (
                f"The card for {tenant['studio_name']} still hasn't gone through, and "
                f"access pauses in about {max(days_left, 1)} "
                f"day{'s' if max(days_left, 1) != 1 else ''}.\n\n"
                f"One click fixes it — update the card here:\n{billing_url}\n\n"
                "Nothing gets deleted either way: your galleries, contracts, and "
                "invoices wait for you, and you can export everything from the same "
                f"page.\n\nQuestions? {platform_url('/support')}\n"
            )
        else:
            continue
        try:
            mailer.send(tenant["owner_email"], subject, body)
        except Exception:
            log.exception("dunning email failed for %s (will retry next sweep)", tenant["slug"])
            continue
        with control_connect() as con:
            con.execute(f"UPDATE tenants SET {stamp_col}=? WHERE id=?", (_iso(now), tenant["id"]))
        sent += 1
    if sent:
        log.info("dunning emails sent: %d", sent)
    return sent


def _within_days(stamp: str | None, days: int) -> bool:
    parsed = _parse_iso(stamp)
    return parsed is not None and _now() - parsed <= timedelta(days=days)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def weekly_digest_sweep() -> int:
    """One operator email per ISO week: the console's headline, delivered (Batch D1).

    Every other sweep here mails TENANTS; this is the only platform mail addressed
    to the OPERATOR — signups, at-risk trials, fresh feedback, waitlist growth, and
    what lifecycle mail went out, so running the beta doesn't depend on remembering
    to open /admin/saas. Fires on the first tick of each ISO week (Monday 00:00 UTC)
    and is stamped in control_meta only after a successful send — a failed send
    retries next tick, a restart never double-sends, and a server down on Monday
    catches up whenever it returns that week.
    """
    if not (config.SAAS_MODE and mailer.configured() and config.SAAS_SUPPORT_EMAIL):
        return 0
    now = _now()
    iso_week = now.isocalendar()
    week_key = f"{iso_week.year}-W{iso_week.week:02d}"
    if _meta_get("digest_last_week") == week_key:
        return 0

    overview = operator_tenant_overview()
    counts = overview["counts"]
    with control_connect() as con:
        tenants = [dict(r) for r in con.execute("SELECT * FROM tenants").fetchall()]
        waitlist_total = con.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
        # Both sides of this comparison are SQLite datetime('now') strings, so
        # the count stays exact however large the waitlist grows.
        waitlist_new = con.execute(
            "SELECT COUNT(*) FROM waitlist WHERE created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
    new_signups = sum(1 for t in tenants if _within_days(t["created_at"], 7))
    departures = sum(1 for t in tenants if _within_days(t.get("deleted_at"), 7))
    reminders = sum(1 for t in tenants if _within_days(t.get("trial_reminder_sent_at"), 7))
    winbacks = sum(1 for t in tenants if _within_days(t.get("winback_sent_at"), 7))
    dunnings = sum(
        1
        for t in tenants
        if _within_days(t.get("dunning_notice_sent_at"), 7)
        or _within_days(t.get("dunning_final_sent_at"), 7)
    )
    feedback = [f for f in recent_tenant_feedback(limit=200) if _within_days(f["created_at"], 7)]
    nudges = operator_trial_nudges(overview)[:5]

    lines = [
        f"Week {week_key} — {_plural(counts['total'], 'studio')}: "
        f"{counts['active']} paying (${counts['active_mrr_cents'] // 100}/mo), "
        f"{counts['trialing']} trialing, {counts['trials_at_risk']} at risk.",
        "",
        "This week:",
        f"- New studios: {new_signups}" + (f" (departures: {departures})" if departures else ""),
        f"- Waitlist joins: {waitlist_new} (total {waitlist_total})",
        f"- Feedback notes: {len(feedback)}",
        "- Lifecycle mail sent: "
        + ", ".join(
            [
                _plural(reminders, "trial reminder"),
                _plural(winbacks, "win-back"),
                _plural(dunnings, "dunning email"),
            ]
        ),
    ]
    if nudges:
        lines += ["", "Needs a human:"]
        lines += [f"- {n['label']}: {n['tenant']['studio_name']} — {n['reason']}" for n in nudges]
    if feedback:
        lines += ["", "Fresh feedback:"]
        for item in feedback[:5]:
            excerpt = item["message"][:120] + ("…" if len(item["message"]) > 120 else "")
            lines.append(f"- {item['studio_name']} ({item['page'] or 'app'}): {excerpt}")
    lines += ["", f"Console: {platform_url('/admin/saas')}"]
    subject = (
        f"Mise weekly — {_plural(counts['total'], 'studio')}, "
        f"{counts['trials_at_risk']} at risk, {new_signups} new"
    )
    try:
        mailer.send(config.SAAS_SUPPORT_EMAIL, subject, "\n".join(lines) + "\n")
    except Exception:
        log.exception("weekly digest failed (will retry next sweep)")
        return 0
    _meta_set("digest_last_week", week_key)
    log.info("weekly operator digest sent (%s)", week_key)
    return 1


def _remaining_trial_days(tenant: dict) -> int:
    ends = _parse_iso(tenant.get("trial_ends_at"))
    if not ends:
        return 0
    return max(0, (ends - _now()).days)


def create_subscription_checkout(
    tenant: dict, *, trial_days: int, success_url: str, cancel_url: str
):
    """One $20/month subscription Checkout session (signup and recovery share it).

    ``trial_days`` <= 0 omits the trial entirely — Stripe bills immediately, which is
    exactly right for a tenant recovering an abandoned checkout after their trial ran out.
    """
    subscription_data: dict = {"metadata": {"tenant_id": str(tenant["id"]), "slug": tenant["slug"]}}
    if trial_days > 0:
        subscription_data["trial_period_days"] = trial_days
    return _stripe().checkout.Session.create(
        api_key=config.STRIPE_SECRET_KEY,
        mode="subscription",
        customer_email=tenant["owner_email"],
        line_items=[{"price": config.SAAS_STRIPE_PRICE_ID, "quantity": 1}],
        metadata={"tenant_id": str(tenant["id"]), "slug": tenant["slug"]},
        subscription_data=subscription_data,
        success_url=success_url,
        cancel_url=cancel_url,
    )


@router.post("/admin/billing/checkout")
async def billing_checkout(request: Request):
    """Start (or restart) the $20/month subscription from inside the studio.

    This is the recovery path for the audit's biggest funnel leak: a trial that
    abandoned the signup checkout used to hit the day-14 paywall with NO pay button —
    a conversion dead-end only the operator could fix. Reachable while locked out
    because _billing_allowed_path admits /admin/billing*.
    """
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    if not (config.STRIPE_SECRET_KEY and config.SAAS_STRIPE_PRICE_ID):
        raise HTTPException(status_code=503, detail="hosted billing is not configured")
    # A live/attached subscription manages itself in the Stripe portal; checkout is
    # only for no-subscription (abandoned signup) or terminally-canceled tenants.
    if tenant.get("stripe_subscription_id") and tenant["plan_status"] not in {
        "canceled",
        "incomplete_expired",
    }:
        return RedirectResponse("/admin/billing?already=1", status_code=303)
    session = create_subscription_checkout(
        tenant,
        trial_days=_remaining_trial_days(tenant),
        success_url=tenant_url(tenant["slug"], "/admin/billing?subscribed=1"),
        cancel_url=tenant_url(tenant["slug"], "/admin/billing"),
    )
    log.info("tenant %s recovery checkout session %s created", tenant["slug"], session.id)
    return RedirectResponse(session.url, status_code=303)


def _welcome_email_task(tenant: dict) -> BackgroundTask | None:
    """Deferred signup welcome — sent after the response, same pattern as the
    password-reset mail (never blocks the event loop; failures only log)."""
    if not mailer.configured():
        return None
    owner_email = tenant["owner_email"]
    studio_name = tenant["studio_name"]
    slug = tenant["slug"]
    login_url = tenant_url(slug, "/admin/login")
    body = (
        f"Welcome to Mise — {studio_name} is ready.\n\n"
        f"Your studio lives at:\n{tenant_url(slug)}\n\n"
        f"Sign in here (bookmark this):\n{login_url}\n\n"
        f"Your {config.SAAS_TRIAL_DAYS}-day free trial is active. A good first step: sign in "
        "and install a niche preset from the onboarding checklist — it seeds packages, lead "
        "forms, and a demo client so nothing starts blank.\n\n"
        "Your studio is its own isolated database. You can export all of it — or delete it — "
        "anytime from the Billing page.\n\n"
        f"Questions? {platform_url('/support')}\n"
    )

    def _send() -> None:
        try:
            mailer.send(owner_email, f"Your {studio_name} studio is ready", body)
        except Exception:
            log.exception("welcome email failed for %s", slug)

    return BackgroundTask(_send)


@router.post("/webhooks/stripe/saas")
async def saas_stripe_webhook(request: Request):
    if not (config.SAAS_STRIPE_WEBHOOK_SECRET and config.STRIPE_SECRET_KEY):
        raise HTTPException(status_code=503, detail="saas billing webhook not configured")
    payload = await request.body()
    stripe_mod = _stripe()
    try:
        event = stripe_mod.Webhook.construct_event(
            payload,
            request.headers.get("stripe-signature", ""),
            config.SAAS_STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe_mod.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="bad signature")

    return _process_saas_event(event)


def _process_saas_event(event) -> dict:
    """Apply one signature-verified SaaS billing event exactly once.

    The idempotency marker and the billing side-effect commit in the SAME
    control-DB transaction: a crash or error before commit leaves neither (so
    Stripe's retry reprocesses the event); after commit both exist (so the retry
    is a duplicate no-op). Previously the marker committed in its own transaction
    *before* the effect — a crash between the two swallowed the billing event
    forever, because the retry deduped against a marker whose effect never ran.
    """
    obj = event["data"]["object"]
    event_type = event["type"]
    with control_connect() as con:
        try:
            con.execute(
                "INSERT INTO saas_events (id, type) VALUES (?,?)", (event["id"], event["type"])
            )
        except sqlite3.IntegrityError:
            return {"ok": True, "duplicate": True}

        if event_type == "checkout.session.completed":
            metadata = obj.get("metadata") or {}
            tenant_id = int(metadata.get("tenant_id") or 0)
            if tenant_id:
                # Attach the Stripe customer + subscription, but do NOT set plan_status
                # here. This event and customer.subscription.created race — Stripe does
                # not order them — and hardcoding "trialing" would clobber the real
                # "active" status of a LAPSED tenant who just re-subscribed (recovery
                # checkout runs with trial_days=0, so Stripe activates immediately),
                # leaving a just-paid customer locked out by tenant_has_access() until
                # the next subscription event. The customer.subscription.* branch below
                # carries the authoritative status regardless of which arrives last.
                update_tenant_billing(
                    tenant_id,
                    stripe_customer_id=obj.get("customer"),
                    stripe_subscription_id=obj.get("subscription"),
                    con=con,
                )
        elif event_type.startswith("customer.subscription."):
            metadata = obj.get("metadata") or {}
            tenant_id = int(metadata.get("tenant_id") or 0)
            tenant = tenant_by_id(tenant_id) if tenant_id else tenant_by_subscription(obj["id"])
            if tenant:
                status = obj.get("status") or "incomplete"
                update_tenant_billing(
                    tenant["id"],
                    plan_status=status,
                    stripe_customer_id=obj.get("customer"),
                    stripe_subscription_id=obj["id"],
                    con=con,
                )
    return {"ok": True, "type": event_type}


@router.get("/admin/billing", response_class=HTMLResponse)
async def billing(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    delete_errors = {
        "slug": "Type the studio address exactly to confirm deletion.",
        "password": "Wrong password — the studio was not deleted.",
    }
    checkout_available = bool(config.STRIPE_SECRET_KEY and config.SAAS_STRIPE_PRICE_ID) and (
        not tenant.get("stripe_subscription_id")
        or tenant["plan_status"] in {"canceled", "incomplete_expired"}
    )
    return templates.TemplateResponse(
        request,
        "admin/saas_billing.html",
        {
            "tenant": tenant,
            "price_cents": config.SAAS_PRICE_CENTS,
            "access_ok": tenant_has_access(tenant),
            "billing_status": tenant_billing_context(tenant),
            "delete_error": delete_errors.get(request.query_params.get("delete_error", "")),
            "checkout_available": checkout_available,
            "subscribed_notice": request.query_params.get("subscribed") == "1",
            "already_notice": request.query_params.get("already") == "1",
            # tenant_middleware bounces a locked-out owner here with ?expired=1;
            # read it so the page can say WHY they landed, not just that access is off.
            "expired_notice": request.query_params.get("expired") == "1",
        },
    )


@router.post("/admin/billing/portal")
async def billing_portal(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    return_url = f"{urls.public_base_url(request)}/admin/billing"
    portal_url = create_billing_portal_url(tenant, return_url)
    return RedirectResponse(portal_url, status_code=303)


@router.get("/admin/forgot", response_class=HTMLResponse)
async def forgot_password_form(request: Request):
    if not current_tenant():
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/forgot.html",
        {"sent": False, "email_on": mailer.configured(), "support_url": platform_url("/support")},
    )


@router.post("/admin/forgot")
async def forgot_password(request: Request, email: str = Form(...)):
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    # Same response whether or not the address matched — no account enumeration.
    # The match path is deferred to a background task (AFTER the response is sent) so
    # that (a) the blocking 20s-timeout SMTP send never stalls the single-worker event
    # loop, and (b) match and miss return with identical latency, closing the timing
    # oracle that would otherwise leak whether the address is the owner's.
    background = None
    if mailer.configured() and email.strip().lower() == (tenant["owner_email"] or "").lower():
        token = make_password_reset_token(tenant)
        link = tenant_url(tenant["slug"], f"/admin/reset?token={quote(token)}")
        owner_email = tenant["owner_email"]
        studio_name = tenant["studio_name"]
        slug = tenant["slug"]

        def _send_reset() -> None:
            try:
                mailer.send(
                    owner_email,
                    f"Reset your {studio_name} password",
                    "Someone (hopefully you) asked to reset the admin password for "
                    f"{studio_name}.\n\n"
                    f"Reset it here (link valid for 2 hours):\n{link}\n\n"
                    "If this wasn't you, ignore this email — the password is unchanged.",
                )
            except Exception:
                log.exception("password reset email failed for %s", slug)

        background = BackgroundTask(_send_reset)
    return templates.TemplateResponse(
        request,
        "admin/forgot.html",
        {"sent": True, "email_on": mailer.configured()},
        background=background,
    )


@router.get("/admin/reset", response_class=HTMLResponse)
async def reset_password_form(request: Request, token: str = ""):
    host_tenant = current_tenant()
    if not host_tenant:
        raise HTTPException(status_code=404)
    tenant = redeem_password_reset_token(token)
    valid = tenant is not None and tenant["id"] == host_tenant["id"]
    return templates.TemplateResponse(
        request, "admin/reset.html", {"token": token, "valid": valid, "error": None}
    )


@router.post("/admin/reset")
async def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    host_tenant = current_tenant()
    if not host_tenant:
        raise HTTPException(status_code=404)
    tenant = redeem_password_reset_token(token)
    if not tenant or tenant["id"] != host_tenant["id"]:
        return templates.TemplateResponse(
            request,
            "admin/reset.html",
            {
                "token": token,
                "valid": False,
                "error": "This reset link is invalid or has expired. Request a new one.",
            },
            status_code=400,
        )
    error = None
    if len(password) < 8:
        error = "Use at least 8 characters."
    elif password != password_confirm:
        error = "Passwords don't match."
    if error:
        return templates.TemplateResponse(
            request,
            "admin/reset.html",
            {"token": token, "valid": True, "error": error},
            status_code=400,
        )
    set_tenant_password(tenant["id"], password)
    log.info("tenant %s admin password reset via emailed link", tenant["slug"])
    return RedirectResponse("/admin/login?reset=1", status_code=303)


@router.get("/admin/export-studio")
async def export_studio(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    # The zip build is minutes of blocking sqlite-backup + compression for a
    # media-heavy studio; on the single-worker event loop that would stall every
    # tenant, so run it off-thread.
    tmp_zip = await run_in_threadpool(build_studio_export, tenant)
    log.info("tenant %s exported their studio archive", tenant["slug"])
    return FileResponse(
        str(tmp_zip),
        filename=f"{tenant['slug']}-studio-export.zip",
        media_type="application/zip",
        background=BackgroundTask(lambda: tmp_zip.unlink(missing_ok=True)),
    )


@router.post("/admin/delete-studio")
async def delete_studio(
    request: Request,
    confirm_slug: str = Form(...),
    password: str = Form(...),
    reason: str = Form(""),
):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    if confirm_slug.strip().lower() != tenant["slug"]:
        return RedirectResponse("/admin/billing?delete_error=slug", status_code=303)
    if not security.check_admin_password(password):
        return RedirectResponse("/admin/billing?delete_error=password", status_code=303)
    if reason.strip():
        # Exit note (Batch C4): the single most valuable feedback a beta produces —
        # why someone left — used to evaporate with the studio. Recorded BEFORE the
        # tombstone (the tenants row survives deletion, so the feedback join holds),
        # landing in the operator console's feedback panel like any other note.
        record_tenant_feedback(tenant["id"], "studio-delete", reason)
        from . import alerts  # lazy: alerts→features would cycle at import time

        preview = reason.strip()[:300]
        alerts.notify(
            f"Studio deleted: {tenant['studio_name']} ({tenant['slug']}) — why: {preview}"
        )
    delete_tenant_studio(tenant)
    resp = RedirectResponse(platform_url("/pricing?deleted=1"), status_code=303)
    security.delete_session_cookie(resp, security.ADMIN_COOKIE)
    return resp


def create_billing_portal_url(tenant: dict | None, return_url: str) -> str:
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="stripe is not configured")
    if not tenant or not tenant.get("stripe_customer_id"):
        raise HTTPException(status_code=503, detail="billing portal not available yet")
    session = _stripe().billing_portal.Session.create(
        api_key=config.STRIPE_SECRET_KEY,
        customer=tenant["stripe_customer_id"],
        return_url=return_url,
    )
    return session.url


def require_platform_admin(request: Request) -> None:
    security.require_admin(request)
    if current_tenant():
        raise HTTPException(status_code=404)


@router.get("/admin/saas", response_class=HTMLResponse)
async def operator_console(request: Request):
    require_platform_admin(request)
    from . import saas_preflight

    overview = operator_tenant_overview()
    preflight = saas_preflight.check_readiness(write_probes=False)

    return templates.TemplateResponse(
        request,
        "admin/saas_operator.html",
        {
            "overview": overview,
            "preflight": preflight,
            "launch": operator_launch_checklist(overview, preflight),
            "trial_nudges": operator_trial_nudges(overview),
            "feedback": recent_tenant_feedback(30, status="new"),
            "waitlist": waitlist_entries(50),
            "cancel_failures": departed_needs_cancel(),
            "root_domain": _root_domain(),
            "platform_url": platform_url("/pricing"),
            "price_cents": config.SAAS_PRICE_CENTS,
            # Batch D3: which mode is production actually in? The flip is one env
            # var (ADR 0053) — this makes its current state impossible to misread.
            "invite_gate_armed": bool(config.SAAS_INVITE_CODE),
        },
    )


@router.get("/admin/saas/export.csv")
async def operator_tenants_export(request: Request):
    require_platform_admin(request)
    return PlainTextResponse(
        operator_tenant_export_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mise_hosted_tenants.csv"'},
    )


@router.get("/admin/saas/waitlist.csv")
async def operator_waitlist_export(request: Request):
    require_platform_admin(request)
    return PlainTextResponse(
        waitlist_export_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mise_waitlist.csv"'},
    )


@router.post("/admin/saas/{tenant_id}/billing")
async def operator_billing_status(request: Request, tenant_id: int, plan_status: str = Form(...)):
    require_platform_admin(request)
    try:
        operator_update_tenant_status(tenant_id, plan_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/admin/saas?billing=1", status_code=303)


NOTES_MAX_CHARS = 4000


@router.post("/admin/saas/{tenant_id}/notes")
async def operator_tenant_notes(request: Request, tenant_id: int, notes: str = Form("")):
    """Operator-only per-studio notes (Batch A4) — where emailed/DM'd feedback and
    support context get recorded against the tenant. Empty clears."""
    require_platform_admin(request)
    with control_connect() as con:
        cur = con.execute(
            "UPDATE tenants SET notes=? WHERE id=?",
            (notes.strip()[:NOTES_MAX_CHARS] or None, tenant_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404)
    return RedirectResponse("/admin/saas#tenants", status_code=303)


@router.post("/admin/saas/feedback/{feedback_id}/done")
async def operator_feedback_done(request: Request, feedback_id: int):
    """Triage a feedback note (Batch D2): done leaves the console queue, not the DB.

    Once real beta feedback flows, an append-only panel stops being a queue and
    starts being a guilt pile. 'done' is one-way and never deletes — C4 exit
    reasons and shipped requests keep their archive value (the weekly digest
    still counts a week's notes regardless of triage).
    """
    require_platform_admin(request)
    with control_connect() as con:
        cur = con.execute("UPDATE tenant_feedback SET status='done' WHERE id=?", (feedback_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404)
    return RedirectResponse("/admin/saas#feedback", status_code=303)


@router.post("/admin/saas/{tenant_id}/cancel-resolved")
async def operator_cancel_resolved(request: Request, tenant_id: int):
    """Clear a failed-cancel flag once the operator has cancelled in Stripe by hand.

    The stamp is a follow-up reminder, not a state machine — this just dismisses it
    from the console after the manual cancel is done."""
    require_platform_admin(request)
    with control_connect() as con:
        cur = con.execute(
            "UPDATE tenants SET cancel_failed_at=NULL WHERE id=? AND cancel_failed_at IS NOT NULL",
            (tenant_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404)
    return RedirectResponse("/admin/saas#cancel-failures", status_code=303)


TRIAL_EXTEND_MAX_DAYS = 30


@router.post("/admin/saas/{tenant_id}/extend-trial")
async def operator_extend_trial(request: Request, tenant_id: int, days: int = Form(7)):
    """Give a promising trial more runway (Batch C3) — the audit's gap: the only
    recovery for an expired trial was immediate payment. Trialing tenants only;
    extends from now or the current end, whichever is later. Clears the trial-
    reminder and win-back stamps so the lifecycle emails work for the NEW window,
    and appends an audit line to the tenant's notes."""
    require_platform_admin(request)
    days = max(1, min(int(days), TRIAL_EXTEND_MAX_DAYS))
    with control_connect() as con:
        row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        if row is None or row["deleted_at"]:
            raise HTTPException(status_code=404)
        tenant = dict(row)
        if tenant["plan_status"] != "trialing":
            raise HTTPException(status_code=400, detail="only trialing studios can be extended")
        now = _now()
        current_end = _parse_iso(tenant.get("trial_ends_at"))
        base = current_end if current_end and current_end > now else now
        new_end = base + timedelta(days=days)
        stamp = f"[{now.date().isoformat()}] trial extended {days}d by operator"
        notes = f"{tenant['notes']}\n{stamp}" if tenant.get("notes") else stamp
        con.execute(
            """UPDATE tenants SET trial_ends_at=?, trial_reminder_sent_at=NULL,
                  winback_sent_at=NULL, notes=? WHERE id=?""",
            (_iso(new_end), notes[:NOTES_MAX_CHARS], tenant_id),
        )
    log.info("trial extended %dd for tenant %s", days, tenant["slug"])
    return RedirectResponse("/admin/saas#tenants", status_code=303)


@router.post("/admin/saas/{tenant_id}/domain/verify")
async def operator_verify_domain(request: Request, tenant_id: int):
    require_platform_admin(request)
    try:
        operator_set_domain_verified(tenant_id, verified=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/admin/saas?domain=verified", status_code=303)


@router.post("/admin/saas/{tenant_id}/domain/reset")
async def operator_reset_domain(request: Request, tenant_id: int):
    require_platform_admin(request)
    try:
        operator_set_domain_verified(tenant_id, verified=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/admin/saas?domain=reset", status_code=303)


def _account_context(tenant: dict, *, error: str | None = None, saved: bool = False) -> dict:
    return {
        "tenant": tenant,
        "error": error,
        "saved": saved,
        "root_domain": _root_domain(),
        "payments": _payments_status(tenant),
        "client_webhook_url": tenant_url(tenant["slug"], "/webhooks/stripe"),
    }


@router.get("/admin/account", response_class=HTMLResponse)
async def account(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    ctx = _account_context(tenant, saved=request.query_params.get("saved") == "1")
    ctx["payments_saved"] = request.query_params.get("payments") == "1"
    ctx["payments_off"] = request.query_params.get("payments_off") == "1"
    return templates.TemplateResponse(request, "admin/saas_account.html", ctx)


@router.post("/admin/account", response_class=HTMLResponse)
async def update_account(
    request: Request,
    studio_name: str = Form(...),
    owner_email: str = Form(...),
    custom_domain: str = Form(""),
    brand_accent: str = Form(_DEFAULT_BRAND_ACCENT),
):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    try:
        update_tenant_account(
            tenant["id"],
            studio_name=studio_name,
            owner_email=owner_email,
            custom_domain=custom_domain,
            brand_accent=brand_accent,
        )
    except ValueError as exc:
        values = dict(tenant)
        values.update(
            {
                "studio_name": studio_name,
                "owner_email": owner_email,
                "custom_domain": custom_domain,
                "brand_accent": brand_accent,
            }
        )
        return templates.TemplateResponse(
            request,
            "admin/saas_account.html",
            _account_context(values, error=str(exc)),
            status_code=400,
        )
    return RedirectResponse("/admin/account?saved=1", status_code=303)


@router.post("/admin/account/payments", response_class=HTMLResponse)
async def update_account_payments(
    request: Request,
    stripe_secret_key: str = Form(""),
    stripe_webhook_secret: str = Form(""),
):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    secret_key = stripe_secret_key.strip()
    webhook_secret = stripe_webhook_secret.strip()
    error = None
    if not secret_key.startswith(("sk_", "rk_")):
        error = "That doesn't look like a Stripe secret key (they start with sk_ or rk_)."
    elif not webhook_secret.startswith("whsec_"):
        # The webhook is how Mise marks an invoice paid — without it a client's
        # successful charge would never be recorded, so it is required, not optional.
        error = (
            "The webhook signing secret is required (starts with whsec_) — "
            "it's how Mise records your client's payment against the invoice."
        )
    if error is None:
        error = await run_in_threadpool(_verify_stripe_secret_key, secret_key)
    if error:
        return templates.TemplateResponse(
            request,
            "admin/saas_account.html",
            _account_context(tenant, error=error),
            status_code=400,
        )
    set_tenant_client_stripe(tenant["id"], secret_key, webhook_secret)
    mode = "live" if secret_key.startswith(("sk_live_", "rk_live_")) else "test"
    log.info("tenant %s connected client Stripe (%s mode)", tenant["slug"], mode)
    return RedirectResponse("/admin/account?payments=1", status_code=303)


@router.post("/admin/account/payments/disconnect")
async def disconnect_account_payments(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    set_tenant_client_stripe(tenant["id"], "", "")
    log.info("tenant %s disconnected client Stripe (payments fail-closed off)", tenant["slug"])
    return RedirectResponse("/admin/account?payments_off=1", status_code=303)


@router.get("/admin/onboarding", response_class=HTMLResponse)
async def onboarding(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    from . import onboarding as onboarding_state
    from . import preset_packs

    seeded = request.query_params.get("seeded")
    installed = request.query_params.get("pack")
    setup = onboarding_state.setup_status()
    return templates.TemplateResponse(
        request,
        "admin/onboarding.html",
        {
            "tenant": tenant,
            "seeded": seeded,
            "installed": installed,
            "setup": setup,
            "launch": onboarding_state.launch_plan(setup),
            "packs": preset_packs.PRESET_PACKS,
        },
    )


@router.post("/admin/onboarding/pack")
async def install_onboarding_pack(request: Request, pack: str = Form(...)):
    security.require_admin(request)
    from . import preset_packs

    if pack not in preset_packs.PRESET_PACKS:
        raise HTTPException(status_code=400, detail="bad preset pack")
    preset_packs.install_pack(pack)
    return RedirectResponse(f"/admin/onboarding?pack={pack}", status_code=303)


@router.post("/admin/onboarding/demo")
async def seed_demo(request: Request, preset: str = Form(...)):
    security.require_admin(request)
    from . import saas_demo

    result = saas_demo.seed_preset(preset)
    suffix = result["preset"]
    return RedirectResponse(f"/admin/onboarding?seeded={suffix}", status_code=303)


@router.get("/admin/help", response_class=HTMLResponse)
async def tenant_help(request: Request):
    """Help & feedback for the logged-in studio owner — the first support surface
    reachable from INSIDE the admin (everything else lives on the public root host)."""
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/saas_help.html",
        {
            "tenant": tenant,
            "sent": request.query_params.get("sent"),
            "support_email": config.SAAS_SUPPORT_EMAIL,
            "support_url": platform_url("/support"),
        },
    )


@router.post("/admin/help/feedback")
async def tenant_feedback_submit(request: Request, message: str = Form(...), page: str = Form("")):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    if not message.strip():
        return RedirectResponse("/admin/help?sent=", status_code=303)
    record_tenant_feedback(tenant["id"], page, message)
    # Ids only in the log (content is user-authored); the content goes to the
    # operator's own Telegram, which is the point — a caller-deduped business
    # event, fire-and-forget, never blocks the response (see alerts.notify).
    log.info("tenant feedback recorded%s", security.tenant_log_label())
    from . import alerts  # lazy: alerts→features would cycle at import time

    preview = message.strip()[:300]
    suffix = "…" if len(message.strip()) > 300 else ""
    alerts.notify(
        f"Beta feedback from {tenant['studio_name']} ({tenant['slug']}): {preview}{suffix}"
    )
    return RedirectResponse("/admin/help?sent=1", status_code=303)
