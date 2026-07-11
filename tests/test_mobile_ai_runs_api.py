"""Owner-only, privacy-bounded native AI activity API contracts."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, ratelimit, saas
from app.main import app

pytestmark = pytest.mark.unit

_ITEM_KEYS = {
    "id",
    "capability",
    "provider",
    "status",
    "review",
    "latency_ms",
    "cost_micro_usd",
    "tokens",
    "subject",
    "created_at",
}
_SUBJECT_KEYS = {"kind"}


def _device(name: str) -> dict:
    return {
        "installation_id": "0A1E80EC-2DCB-4C8F-A864-89651874F0C8",
        "name": name,
        "platform": "ios",
        "app_version": "5.0",
    }


@pytest.fixture
def ai_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "native-ai-activity-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _owner_headers(
    client: TestClient,
    *,
    email: str | None = None,
    password: str = "owner-password",
    name: str = "Owner iPhone",
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": email,
            "password": password,
            "device": _device(name),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _guest_headers(client: TestClient, slug: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={
            "kind": "gallery",
            "slug": slug,
            "pin": "2468",
            "device": _device("Client iPhone"),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _insert_run(
    *,
    capability: str = "vision",
    provider: str = "argus",
    status: str = "ok",
    review: str = "human_review",
    model: str | None = "argus-v1",
    latency_ms: object = 125,
    cost_usd: object = 0.001,
    tokens: object = 42,
    error: str = "PRIVATE-PROVIDER-ERROR",
    subject_type: str | None = None,
    subject_id: int | None = None,
    correlation_id: str = "PRIVATE-CORRELATION-ID",
    idempotency_key: str = "PRIVATE-IDEMPOTENCY-KEY",
    created_at: str = "2026-07-11 12:00:00",
) -> int:
    return db.run(
        """INSERT INTO ai_runs
           (capability,provider,status,review,model,latency_ms,cost_usd,tokens,error,
            subject_type,subject_id,correlation_id,idempotency_key,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            capability,
            provider,
            status,
            review,
            model,
            latency_ms,
            cost_usd,
            tokens,
            error,
            subject_type,
            subject_id,
            correlation_id,
            idempotency_key,
            created_at,
        ),
    )


def _seed_gallery(*, slug: str = "native-ai", title: str = "Native AI") -> int:
    return db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,created_at)
           VALUES (?,?,?,1,'gallery',1,'2026-07-11 09:00:00')""",
        (slug, title, "2468"),
    )


def _configure_hosted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "native-ai-hosted-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    ratelimit._hits.clear()
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_ai_activity_pages_are_safe_normalized_conditional_and_keyset_ordered(ai_client):
    gallery_id = _seed_gallery(title="  Gallery\u202e\n One  ")
    oldest_id = _insert_run(
        capability="content",
        provider="dionysus",
        status="disabled",
        review="explicit_commit",
        subject_type="retainer_caption",
        subject_id=73,
        created_at="2026-07-11 10:00:00",
    )
    unknown_id = _insert_run(
        capability="offers-internal",
        provider="https://PRIVATE-HOST/path",
        status="mystery-status",
        review="provider-decides",
        model="/srv/PRIVATE-MODEL",
        latency_ms=-1,
        cost_usd=-0.25,
        tokens=str(2**63),
        subject_type="project-internal",
        subject_id=424242,
        created_at="2026-07-11T11:00:00-04:00",
    )
    deleted_id = _insert_run(
        subject_type="gallery",
        subject_id=999999,
        created_at="2026-07-11 12:00:00",
    )
    gallery_run_id = _insert_run(
        provider="argus",
        model="model-one",
        latency_ms=321,
        cost_usd=0.0000015,
        tokens=987,
        subject_type="gallery",
        subject_id=gallery_id,
        created_at="2026-07-11 13:00:00",
    )
    latest_id = _insert_run(
        capability="products",
        provider="plutus",
        status="invalid_response",
        review="none",
        model=None,
        latency_ms=None,
        cost_usd=None,
        tokens=None,
        created_at="2026-07-11 14:00:00",
    )
    headers = _owner_headers(ai_client)

    first = ai_client.get("/api/v1/ai/runs?limit=2", headers=headers)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["vary"] == "Authorization"
    assert first.headers["etag"].startswith('"ai-runs-v1-')
    first_payload = first.json()
    assert [item["id"] for item in first_payload["items"]] == [latest_id, gallery_run_id]
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]
    assert first_payload["items"][0]["subject"] is None

    gallery_item = first_payload["items"][1]
    assert set(gallery_item) == _ITEM_KEYS
    assert gallery_item == {
        "id": gallery_run_id,
        "capability": "vision",
        "provider": "argus",
        "status": "ok",
        "review": "human_review",
        "latency_ms": 321,
        "cost_micro_usd": 2,
        "tokens": 987,
        "subject": {
            "kind": "gallery",
        },
        "created_at": "2026-07-11T13:00:00Z",
    }
    assert set(gallery_item["subject"]) == _SUBJECT_KEYS

    cached = ai_client.get(
        "/api/v1/ai/runs?limit=2",
        headers={**headers, "If-None-Match": f"W/{first.headers['etag']}"},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == first.headers["etag"]
    assert cached.headers["cache-control"] == "private, no-cache"
    assert cached.headers["vary"] == "Authorization"

    db.run("UPDATE galleries SET title='PRIVATE RENAMED CLIENT' WHERE id=?", (gallery_id,))
    renamed = ai_client.get(
        "/api/v1/ai/runs?limit=2",
        headers={**headers, "If-None-Match": first.headers["etag"]},
    )
    assert renamed.status_code == 304

    _insert_run(provider="new-provider", created_at="2026-07-11 15:00:00")
    second = ai_client.get(
        "/api/v1/ai/runs",
        params={"limit": 2, "cursor": first_payload["next_cursor"]},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert [item["id"] for item in second.json()["items"]] == [deleted_id, unknown_id]
    assert second.json()["items"][0]["subject"] == {
        "kind": "gallery",
    }
    unknown = second.json()["items"][1]
    assert unknown["capability"] == "other"
    assert unknown["provider"] == "other"
    assert unknown["status"] == "unknown"
    assert unknown["review"] == "unknown"
    assert unknown["latency_ms"] is None
    assert unknown["cost_micro_usd"] is None
    assert unknown["tokens"] is None
    assert unknown["subject"] == {
        "kind": "other",
    }
    assert unknown["created_at"] == "2026-07-11T15:00:00Z"

    third = ai_client.get(
        "/api/v1/ai/runs",
        params={"limit": 2, "cursor": second.json()["next_cursor"]},
        headers=headers,
    )
    assert third.status_code == 200, third.text
    assert [item["id"] for item in third.json()["items"]] == [oldest_id]
    assert third.json()["items"][0]["subject"] == {
        "kind": "caption",
    }
    assert third.json()["has_more"] is False
    assert third.json()["next_cursor"] is None

    all_items = first_payload["items"] + second.json()["items"] + third.json()["items"]
    assert all(set(item) == _ITEM_KEYS for item in all_items)
    serialized = json.dumps(all_items)
    for private_value in (
        "PRIVATE-PROVIDER-ERROR",
        "PRIVATE-CORRELATION-ID",
        "PRIVATE-IDEMPOTENCY-KEY",
        "PRIVATE-HOST",
        "PRIVATE-MODEL",
        "project-internal",
        "424242",
        "999999",
    ):
        assert private_value not in serialized

    changed = ai_client.get(
        "/api/v1/ai/runs?limit=2",
        headers={**headers, "If-None-Match": first.headers["etag"]},
    )
    assert changed.status_code == 200
    assert changed.headers["etag"] != first.headers["etag"]


def test_ai_activity_requires_owner_and_rejects_bad_bounds_and_cursors(ai_client):
    assert ai_client.get("/api/v1/ai/runs").status_code == 401
    gallery_id = _seed_gallery()
    guest = _guest_headers(ai_client, "native-ai")
    denied = ai_client.get("/api/v1/ai/runs", headers=guest)
    assert denied.status_code == 403
    assert denied.headers["content-type"].startswith("application/problem+json")
    assert denied.json()["code"] == "auth.insufficient_scope"

    owner = _owner_headers(ai_client)
    wrong_host = ai_client.get("https://other.test/api/v1/ai/runs", headers=owner)
    assert wrong_host.status_code == 401
    assert wrong_host.json()["code"] == "auth.invalid_token"

    for limit in (0, 101):
        invalid = ai_client.get(
            "/api/v1/ai/runs",
            params={"limit": limit},
            headers=owner,
        )
        assert invalid.status_code == 422
        assert invalid.headers["content-type"].startswith("application/problem+json")
        assert invalid.json()["code"] == "request.validation_failed"

    for params in (
        [("tenant_id", "999")],
        [("provider", "argus")],
        [("limit", "1"), ("limit", "2")],
        [("cursor", "one"), ("cursor", "two")],
    ):
        invalid_query = ai_client.get(
            "/api/v1/ai/runs",
            params=params,
            headers=owner,
        )
        assert invalid_query.status_code == 422
        assert invalid_query.json()["code"] == "request.validation_failed"

    oversized = ai_client.get(
        "/api/v1/ai/runs",
        params={"cursor": "x" * 1025},
        headers=owner,
    )
    assert oversized.status_code == 422
    assert oversized.json()["code"] == "request.validation_failed"

    _insert_run(subject_type="gallery", subject_id=gallery_id)
    _insert_run(subject_type="gallery", subject_id=gallery_id)
    first = ai_client.get("/api/v1/ai/runs?limit=1", headers=owner)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    invalid_cursor = ai_client.get(
        "/api/v1/ai/runs",
        params={"cursor": tampered},
        headers=owner,
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.headers["content-type"].startswith("application/problem+json")
    assert invalid_cursor.json()["code"] == "pagination.invalid_cursor"

    detail = ai_client.get("/api/v1/ai/runs/1", headers=owner)
    assert detail.status_code == 404


def test_ai_activity_empty_page_is_stable(ai_client):
    headers = _owner_headers(ai_client)
    response = ai_client.get("/api/v1/ai/runs?limit=100", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None, "has_more": False}
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["vary"] == "Authorization"
    cached = ai_client.get(
        "/api/v1/ai/runs?limit=100",
        headers={**headers, "If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304


def test_invalid_legacy_timestamp_fails_closed_without_leaking_value(ai_client):
    sentinel = "PRIVATE-BAD-TIMESTAMP"
    _insert_run(created_at=sentinel)
    headers = _owner_headers(ai_client)

    response = ai_client.get("/api/v1/ai/runs", headers=headers)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "server.internal"
    assert sentinel not in response.text


def test_fractional_legacy_integer_metrics_are_omitted(ai_client):
    run_id = _insert_run(latency_ms=1.5, tokens=2.5)
    headers = _owner_headers(ai_client)

    response = ai_client.get("/api/v1/ai/runs", headers=headers)

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == run_id
    assert item["latency_ms"] is None
    assert item["tokens"] is None


def test_hosted_ai_activity_isolates_overlapping_ids_and_cursors(tmp_path, monkeypatch):
    _configure_hosted(tmp_path, monkeypatch)
    alpha = saas.create_tenant(
        "alpha",
        "Alpha Studio",
        "owner@alpha.test",
        "alpha-password",
    )
    beta = saas.create_tenant(
        "beta",
        "Beta Studio",
        "owner@beta.test",
        "beta-password",
    )
    with saas.tenant_runtime(alpha):
        alpha_ids = [
            _insert_run(provider="qwen3-vl"),
            _insert_run(provider="argus"),
        ]
    with saas.tenant_runtime(beta):
        beta_ids = [
            _insert_run(provider="odysseus"),
            _insert_run(provider="dionysus"),
        ]
    assert alpha_ids == beta_ids == [1, 2]

    alpha_client = TestClient(app, base_url="https://alpha.mise.test")
    beta_client = TestClient(app, base_url="https://beta.mise.test")
    try:
        alpha_owner = _owner_headers(
            alpha_client,
            email="owner@alpha.test",
            password="alpha-password",
            name="Alpha iPhone",
        )
        beta_owner = _owner_headers(
            beta_client,
            email="owner@beta.test",
            password="beta-password",
            name="Beta iPhone",
        )
        alpha_page = alpha_client.get("/api/v1/ai/runs?limit=1", headers=alpha_owner)
        beta_page = beta_client.get("/api/v1/ai/runs?limit=1", headers=beta_owner)
        assert alpha_page.status_code == beta_page.status_code == 200
        assert alpha_page.json()["items"][0]["provider"] == "argus"
        assert beta_page.json()["items"][0]["provider"] == "dionysus"
        assert alpha_page.json()["items"][0]["id"] == beta_page.json()["items"][0]["id"] == 2

        assert beta_client.get("/api/v1/ai/runs", headers=alpha_owner).status_code == 401
        cross_tenant_cursor = beta_client.get(
            "/api/v1/ai/runs",
            params={"limit": 1, "cursor": alpha_page.json()["next_cursor"]},
            headers=beta_owner,
        )
        assert cross_tenant_cursor.status_code == 422
        assert cross_tenant_cursor.json()["code"] == "pagination.invalid_cursor"

        alpha_second = alpha_client.get(
            "/api/v1/ai/runs",
            params={"limit": 1, "cursor": alpha_page.json()["next_cursor"]},
            headers=alpha_owner,
        )
        beta_second = beta_client.get(
            "/api/v1/ai/runs",
            params={"limit": 1, "cursor": beta_page.json()["next_cursor"]},
            headers=beta_owner,
        )
        assert alpha_second.json()["items"][0]["provider"] == "qwen"
        assert beta_second.json()["items"][0]["provider"] == "odysseus"
    finally:
        alpha_client.close()
        beta_client.close()
        ratelimit._hits.clear()
