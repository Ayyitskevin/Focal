"""Central feature flags.

All "enabled" checks mean the feature is fully armed (keys present).
Empty/missing config values keep the feature dormant (graceful 503s or hidden UI).

Existing per-module is_enabled/configured() are kept for now but callers
should prefer features.* for consistency. Modules can delegate to here.
"""

import urllib.parse

from . import config


def client_stripe_secret_key() -> str:
    """Stripe secret used to CHARGE a client invoice.

    Single-tenant: the operator's own key (unchanged).
    Hosted (SAAS_MODE): the *tenant's own* key — never the platform operator's —
    so a studio's client can only ever pay into that studio's own Stripe account.
    Fail-closed: with no tenant in context, or a tenant that has not connected its
    own Stripe, this returns "" and online payment stays off. The operator key is
    NEVER used to charge a studio's client (that would route a customer's payment
    into the host's account — the money-boundary violation this closes).
    """
    if config.SAAS_MODE:
        from . import saas

        tenant = saas.current_tenant()
        if not tenant:
            return ""
        return (tenant.get("client_stripe_secret_key") or "").strip()
    return config.STRIPE_SECRET_KEY


def client_stripe_webhook_secret() -> str:
    """Webhook secret for the client-invoice payment webhook.

    Mirrors ``client_stripe_secret_key``: the operator's global secret only in
    single-tenant mode; per-tenant (fail-closed to "") in hosted mode. The
    platform-subscription webhook is separate (``SAAS_STRIPE_WEBHOOK_SECRET``)
    and unaffected.
    """
    if config.SAAS_MODE:
        from . import saas

        tenant = saas.current_tenant()
        if not tenant:
            return ""
        return (tenant.get("client_stripe_webhook_secret") or "").strip()
    return config.STRIPE_WEBHOOK_SECRET


def client_stripe_webhook_secrets() -> list[str]:
    """Accept-list for verifying the client-payment webhook: current secret first,
    then the previous one (ADR 0054 rotation grace).

    A checkout link stays payable for ~24h and Stripe retries deliveries for days;
    without the grace, rotating or disconnecting Stripe mid-flight would make an
    already-paid session unverifiable forever — client charged, invoice never
    marked paid. Single-tenant mode has no rotation store and returns at most the
    one global secret (unchanged behavior).
    """
    secrets = [client_stripe_webhook_secret()]
    if config.SAAS_MODE:
        from . import saas

        tenant = saas.current_tenant()
        if tenant:
            secrets.append((tenant.get("client_stripe_webhook_secret_prev") or "").strip())
    return [s for s in secrets if s]


def stripe_enabled() -> bool:
    return bool(client_stripe_secret_key())


def stripe_webhook_enabled() -> bool:
    return bool(client_stripe_webhook_secrets())


def odysseus_caption_enabled() -> bool:
    raw = str(config.ODYSSEUS_CAPTION_URL or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        config.ODYSSEUS_CAPTION_TOKEN
        and parsed.scheme.lower() == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def content_provider_facade_enabled() -> bool:
    """Content-capability facade flag (default off — legacy paths stay production).

    Gates two provenance behaviors, both additive and ai_runs-only:
    - Phase 1: route caption drafting through the app/providers facade.
    - Phase 4: record Dionysus pack-draft outcomes to the ai_runs ledger (platekit).
    """
    return bool(config.PROVIDER_FACADE_CONTENT)


def vision_shadow_enabled() -> bool:
    """Phase 2: shadow a completed Argus analysis with a registered vision challenger,
    recording the comparison to ai_runs. Default off; also inert without a challenger."""
    return bool(config.VISION_SHADOW)


def gmail_enabled() -> bool:
    return bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)


def telegram_enabled() -> bool:
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def operator_context() -> bool:
    """True when global-credential integrations may act (ADR 0055).

    Notion, Google Calendar, and SMS are the OPERATOR's personal accounts. In hosted
    mode a tenant context must never ride them — a studio's bookings mirroring into
    the operator's Notion/Calendar, or a studio's texts sending from the operator's
    number, is a cross-tenant data leak. Fail-closed, same doctrine as the client
    Stripe gate (ADR 0049). Single-tenant mode is always the operator's own context.
    """
    if config.SAAS_MODE:
        from . import saas

        return saas.current_tenant() is None
    return True


def sms_enabled() -> bool:
    """Quo / OpenPhone SMS."""
    return bool(config.QUO_API_KEY and config.QUO_NUMBER) and operator_context()


def google_calendar_enabled() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def reopen_notify_enabled() -> bool:
    return bool(config.REOPEN_NOTIFY_URL and config.REOPEN_NOTIFY_TOKEN)


def hermes_enabled() -> bool:
    return bool(config.HERMES_ARM_URL)


def shots_api_enabled() -> bool:
    return bool(config.SHOTS_TOKEN)


def notion_enabled() -> bool:
    return bool(config.NOTION_TOKEN) and operator_context()


def notion_bookings_enabled() -> bool:
    return bool(config.NOTION_TOKEN and config.NOTION_BOOKINGS_DB) and operator_context()


def notion_sessions_enabled() -> bool:
    return bool(config.NOTION_TOKEN and config.NOTION_SESSIONS_DB) and operator_context()


def plausible_enabled() -> bool:
    return bool(config.PLAUSIBLE_DOMAIN)


def demo_gallery_enabled() -> bool:
    return bool(config.DEMO_GALLERY_SLUG and config.DEMO_GALLERY_PIN)
