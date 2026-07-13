"""Recurring cleanup covers retained hosted tenant DBs without recreating tombstones."""

from contextlib import contextmanager

import pytest

from app import mobile_idempotency, saas, scheduler

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
