"""Mise's scheduler — one in-process daemon thread for every recurring sweep.

It wakes on an interval and runs: due recurring plans (DRAFT invoices only —
the money path never sends or charges itself; Kevin still clicks Send, Stripe
still collects), the operational reminder sweeps (booking/gallery/contract/
retainer/post-shoot — owner- and client-consented reminder mail per their own
gates), and in hosted mode the platform lifecycle mail (trial reminder,
win-back, dunning, weekly operator digest — all owner-facing, all one-shot).
It is deliberately the simplest thing that works: no cron, no run_at column,
no second process. Every sweep is idempotent (period claims, one-shot stamps),
so the loop can fire as often as it likes.

The recurring thread waits one interval before its first monthly/reminder sweep.
A separate durable-booking worker drains once on boot and then at its short poll
interval: booking delivery is interactive, leased, and must recover promptly
after a process crash without accelerating the unrelated recurring money path.
"""

import logging
import threading

from . import (
    booking_reminders,
    booking_workflow,
    config,
    contract_reminders,
    gallery_reminders,
    mobile_idempotency,
    ops_monitor,
    postshoot_reminders,
    retainer_reminders,
)
from .admin import recurring

log = logging.getLogger("mise.scheduler")

_stop = threading.Event()
_thread: threading.Thread | None = None
_booking_stop = threading.Event()
_booking_wake = threading.Event()
_booking_thread: threading.Thread | None = None


def _revalidated_retained_tenant(listed_tenant: dict) -> dict | None:
    """Resolve one control-plane snapshot row by immutable identity."""
    from . import saas

    tenant_id = listed_tenant.get("id")
    listed_slug = listed_tenant.get("slug")
    if tenant_id is None or not listed_slug:
        return None
    tenant = saas.tenant_by_id(tenant_id)
    if (
        not tenant
        or tenant.get("id") != tenant_id
        or tenant.get("slug") != listed_slug
        or tenant.get("deleted_at")
    ):
        return None
    return tenant


def _loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(config.RECURRING_TICK_SECONDS):
        if config.SAAS_MODE:
            from . import saas

            try:
                # Platform-level lifecycle mail (ADR 0060) — outside tenant_runtime
                # on purpose: it must carry platform identity, not a studio's.
                saas.trial_reminder_sweep()
            except Exception:
                log.exception("trial reminder sweep failed")
            try:
                saas.winback_sweep()
            except Exception:
                log.exception("win-back sweep failed")
            try:
                saas.dunning_sweep()
            except Exception:
                log.exception("dunning sweep failed")
            try:
                # The one sweep that mails the OPERATOR, not a tenant (Batch D1).
                saas.weekly_digest_sweep()
            except Exception:
                log.exception("weekly digest sweep failed")
            _prune_hosted_mobile_idempotency()
            for listed_tenant in saas.list_tenants(billable_only=True):
                listed_slug = listed_tenant.get("slug")
                try:
                    tenant = _revalidated_retained_tenant(listed_tenant)
                    if not tenant:
                        continue
                    with saas.tenant_runtime(tenant):
                        _sweep_once()
                except saas.TenantStorageUnavailable as exc:
                    saas.report_tenant_storage_unavailable(exc, operation="scheduled tenant sweep")
                except Exception:
                    log.exception("tenant scheduler sweep failed: %s", listed_slug)
            continue
        _prune_mobile_idempotency()
        _sweep_once()


def _booking_loop(
    stop_event: threading.Event,
    wake_event: threading.Event | None = None,
) -> None:
    wake_event = wake_event or _booking_wake
    _safe_sweep_booking_workflows()
    interval = max(1, config.BOOKING_WORKFLOW_POLL_SECONDS)
    while not stop_event.is_set():
        wake_event.wait(interval)
        wake_event.clear()
        if stop_event.is_set():
            break
        _safe_sweep_booking_workflows()


def _safe_sweep_booking_workflows() -> None:
    """Keep the durable worker alive when an outer sweep dependency fails."""
    try:
        _sweep_booking_workflows()
    except Exception:
        # In particular, the hosted control DB can fail before the per-tenant
        # exception boundary while list_tenants() is building the work list.
        log.exception("booking workflow outer sweep failed")


def wake_booking_workflows() -> None:
    """Wake the durable worker without doing provider I/O in a request thread."""
    _booking_wake.set()


def _sweep_booking_workflows() -> None:
    if not booking_workflow.available():
        return
    if not config.SAAS_MODE:
        try:
            booking_workflow.sweep()
        except Exception:
            log.exception("booking workflow sweep failed")
        return

    from . import saas

    # Delivery recovery is a retention obligation, not a billing benefit. Include
    # cancelled/unpaid retained tenants; missing storage produces an incident
    # signal, while deleted tenant tombstones are never entered or recreated.
    for listed_tenant in saas.list_tenants():
        tenant_id = listed_tenant.get("id")
        listed_slug = listed_tenant.get("slug")

        # The list is only a snapshot. Re-fetch by immutable identity immediately
        # before entering the existing-only runtime so a deleted tenant, renamed
        # row, or reused slug cannot be processed from stale state. Do not preflight
        # the path here: a retained tenant with missing storage must produce the
        # recovery signal, and SQLite mode=rw prevents replacement creation.
        try:
            tenant = _revalidated_retained_tenant(listed_tenant)
            if not tenant:
                continue
        except Exception:
            log.exception(
                "tenant booking workflow revalidation failed: id=%s slug=%s",
                tenant_id,
                listed_slug,
            )
            continue
        try:
            with saas.tenant_runtime_existing(tenant):
                booking_workflow.sweep()
        except saas.TenantStorageUnavailable as exc:
            saas.report_tenant_storage_unavailable(exc, operation="booking workflow recovery")
        except Exception:
            log.exception("tenant booking workflow sweep failed: %s", listed_slug)


def _prune_mobile_idempotency() -> None:
    try:
        pruned = mobile_idempotency.prune_expired()
        if pruned:
            log.info("pruned %s expired mobile idempotency receipt(s)", pruned)
    except Exception:
        log.exception("mobile idempotency receipt cleanup failed")


def _prune_hosted_mobile_idempotency() -> None:
    """Prune every retained tenant DB without running billable-only mail sweeps.

    Deleted tenant rows are tombstones whose data has moved to ``.trash``; entering
    their runtime would accidentally recreate an empty live-looking directory.
    Likewise, cleanup never provisions a missing tenant DB merely to delete rows;
    retained missing storage produces the same throttled recovery signal.
    """
    from . import saas

    for listed_tenant in saas.list_tenants():
        listed_slug = listed_tenant.get("slug")
        try:
            tenant = _revalidated_retained_tenant(listed_tenant)
            if not tenant:
                continue
            with saas.tenant_runtime(tenant):
                _prune_mobile_idempotency()
        except saas.TenantStorageUnavailable as exc:
            saas.report_tenant_storage_unavailable(exc, operation="mobile idempotency cleanup")
        except Exception:
            log.exception("tenant mobile idempotency cleanup failed: %s", listed_slug)


def _sweep_once() -> None:
    try:
        recurring.run_due_plans()
    except Exception:
        log.exception("recurring sweep failed")
    try:
        booking_reminders.sweep()
    except Exception:
        log.exception("booking reminder sweep failed")
    try:
        gallery_reminders.sweep()
    except Exception:
        log.exception("gallery reminder sweep failed")
    try:
        contract_reminders.sweep()
    except Exception:
        log.exception("contract reminder sweep failed")
    try:
        retainer_reminders.sweep()
    except Exception:
        log.exception("retainer renewal reminder sweep failed")
    try:
        ops_monitor.sweep()
    except Exception:
        log.exception("ops monitor sweep failed")
    try:
        postshoot_reminders.sweep()
    except Exception:
        log.exception("post-shoot reminder sweep failed")


def start() -> None:
    global _thread, _stop, _booking_thread, _booking_stop, _booking_wake
    # A FRESH event per generation: stop() joins with a 2s timeout, so a sweep
    # mid-SMTP can outlive it — clearing the shared event would un-stop that
    # orphan and leave two loops sweeping side by side after a restart.
    _stop = threading.Event()
    _thread = threading.Thread(target=_loop, args=(_stop,), name="mise-recurring", daemon=True)
    _thread.start()
    _booking_stop = threading.Event()
    _booking_wake = threading.Event()
    _booking_thread = threading.Thread(
        target=_booking_loop,
        args=(_booking_stop, _booking_wake),
        name="mise-booking-workflow",
        daemon=True,
    )
    _booking_thread.start()
    log.info(
        "scheduler up (recurring=%ss; booking workflow=%ss; money path stays drafts-only)",
        config.RECURRING_TICK_SECONDS,
        max(1, config.BOOKING_WORKFLOW_POLL_SECONDS),
    )


def stop() -> None:
    global _thread, _booking_thread
    _stop.set()
    _booking_stop.set()
    _booking_wake.set()
    if _thread:
        _thread.join(timeout=2)
        _thread = None
    if _booking_thread:
        _booking_thread.join(timeout=2)
        _booking_thread = None
