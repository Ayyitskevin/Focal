import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from starlette.requests import Request

from app import alerts, booking_notify, booking_workflow, config, db, jobs, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path: str, host: str, *, accept: str, method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"host", host.encode()), (b"accept", accept.encode())],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _remove_live_storage(tenant: dict, destination) -> None:
    saas.tenant_data_path(tenant["slug"]).rename(destination)


def _safe_signal_assertions(caplog, alert_calls, tenant, request_id: str) -> None:
    assert len(alert_calls) == 1
    signature, message = alert_calls[0]
    assert signature == f"tenant-storage:{tenant['id']}"
    assert f"tenant id={tenant['id']}" in message
    assert f"slug={tenant['slug']}" in message
    assert f"reference={request_id}" in message
    assert "restore from a verified backup" in message.lower()
    assert tenant["owner_email"] not in message
    assert str(config.SAAS_TENANT_DATA_DIR) not in message

    storage_records = [
        record
        for record in caplog.records
        if record.name == "mise.saas" and request_id in record.message
    ]
    assert len(storage_records) == 1
    assert f"tenant_id={tenant['id']}" in storage_records[0].message
    assert f"slug={tenant['slug']}" in storage_records[0].message
    assert tenant["owner_email"] not in storage_records[0].message
    assert str(config.SAAS_TENANT_DATA_DIR) not in storage_records[0].message


def test_cold_cache_missing_storage_returns_correlated_api_503_without_replacement(
    tmp_path, monkeypatch, caplog
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    lost = tmp_path / "lost-alpha"
    _remove_live_storage(tenant, lost)
    saas._MIGRATED_TENANT_DBS.clear()

    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )
    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        count = db.one("SELECT COUNT(*) AS n FROM clients")["n"]
        return saas.JSONResponse({"clients": count})

    request = _request("/api/v1/clients", "alpha.mise.test", accept="application/json")
    with caplog.at_level(logging.ERROR, logger="mise.saas"):
        response = asyncio.run(saas.tenant_middleware(request, call_next))

    body = json.loads(response.body)
    assert response.status_code == 503
    assert response.headers["x-request-id"].startswith("req_")
    assert body == {
        "type": "https://mise.example/problems/tenant-storage_unavailable",
        "title": "Studio temporarily unavailable",
        "status": 503,
        "code": "tenant.storage_unavailable",
        "detail": "This studio's data is temporarily unavailable. Please try again later.",
        "request_id": response.headers["x-request-id"],
        "errors": [],
    }
    assert called is False
    assert not saas.tenant_data_path("alpha").exists()
    assert not saas.tenant_db_path("alpha").exists()
    assert (lost / "mise.db").is_file()
    _safe_signal_assertions(caplog, alert_calls, tenant, body["request_id"])


def test_warm_process_deleted_database_returns_correlated_html_503_without_recreation(
    tmp_path, monkeypatch, caplog
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    assert str(path) in saas._MIGRATED_TENANT_DBS
    path.unlink()

    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )
    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        db.one("SELECT COUNT(*) AS n FROM clients")
        return saas.JSONResponse({"ok": True})

    request = _request("/admin/home", "alpha.mise.test", accept="text/html")
    with caplog.at_level(logging.ERROR, logger="mise.saas"):
        response = asyncio.run(saas.tenant_middleware(request, call_next))

    body = response.body.decode()
    request_id = response.headers["x-request-id"]
    assert response.status_code == 503
    assert request_id.startswith("req_")
    assert "Studio temporarily unavailable" in body
    assert "try again later" in body.lower()
    assert request_id in body
    assert tenant["owner_email"] not in body
    assert str(path) not in body
    assert called is False
    assert not path.exists()
    _safe_signal_assertions(caplog, alert_calls, tenant, request_id)


def test_deletion_after_preflight_is_translated_without_logging_a_bearer_path(
    tmp_path, monkeypatch, caplog
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )
    entered = False
    bearer_token = "manage-secret-token-should-not-be-logged"

    async def call_next(_request):
        nonlocal entered
        entered = True
        path.unlink()
        db.one("SELECT COUNT(*) AS n FROM clients")
        raise AssertionError("the deleted database query must not return")

    request = _request(f"/booking/{bearer_token}", "alpha.mise.test", accept="text/html")
    with caplog.at_level(logging.ERROR, logger="mise.saas"):
        response = asyncio.run(saas.tenant_middleware(request, call_next))

    request_id = response.headers["x-request-id"]
    assert entered is True
    assert response.status_code == 503
    assert not path.exists()
    assert bearer_token not in caplog.text
    assert all(bearer_token not in message for _, message in alert_calls)
    _safe_signal_assertions(caplog, alert_calls, tenant, request_id)


def test_swallowed_storage_error_still_overrides_an_empty_success_response(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    monkeypatch.setattr(alerts, "ops_alert", lambda *_args: None)

    async def call_next(_request):
        path.unlink()
        try:
            db.one("SELECT COUNT(*) AS n FROM clients")
        except db.ExistingDatabaseUnavailable:
            return saas.JSONResponse({"clients": []}, status_code=200)
        raise AssertionError("the deleted database must raise the typed sentinel")

    response = asyncio.run(
        saas.tenant_middleware(
            _request("/api/v1/clients", "alpha.mise.test", accept="application/json"),
            call_next,
        )
    )

    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "tenant.storage_unavailable"
    assert b'"clients":[]' not in response.body
    assert not path.exists()


def test_real_http_middleware_child_task_cannot_hide_a_swallowed_storage_error(
    tmp_path, monkeypatch
):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    monkeypatch.setattr(alerts, "ops_alert", lambda *_args: None)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()

    @test_app.middleware("http")
    async def tenant_context(request, call_next):
        return await saas.tenant_middleware(request, call_next)

    @test_app.get("/api/v1/swallowed")
    async def swallowed_route():
        path.unlink()
        try:
            db.one("SELECT COUNT(*) AS n FROM clients")
        except db.ExistingDatabaseUnavailable:
            return {"clients": []}
        raise AssertionError("the deleted database must raise the typed sentinel")

    with TestClient(test_app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v1/swallowed",
            headers={"host": "alpha.mise.test", "accept": "application/json"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "tenant.storage_unavailable"
    assert "clients" not in response.json()
    assert not path.exists()


def test_corrupt_tenant_database_returns_api_503_without_changing_the_file(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    corrupt = b"not a sqlite database\n"
    path.write_bytes(corrupt)
    saas._MIGRATED_TENANT_DBS.clear()
    monkeypatch.setattr(alerts, "ops_alert", lambda *_args: None)

    async def call_next(_request):
        raise AssertionError("unreadable storage must fail before a tenant handler runs")

    response = asyncio.run(
        saas.tenant_middleware(
            _request("/api/v1/clients", "alpha.mise.test", accept="application/json"),
            call_next,
        )
    )

    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "tenant.storage_unavailable"
    assert path.read_bytes() == corrupt


@pytest.mark.parametrize("shape", ["zero-byte", "unrelated", "empty-marker"])
def test_unrecognized_existing_sqlite_file_is_never_initialized_or_migrated(
    tmp_path, monkeypatch, shape
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    lost = tmp_path / "lost-alpha"
    _remove_live_storage(tenant, lost)
    data_path = saas.tenant_data_path("alpha")
    data_path.mkdir()
    path = saas.tenant_db_path("alpha")
    if shape == "zero-byte":
        path.touch()
    else:
        with sqlite3.connect(path) as con:
            if shape == "unrelated":
                con.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            else:
                con.execute(
                    "CREATE TABLE schema_migrations "
                    "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
    before = path.read_bytes()
    saas._MIGRATED_TENANT_DBS.clear()
    monkeypatch.setattr(alerts, "ops_alert", lambda *_args: None)

    async def call_next(_request):
        raise AssertionError("an unrecognized database must fail before routing")

    response = asyncio.run(
        saas.tenant_middleware(
            _request("/api/v1/clients", "alpha.mise.test", accept="application/json"),
            call_next,
        )
    )

    assert response.status_code == 503
    assert path.read_bytes() == before
    assert not path.with_name("mise.db-wal").exists()
    assert not path.with_name("mise.db-shm").exists()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        marker_table = con.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        marker = (
            con.execute("SELECT 1 FROM schema_migrations WHERE name='001_init.sql'").fetchone()
            if marker_table
            else None
        )
    assert marker is None


def test_latent_tenant_page_corruption_is_translated_when_the_query_reads_it(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        root_page = con.execute(
            "SELECT rootpage FROM sqlite_schema WHERE type='table' AND name='clients'"
        ).fetchone()[0]
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    contents = bytearray(path.read_bytes())
    page_offset = (root_page - 1) * page_size
    contents[page_offset] = 0xFF
    path.write_bytes(contents)
    corrupted_byte = path.read_bytes()[page_offset]
    monkeypatch.setattr(alerts, "ops_alert", lambda *_args: None)

    async def call_next(_request):
        db.one("SELECT * FROM clients LIMIT 1")
        raise AssertionError("the corrupt clients page must not return")

    response = asyncio.run(
        saas.tenant_middleware(
            _request("/api/v1/clients", "alpha.mise.test", accept="application/json"),
            call_next,
        )
    )

    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "tenant.storage_unavailable"
    assert path.read_bytes()[page_offset] == corrupted_byte


def test_missing_storage_returns_the_same_503_to_a_public_client_path(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    lost = tmp_path / "lost-alpha"
    _remove_live_storage(tenant, lost)
    saas._MIGRATED_TENANT_DBS.clear()
    monkeypatch.setattr(alerts, "ops_alert", lambda *_args: None)

    async def call_next(_request):
        return saas.HTMLResponse("empty studio", status_code=200)

    response = asyncio.run(
        saas.tenant_middleware(
            _request("/g/client-gallery", "alpha.mise.test", accept="text/html"),
            call_next,
        )
    )

    assert response.status_code == 503
    assert response.headers["x-request-id"].startswith("req_")
    assert "empty studio" not in response.body.decode()
    assert not saas.tenant_data_path("alpha").exists()
    assert (lost / "mise.db").is_file()


def test_platform_host_storage_exception_uses_the_registered_correlated_503_handler(
    tmp_path, monkeypatch, caplog
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    from app import main as app_main

    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )
    request = _request("/webhooks/stripe", "mise.test", accept="application/json", method="POST")

    with caplog.at_level(logging.ERROR, logger="mise.saas"):
        response = asyncio.run(
            app_main.tenant_storage_errors(request, saas.TenantStorageUnavailable(tenant))
        )

    body = json.loads(response.body)
    assert app_main.app.exception_handlers[saas.TenantStorageUnavailable] is (
        app_main.tenant_storage_errors
    )
    assert response.status_code == 503
    assert body == {
        "detail": "studio data temporarily unavailable",
        "request_id": response.headers["x-request-id"],
    }
    _safe_signal_assertions(caplog, alert_calls, tenant, body["request_id"])


def test_native_api_boundary_rethrows_post_preflight_storage_loss_to_parent_503(
    tmp_path, monkeypatch
):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    from app import mobile_api

    ops_calls = []
    error_calls = []
    monkeypatch.setattr(alerts, "ops_alert", lambda *args: ops_calls.append(args))
    monkeypatch.setattr(alerts, "error_alert", lambda *args: error_calls.append(args))

    async def mobile_handler(_request):
        path.unlink()
        db.one("SELECT COUNT(*) AS n FROM clients")
        raise AssertionError("the deleted tenant DB query must not return")

    async def call_next(request):
        return await mobile_api.contain_unhandled_errors(request, mobile_handler)

    response = asyncio.run(
        saas.tenant_middleware(
            _request("/api/v1/clients", "alpha.mise.test", accept="application/json"),
            call_next,
        )
    )

    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "tenant.storage_unavailable"
    assert len(ops_calls) == 1
    assert error_calls == []
    assert not path.exists()


def test_export_never_provisions_missing_tenant_storage(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    lost = tmp_path / "lost-alpha"
    _remove_live_storage(tenant, lost)
    saas._MIGRATED_TENANT_DBS.clear()

    with pytest.raises(RuntimeError, match="tenant storage unavailable"):
        saas.build_studio_export(tenant)

    assert not saas.tenant_data_path("alpha").exists()
    assert (lost / "mise.db").is_file()


def test_existing_database_cache_reset_replays_migrations_without_recreating_directories(
    tmp_path, monkeypatch
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    path = saas.tenant_db_path("alpha")
    brand = saas.tenant_data_path("alpha") / "brand"
    brand.rmdir()
    saas._MIGRATED_TENANT_DBS.clear()
    calls = []
    real_migrate = db.migrate

    def migrate(existing_path=None):
        calls.append(existing_path)
        return real_migrate(existing_path)

    monkeypatch.setattr(db, "migrate", migrate)

    with saas.tenant_runtime(tenant):
        assert db.one("SELECT COUNT(*) AS n FROM schema_migrations")["n"] > 0

    assert calls == [path]
    assert str(path) in saas._MIGRATED_TENANT_DBS
    assert not brand.exists()


def test_existing_runtime_does_not_reclassify_an_ordinary_sql_bug(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    with saas.tenant_runtime(tenant):
        with pytest.raises(sqlite3.OperationalError, match="no such table") as raised:
            db.one("SELECT * FROM definitely_missing_table")

    assert not isinstance(raised.value, db.ExistingDatabaseUnavailable)


def test_lazy_migration_sql_bug_is_not_reclassified_as_storage_loss(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas._MIGRATED_TENANT_DBS.clear()

    def broken_migration(_path=None):
        raise sqlite3.OperationalError("near BROKEN: syntax error")

    monkeypatch.setattr(db, "migrate", broken_migration)

    with pytest.raises(sqlite3.OperationalError, match="syntax error") as raised:
        with saas.tenant_runtime(tenant):
            pass

    assert not isinstance(raised.value, db.ExistingDatabaseUnavailable)


def test_tenant_middleware_does_not_reclassify_missing_media_as_storage_loss(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    async def call_next(_request):
        raise FileNotFoundError("missing media derivative")

    with pytest.raises(FileNotFoundError, match="missing media derivative"):
        asyncio.run(
            saas.tenant_middleware(
                _request("/g/client-gallery/media/1", "alpha.mise.test", accept="image/jpeg"),
                call_next,
            )
        )


def test_job_start_skips_missing_storage_and_recovers_healthy_tenant_jobs(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    missing = saas.create_tenant("missing", "Missing Studio", "missing@example.com", "secret123")
    healthy = saas.create_tenant("healthy", "Healthy Studio", "healthy@example.com", "secret123")
    with saas.tenant_runtime(healthy):
        job_id = jobs.enqueue("image_derivatives", {"asset_id": 99})
        db.run("UPDATE jobs SET status='running' WHERE id=?", (job_id,))

    lost = tmp_path / "lost-missing"
    _remove_live_storage(missing, lost)
    saas._MIGRATED_TENANT_DBS.clear()
    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )

    class FakePool:
        def __init__(self):
            self.submissions = []

        def submit(self, function, *args):
            self.submissions.append((function, args))

        def shutdown(self, **_kwargs):
            return None

    pool = FakePool()
    monkeypatch.setattr(jobs, "ThreadPoolExecutor", lambda **_kwargs: pool)

    try:
        jobs.start()
        assert not saas.tenant_data_path("missing").exists()
        assert (lost / "mise.db").is_file()
        with saas.tenant_runtime(healthy):
            assert db.one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == "queued"
        assert [(args[0], args[1]) for _, args in pool.submissions] == [(job_id, "healthy")]
        assert len(alert_calls) == 1
        assert alert_calls[0][0] == f"tenant-storage:{missing['id']}"
        assert "operation=job startup" in alert_calls[0][1]
    finally:
        jobs.stop()


def test_queued_job_reports_missing_storage_without_recreating_it(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        job_id = jobs.enqueue("image_derivatives", {"asset_id": 99})
    lost = tmp_path / "lost-alpha"
    _remove_live_storage(tenant, lost)
    saas._MIGRATED_TENANT_DBS.clear()
    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )

    jobs._execute(job_id, "alpha")

    assert not saas.tenant_data_path("alpha").exists()
    assert (lost / "mise.db").is_file()
    assert len(alert_calls) == 1
    assert alert_calls[0][0] == f"tenant-storage:{tenant['id']}"
    assert "operation=job execution" in alert_calls[0][1]


def test_job_handler_storage_failure_is_not_recorded_as_an_ordinary_retry(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        job_id = jobs.enqueue("image_derivatives", {"asset_id": 99})
    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )
    monkeypatch.setitem(
        jobs.HANDLERS,
        "image_derivatives",
        lambda _payload: (_ for _ in ()).throw(
            db.ExistingDatabaseUnavailable("simulated page corruption")
        ),
    )

    jobs._execute(job_id, "alpha")

    with saas.tenant_runtime(tenant):
        row = db.one("SELECT status, error FROM jobs WHERE id=?", (job_id,))
    assert dict(row) == {"status": "running", "error": None}
    assert len(alert_calls) == 1
    assert alert_calls[0][0] == f"tenant-storage:{tenant['id']}"
    assert "operation=job execution" in alert_calls[0][1]


def test_booking_workflow_storage_failure_is_not_converted_to_provider_retry(monkeypatch):
    failure = db.ExistingDatabaseUnavailable("existing database unavailable")
    fail_calls = []

    def raise_storage_failure(*_args):
        raise failure

    monkeypatch.setattr(booking_notify, "run_reschedule_effect", raise_storage_failure)
    monkeypatch.setattr(
        booking_workflow,
        "_fail",
        lambda *_args: fail_calls.append(True),
    )
    claimed = {
        "effect_kind": "client_cancel_ics",
        "source_booking_id": 1,
        "replacement_booking_id": 2,
    }

    with pytest.raises(db.ExistingDatabaseUnavailable) as raised:
        booking_workflow._execute(claimed)

    assert raised.value is failure
    assert fail_calls == []


def test_new_tenant_provisioning_remains_the_positive_creation_boundary(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert saas.tenant_db_path("alpha").is_file()
    with saas.tenant_runtime(tenant):
        assert db.one("SELECT COUNT(*) AS n FROM schema_migrations")["n"] > 0
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 0


def test_failed_new_tenant_provision_rolls_back_control_row_and_remains_retryable(
    tmp_path, monkeypatch
):
    _configure_saas(tmp_path, monkeypatch)
    real_provision = saas._provision_tenant_database
    attempts = 0

    def flaky_provision(tenant):
        nonlocal attempts
        attempts += 1
        saas.tenant_data_path(tenant["slug"]).mkdir(parents=True, exist_ok=True)
        if attempts == 1:
            raise RuntimeError("simulated provisioning failure")
        return real_provision(tenant)

    monkeypatch.setattr(saas, "_provision_tenant_database", flaky_provision)

    with pytest.raises(RuntimeError, match="simulated provisioning failure"):
        saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert saas.tenant_by_slug("alpha") is None
    assert not saas.tenant_db_path("alpha").exists()

    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert attempts == 2
    assert tenant["slug"] == "alpha"
    assert saas.tenant_db_path("alpha").is_file()


def test_provisioning_integrity_failure_is_not_mislabeled_as_a_duplicate_slug(
    tmp_path, monkeypatch
):
    _configure_saas(tmp_path, monkeypatch)

    def broken_provision(_tenant):
        raise sqlite3.IntegrityError("migration constraint failed")

    monkeypatch.setattr(saas, "_provision_tenant_database", broken_provision)

    with pytest.raises(sqlite3.IntegrityError, match="migration constraint failed"):
        saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert saas.tenant_by_slug("alpha") is None


def test_storage_contract_and_restore_first_runbook_stay_aligned():
    architecture = Path("docs/ARCHITECTURE.md").read_text()
    runbook = Path("docs/MISE-SOLO-STUDIO-OS-RUNBOOK.md").read_text()
    readme = Path("README.md").read_text()
    reviewer_guide = Path("docs/REVIEWER-GUIDE.md").read_text()

    for phrase in (
        "New-tenant provisioning is the only boundary",
        "SQLite `mode=rw`",
        "503 tenant.storage_unavailable",
        "unconditional",
        "throttled ops alert when configured",
    ):
        assert phrase in architecture
    backup_section = runbook.split("## 10. Hosted backups & restore", 1)[1].split(
        "### Tenant-storage incident", 1
    )[0]
    for phrase in (
        "archived control snapshot",
        ".last-hosted-backup-failures",
        "`backup_partial`",
        "does **not** prove every tenant snapshot",
        "one-off command exits non-zero",
    ):
        assert phrase in backup_section
    assert "restore, never reprovision" in runbook
    assert "X-Request-ID" in runbook
    assert "no empty-success response" in runbook

    incident = runbook.split("### Tenant-storage incident: restore, never reprovision", 1)[1].split(
        "### Restore drill", 1
    )[0]
    drill = runbook.split("### Restore drill", 1)[1].split("## 11.", 1)[0]

    def assert_ordered(section, phrases):
        positions = [section.index(phrase) for phrase in phrases]
        assert positions == sorted(positions)

    assert_ordered(
        incident,
        (
            "Restore it to a side file",
            "PRAGMA quick_check",
            "PRAGMA foreign_key_check",
            "schema_migrations` contains `001_init.sql",
            "Human gate",
            "timestamped copies",
            "`mise.db`, `mise.db-wal`, and `mise.db-shm`",
            "atomically move",
            "only after their backups",
            "Start the app",
            "expected studio records",
            "fresh hosted backup",
        ),
    )
    assert_ordered(
        drill,
        (
            "Restore one tenant DB",
            "mise.db.restore",
            "Validate the side file",
            "PRAGMA quick_check",
            "PRAGMA foreign_key_check",
            "schema_migrations` table contains `001_init.sql",
            "Human gate",
            "timestamped copies",
            "`mise.db`, `mise.db-wal`, and `mise.db-shm`",
            "Atomically rename",
            "only then remove stale",
            "Start the app",
            "expected records",
            "fresh hosted backup",
        ),
    )
    assert "empty replacement studio" in readme
    assert "issues/181" not in readme
    assert "issues/181" not in reviewer_guide
