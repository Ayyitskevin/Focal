"""Central feature flags.

All "enabled" checks mean the feature is fully armed (keys present).
Empty/missing config values keep the feature dormant (graceful 503s or hidden UI).

Existing per-module is_enabled/configured() are kept for now but callers
should prefer features.* for consistency. Modules can delegate to here.
"""

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


def stripe_enabled() -> bool:
    return bool(client_stripe_secret_key())


def stripe_webhook_enabled() -> bool:
    return bool(client_stripe_webhook_secret())


def odysseus_caption_enabled() -> bool:
    return bool(config.ODYSSEUS_CAPTION_URL and config.ODYSSEUS_CAPTION_TOKEN)


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


def sms_enabled() -> bool:
    """Quo / OpenPhone SMS."""
    return bool(config.QUO_API_KEY and config.QUO_NUMBER)


def google_calendar_enabled() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def reopen_notify_enabled() -> bool:
    return bool(config.REOPEN_NOTIFY_URL and config.REOPEN_NOTIFY_TOKEN)


def hermes_enabled() -> bool:
    return bool(config.HERMES_ARM_URL)


def shots_api_enabled() -> bool:
    return bool(config.SHOTS_TOKEN)


def notion_enabled() -> bool:
    return bool(config.NOTION_TOKEN)


def notion_bookings_enabled() -> bool:
    return bool(config.NOTION_TOKEN and config.NOTION_BOOKINGS_DB)


def notion_sessions_enabled() -> bool:
    return bool(config.NOTION_TOKEN and config.NOTION_SESSIONS_DB)


def plausible_enabled() -> bool:
    return bool(config.PLAUSIBLE_DOMAIN)


def demo_gallery_enabled() -> bool:
    return bool(config.DEMO_GALLERY_SLUG and config.DEMO_GALLERY_PIN)
