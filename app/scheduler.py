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

The thread WAITS one interval before its first sweep — there is no sweep-on-boot.
That keeps test lifespan cycles from generating anything, and in production it
just means a due monthly draft is caught up within one interval of a restart,
which is plenty for a monthly event.
"""

import logging
import threading

from . import (
    booking_reminders,
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
            for tenant in saas.list_tenants(billable_only=True):
                try:
                    with saas.tenant_runtime(tenant):
                        _sweep_once()
                except Exception:
                    log.exception("tenant scheduler sweep failed: %s", tenant["slug"])
            continue
        _prune_mobile_idempotency()
        _sweep_once()


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
    Likewise, cleanup never provisions a missing tenant DB merely to delete rows.
    """
    from . import saas

    for tenant in saas.list_tenants():
        if tenant.get("deleted_at") or not saas.tenant_db_path(tenant["slug"]).exists():
            continue
        try:
            with saas.tenant_runtime(tenant):
                _prune_mobile_idempotency()
        except Exception:
            log.exception("tenant mobile idempotency cleanup failed: %s", tenant["slug"])


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
    global _thread, _stop
    # A FRESH event per generation: stop() joins with a 2s timeout, so a sweep
    # mid-SMTP can outlive it — clearing the shared event would un-stop that
    # orphan and leave two loops sweeping side by side after a restart.
    _stop = threading.Event()
    _thread = threading.Thread(target=_loop, args=(_stop,), name="mise-recurring", daemon=True)
    _thread.start()
    log.info(
        "scheduler up (every %ss; money path stays drafts-only)", config.RECURRING_TICK_SECONDS
    )


def stop() -> None:
    global _thread
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
        _thread = None
