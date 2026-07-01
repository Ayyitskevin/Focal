"""Hosted MicroSaaS control plane for Mise.

Product data remains in the existing Mise schema. This module adds a small
control database for tenants and switches requests/jobs into a tenant-specific
SQLite database and file-storage root.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config, db, passwords, security, urls
from .render import templates

log = logging.getLogger("mise.saas")
router = APIRouter()

_TENANT_CTX: ContextVar[dict | None] = ContextVar("mise_tenant", default=None)
_MIGRATED_TENANT_DBS: set[str] = set()
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
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

    return stripe


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
        con.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_custom_domain
               ON tenants(custom_domain) WHERE custom_domain IS NOT NULL"""
        )


def _ensure_column(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


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


def operator_tenant_overview() -> dict:
    rows = []
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
        billing = tenant_billing_context(tenant)
        domain_state = "none"
        if tenant.get("custom_domain"):
            domain_state = "verified" if tenant.get("custom_domain_verified_at") else "pending"
        counts["total"] += 1
        if tenant["plan_status"] == "trialing":
            counts["trialing"] += 1
            counts["trial_pipeline_cents"] += config.SAAS_PRICE_CENTS
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
                "domain_state": domain_state,
                "tenant_url": tenant_url(tenant["slug"], "/admin/login"),
                "account_url": tenant_url(tenant["slug"], "/admin/account"),
                "data_path": str(tenant_data_path(tenant["slug"])),
                "db_exists": tenant_db_path(tenant["slug"]).exists(),
            }
        )
    counts["support_queue"] = counts["attention"] + counts["custom_domains_pending"]
    return {"counts": counts, "rows": rows}


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


def create_tenant(slug: str, studio_name: str, owner_email: str, password: str) -> dict:
    slug = validate_slug(slug)
    studio_name = (studio_name or "").strip()
    owner_email = (owner_email or "").strip().lower()
    if not studio_name:
        raise ValueError("Studio name is required.")
    if "@" not in owner_email:
        raise ValueError("A valid email is required.")
    if len(password or "") < 8:
        raise ValueError("Use at least 8 characters for the admin password.")
    started = _now()
    ends = started + timedelta(days=config.SAAS_TRIAL_DAYS)
    try:
        with control_connect() as con:
            cur = con.execute(
                """INSERT INTO tenants
                   (slug, studio_name, owner_email, admin_password_hash,
                    trial_started_at, trial_ends_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    slug,
                    studio_name,
                    owner_email,
                    passwords.hash_password(password),
                    _iso(started),
                    _iso(ends),
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
    with control_connect() as con:
        con.execute(f"UPDATE tenants SET {', '.join(updates)} WHERE id=?", params)


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


def _platform_path(path: str) -> bool:
    return (
        path in {"/", "/pricing", "/demo", "/start-trial", "/healthz", "/favicon.ico"}
        or path.startswith("/static/")
        or path in {"/admin/login", "/admin/logout", "/admin/saas"}
        or path.startswith("/admin/saas/")
        or path in {"/webhooks/stripe", "/webhooks/stripe/saas"}
    )


def _billing_allowed_path(path: str) -> bool:
    return (
        path.startswith("/admin/billing")
        or path.startswith("/admin/account")
        or path in {"/admin/login", "/admin/logout", "/healthz"}
        or path.startswith("/static/")
        or path in {"/webhooks/stripe", "/webhooks/stripe/saas"}
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
    if not tenant:
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
            return JSONResponse({"detail": "subscription required"}, status_code=402)
        return await call_next(request)


def _pricing_context(
    error: str | None = None, values: dict | None = None, *, path: str = "/pricing"
) -> dict:
    return {
        "error": error,
        "values": values or {},
        "price_cents": config.SAAS_PRICE_CENTS,
        "trial_days": config.SAAS_TRIAL_DAYS,
        "root_domain": _root_domain(),
        "canonical_url": platform_url(path),
        "home_url": platform_url("/"),
        "pricing_url": platform_url("/pricing"),
        "demo_url": platform_url("/demo"),
    }


@router.get("/", response_class=HTMLResponse)
async def saas_home(request: Request):
    return templates.TemplateResponse(request, "saas/home.html", _pricing_context(path="/"))


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    return templates.TemplateResponse(
        request, "saas/pricing.html", _pricing_context(path="/pricing")
    )


@router.get("/demo", response_class=HTMLResponse)
async def demo(request: Request):
    return templates.TemplateResponse(request, "saas/demo.html", _pricing_context(path="/demo"))


@router.post("/start-trial")
async def start_trial(
    request: Request,
    studio_name: str = Form(...),
    owner_email: str = Form(...),
    slug: str = Form(...),
    password: str = Form(...),
):
    values = {"studio_name": studio_name, "owner_email": owner_email, "slug": slug}
    try:
        tenant = create_tenant(slug, studio_name, owner_email, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "saas/pricing.html",
            _pricing_context(str(exc), values, path="/pricing"),
            status_code=400,
        )

    success_url = tenant_url(tenant["slug"], "/admin/login?trial=1")
    cancel_url = platform_url("/pricing")
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
        stripe_mod = _stripe()
        session = stripe_mod.checkout.Session.create(
            api_key=config.STRIPE_SECRET_KEY,
            mode="subscription",
            customer_email=tenant["owner_email"],
            line_items=[{"price": config.SAAS_STRIPE_PRICE_ID, "quantity": 1}],
            metadata={"tenant_id": str(tenant["id"]), "slug": tenant["slug"]},
            subscription_data={
                "trial_period_days": config.SAAS_TRIAL_DAYS,
                "metadata": {"tenant_id": str(tenant["id"]), "slug": tenant["slug"]},
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
        log.info("tenant %s checkout session %s created", tenant["slug"], session.id)
        return RedirectResponse(session.url, status_code=303)

    return RedirectResponse(success_url, status_code=303)


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

    with control_connect() as con:
        try:
            con.execute(
                "INSERT INTO saas_events (id, type) VALUES (?,?)", (event["id"], event["type"])
            )
        except sqlite3.IntegrityError:
            return {"ok": True, "duplicate": True}

    obj = event["data"]["object"]
    event_type = event["type"]
    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        tenant_id = int(metadata.get("tenant_id") or 0)
        if tenant_id:
            update_tenant_billing(
                tenant_id,
                plan_status="trialing",
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
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
            )
    return {"ok": True, "type": event_type}


@router.get("/admin/billing", response_class=HTMLResponse)
async def billing(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/saas_billing.html",
        {
            "tenant": tenant,
            "price_cents": config.SAAS_PRICE_CENTS,
            "access_ok": tenant_has_access(tenant),
            "billing_status": tenant_billing_context(tenant),
        },
    )


@router.post("/admin/billing/portal")
async def billing_portal(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    return_url = f"{urls.public_base_url(request)}/admin/billing"
    portal_url = create_billing_portal_url(tenant, return_url)
    return RedirectResponse(portal_url, status_code=303)


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
            "root_domain": _root_domain(),
            "platform_url": platform_url("/pricing"),
            "price_cents": config.SAAS_PRICE_CENTS,
        },
    )


@router.post("/admin/saas/{tenant_id}/billing")
async def operator_billing_status(request: Request, tenant_id: int, plan_status: str = Form(...)):
    require_platform_admin(request)
    try:
        operator_update_tenant_status(tenant_id, plan_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/admin/saas?billing=1", status_code=303)


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


@router.get("/admin/account", response_class=HTMLResponse)
async def account(request: Request):
    security.require_admin(request)
    tenant = current_tenant()
    if not tenant:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/saas_account.html",
        {
            "tenant": tenant,
            "error": None,
            "saved": request.query_params.get("saved") == "1",
            "root_domain": _root_domain(),
        },
    )


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
            {
                "tenant": values,
                "error": str(exc),
                "saved": False,
                "root_domain": _root_domain(),
            },
            status_code=400,
        )
    return RedirectResponse("/admin/account?saved=1", status_code=303)


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
