"""Recurring cleanup covers retained hosted tenant DBs without recreating tombstones."""

import sqlite3
import threading
from contextlib import contextmanager

import pytest

from app import booking_workflow, config, db, mobile_idempotency, saas, scheduler

pytestmark = pytest.mark.unit


def test_hosted_cleanup_includes_nonbillable_and_skips_tombstones(tmp_path, monkeypatch):
    tenants = [
        {"slug": "active", "plan_status": "active", "deleted_at": None},
        {"slug": "canceled", "plan_status": "canceled", "deleted_at": None},
        {"slug": "deleted", "plan_status": "canceled", "deleted_at": "2026-07-13"},
        {"slug": "missing", "plan_status": "unpaid", "deleted_at": None},
    ]
    for slug in ("active", "canceled", "deleted"):
        path = tmp_path / slug / "mise.db"
        path.parent.mkdir()
        path.touch()

    monkeypatch.setattr(saas, "list_tenants", lambda **_: tenants)
    monkeypatch.setattr(saas, "tenant_db_path", lambda slug: tmp_path / slug / "mise.db")
    current: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        current.append(tenant["slug"])
        try:
            yield tenant
        finally:
            current.pop()

    monkeypatch.setattr(saas, "tenant_runtime", tenant_runtime)
    pruned: list[str] = []
    monkeypatch.setattr(
        mobile_idempotency,
        "prune_expired",
        lambda: pruned.append(current[-1]) or 0,
    )

    scheduler._prune_hosted_mobile_idempotency()

    assert pruned == ["active", "canceled"]
    assert not (tmp_path / "missing").exists()


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

    @contextmanager
    def tenant_runtime(tenant):
        current.append(tenant["slug"])
        try:
            yield tenant
        finally:
            current.pop()

    monkeypatch.setattr(saas, "tenant_runtime_existing", tenant_runtime)
    swept: list[str] = []
    monkeypatch.setattr(booking_workflow, "sweep", lambda: swept.append(current[-1]) or 0)

    scheduler._sweep_booking_workflows()

    assert swept == ["active", "canceled"]
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
    entered: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        entered.append(tenant["slug"])
        yield tenant

    swept: list[bool] = []
    monkeypatch.setattr(saas, "tenant_runtime_existing", tenant_runtime)
    monkeypatch.setattr(booking_workflow, "sweep", lambda: swept.append(True))

    scheduler._sweep_booking_workflows()

    assert entered == []
    assert swept == []
    assert not (tmp_path / "renamed").exists()
    assert not (tmp_path / "missing").exists()


def test_existing_tenant_runtime_never_recreates_a_raced_deletion(tmp_path, monkeypatch):
    tenant = {"id": 7, "slug": "retained", "deleted_at": None}
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path)
    path = saas.tenant_db_path(tenant["slug"])
    path.parent.mkdir()
    path.touch()

    with saas.tenant_runtime_existing(tenant):
        path.unlink()
        with pytest.raises(sqlite3.OperationalError, match="unable to open"):
            db.connect()

    assert not path.exists()
