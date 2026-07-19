"""Recurring cleanup covers retained hosted tenant DBs without recreating tombstones."""

import threading
from contextlib import contextmanager

import pytest

from app import booking_workflow, config, db, mobile_idempotency, saas, scheduler

pytestmark = pytest.mark.unit


def test_hosted_cleanup_includes_nonbillable_reports_missing_and_skips_tombstones(
    tmp_path, monkeypatch
):
    tenants = [
        {"id": 1, "slug": "active", "plan_status": "active", "deleted_at": None},
        {"id": 2, "slug": "canceled", "plan_status": "canceled", "deleted_at": None},
        {
            "id": 3,
            "slug": "deleted",
            "plan_status": "canceled",
            "deleted_at": "2026-07-13",
        },
        {"id": 4, "slug": "missing", "plan_status": "unpaid", "deleted_at": None},
        {"id": 5, "slug": "reused", "plan_status": "active", "deleted_at": None},
    ]
    current = {tenant["id"]: tenant for tenant in tenants}
    current[5] = {
        "id": 5,
        "slug": "reused-deleted-5-20260718123456",
        "plan_status": "canceled",
        "deleted_at": "2026-07-18",
    }
    for slug in ("active", "canceled", "deleted", "reused"):
        path = tmp_path / slug / "mise.db"
        path.parent.mkdir()
        path.touch()

    monkeypatch.setattr(saas, "list_tenants", lambda **_: tenants)
    monkeypatch.setattr(saas, "tenant_by_id", current.get)
    monkeypatch.setattr(saas, "tenant_db_path", lambda slug: tmp_path / slug / "mise.db")
    current: list[str] = []
    attempted: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        attempted.append(tenant["slug"])
        if tenant["slug"] == "missing":
            raise saas.TenantStorageUnavailable(tenant)
        current.append(tenant["slug"])
        try:
            yield tenant
        finally:
            current.pop()

    monkeypatch.setattr(saas, "tenant_runtime", tenant_runtime)
    reports: list[tuple[str, str]] = []
    monkeypatch.setattr(
        saas,
        "report_tenant_storage_unavailable",
        lambda error, *, operation, reference=None: reports.append((error.slug, operation)),
    )
    pruned: list[str] = []
    monkeypatch.setattr(
        mobile_idempotency,
        "prune_expired",
        lambda: pruned.append(current[-1]) or 0,
    )

    scheduler._prune_hosted_mobile_idempotency()

    assert pruned == ["active", "canceled"]
    assert attempted == ["active", "canceled", "missing"]
    assert reports == [("missing", "mobile idempotency cleanup")]
    assert not (tmp_path / "missing").exists()


def test_hosted_scheduler_reports_missing_storage_and_continues_healthy_tenants(monkeypatch):
    tenants = [
        {"id": 1, "slug": "missing", "deleted_at": None},
        {"id": 2, "slug": "healthy", "deleted_at": None},
        {"id": 3, "slug": "reused", "deleted_at": None},
    ]
    current_rows = {tenant["id"]: tenant for tenant in tenants}
    current_rows[3] = {
        "id": 3,
        "slug": "reused-deleted-3-20260718123456",
        "deleted_at": "2026-07-18",
    }
    current: list[str] = []
    attempted: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        attempted.append(tenant["slug"])
        if tenant["slug"] == "missing":
            raise saas.TenantStorageUnavailable(tenant)
        current.append(tenant["slug"])
        try:
            yield tenant
        finally:
            current.pop()

    class OneTick:
        def __init__(self):
            self.calls = 0

        def wait(self, _interval):
            self.calls += 1
            return self.calls > 1

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(saas, "list_tenants", lambda **_: tenants)
    monkeypatch.setattr(saas, "tenant_by_id", current_rows.get)
    monkeypatch.setattr(saas, "tenant_runtime", tenant_runtime)
    for name in (
        "trial_reminder_sweep",
        "winback_sweep",
        "dunning_sweep",
        "weekly_digest_sweep",
    ):
        monkeypatch.setattr(saas, name, lambda: None)
    monkeypatch.setattr(scheduler, "_prune_hosted_mobile_idempotency", lambda: None)
    swept: list[str] = []
    monkeypatch.setattr(scheduler, "_sweep_once", lambda: swept.append(current[-1]))
    reports: list[tuple[str, str]] = []
    monkeypatch.setattr(
        saas,
        "report_tenant_storage_unavailable",
        lambda error, *, operation, reference=None: reports.append((error.slug, operation)),
    )

    scheduler._loop(OneTick())

    assert attempted == ["missing", "healthy"]
    assert swept == ["healthy"]
    assert reports == [("missing", "scheduled tenant sweep")]


def test_booking_worker_drains_once_before_its_first_wait(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(scheduler, "_sweep_booking_workflows", lambda: calls.append("sweep"))
    stopped = threading.Event()
    stopped.set()

    scheduler._booking_loop(stopped)

    assert calls == ["sweep"]


def test_hosted_booking_recovery_includes_retained_nonbillable_tenants(
    tmp_path,
    monkeypatch,
):
    tenants = [
        {"id": 1, "slug": "active", "plan_status": "active", "deleted_at": None},
        {"id": 2, "slug": "canceled", "plan_status": "canceled", "deleted_at": None},
        {
            "id": 3,
            "slug": "deleted",
            "plan_status": "canceled",
            "deleted_at": "2026-07-13",
        },
        {"id": 4, "slug": "missing", "plan_status": "unpaid", "deleted_at": None},
    ]
    for slug in ("active", "canceled", "deleted"):
        path = tmp_path / slug / "mise.db"
        path.parent.mkdir()
        path.touch()

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(booking_workflow, "available", lambda: True)
    monkeypatch.setattr(saas, "list_tenants", lambda **_: tenants)
    monkeypatch.setattr(saas, "tenant_by_id", lambda tenant_id: tenants[tenant_id - 1])
    monkeypatch.setattr(saas, "tenant_db_path", lambda slug: tmp_path / slug / "mise.db")
    current: list[str] = []
    attempted: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        attempted.append(tenant["slug"])
        if tenant["slug"] == "missing":
            raise saas.TenantStorageUnavailable(tenant)
        current.append(tenant["slug"])
        try:
            yield tenant
        finally:
            current.pop()

    monkeypatch.setattr(saas, "tenant_runtime_existing", tenant_runtime)
    reports: list[tuple[str, str]] = []
    monkeypatch.setattr(
        saas,
        "report_tenant_storage_unavailable",
        lambda error, *, operation, reference=None: reports.append((error.slug, operation)),
    )
    swept: list[str] = []
    monkeypatch.setattr(booking_workflow, "sweep", lambda: swept.append(current[-1]) or 0)

    scheduler._sweep_booking_workflows()

    assert swept == ["active", "canceled"]
    assert attempted == ["active", "canceled", "missing"]
    assert reports == [("missing", "booking workflow recovery")]
    assert not (tmp_path / "missing").exists()


def test_booking_worker_survives_list_tenants_failure(caplog, monkeypatch):
    stopped = threading.Event()
    calls = 0

    def list_tenants(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("control database unavailable")
        stopped.set()
        return []

    class ImmediateWake:
        def wait(self, _interval):
            return True

        def clear(self):
            return None

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(booking_workflow, "available", lambda: True)
    monkeypatch.setattr(saas, "list_tenants", list_tenants)

    with caplog.at_level("ERROR", logger="mise.scheduler"):
        scheduler._booking_loop(stopped, ImmediateWake())

    assert calls == 2
    assert "booking workflow outer sweep failed" in caplog.text
    assert "control database unavailable" in caplog.text


def test_hosted_booking_recovery_revalidates_stale_tenant_rows(tmp_path, monkeypatch):
    listed = [
        {"id": 1, "slug": "old-slug", "deleted_at": None},
        {"id": 2, "slug": "deleted-now", "deleted_at": None},
        {"id": 3, "slug": "reused", "deleted_at": None},
        {"id": 4, "slug": "missing", "deleted_at": None},
    ]
    current = {
        1: {"id": 1, "slug": "renamed", "deleted_at": None},
        2: {"id": 2, "slug": "deleted-now", "deleted_at": "2026-07-13"},
        3: None,
        4: {"id": 4, "slug": "missing", "deleted_at": None},
    }
    for slug in ("old-slug", "deleted-now", "reused"):
        path = tmp_path / slug / "mise.db"
        path.parent.mkdir()
        path.touch()

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(booking_workflow, "available", lambda: True)
    monkeypatch.setattr(saas, "list_tenants", lambda **_: listed)
    monkeypatch.setattr(saas, "tenant_by_id", current.get)
    monkeypatch.setattr(saas, "tenant_db_path", lambda slug: tmp_path / slug / "mise.db")
    attempted: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        attempted.append(tenant["slug"])
        if tenant["slug"] == "missing":
            raise saas.TenantStorageUnavailable(tenant)
        yield tenant

    swept: list[bool] = []
    monkeypatch.setattr(saas, "tenant_runtime_existing", tenant_runtime)
    monkeypatch.setattr(booking_workflow, "sweep", lambda: swept.append(True))
    reports: list[tuple[str, str]] = []
    monkeypatch.setattr(
        saas,
        "report_tenant_storage_unavailable",
        lambda error, *, operation, reference=None: reports.append((error.slug, operation)),
    )

    scheduler._sweep_booking_workflows()

    assert attempted == ["missing"]
    assert swept == []
    assert reports == [("missing", "booking workflow recovery")]
    assert not (tmp_path / "renamed").exists()
    assert not (tmp_path / "missing").exists()


def test_existing_tenant_runtime_never_recreates_a_raced_deletion(tmp_path, monkeypatch):
    tenant = {"id": 7, "slug": "retained", "deleted_at": None}
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path)
    path = saas.tenant_db_path(tenant["slug"])
    db.migrate(path)

    with pytest.raises(saas.TenantStorageUnavailable, match="tenant storage unavailable"):
        with saas.tenant_runtime_existing(tenant):
            path.unlink()
            db.connect()

    assert not path.exists()
