"""Operations: the AI cost & activity report (admin).

DB-backed (real tmp DB + admin routes). Proves the report sums cost from the ai_runs
ledger by capability and by day over a window, surfaces the costed-vs-total caveat, honors
the window selector, exports CSV, and is admin-gated. Read-only — it writes nothing.
"""

import pytest
from fastapi.testclient import TestClient

from app import ai_runs, config, db, jobs
from app.main import app
from app.providers import Capability, ProviderResult, ResultStatus, ReviewRequirement


def _configure_tmp_db(tmp_path, monkeypatch):
    for attr, val in {
        "DATA_DIR": tmp_path,
        "DB_PATH": tmp_path / "mise.db",
        "MEDIA_DIR": tmp_path / "media",
        "ZIP_DIR": tmp_path / "zips",
        "TMP_DIR": tmp_path / "tmp",
        "BRAND_DIR": tmp_path / "brand",
        "RECEIPTS_DIR": tmp_path / "receipts",
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD": "test-pw",
    }.items():
        monkeypatch.setattr(config, attr, val)
    db.migrate()


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        assert r.status_code == 303
        yield client
    jobs.stop()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _run(capability, *, cost, status=ResultStatus.OK, model="m"):
    ai_runs.record(
        ProviderResult(
            capability=capability,
            provider="p",
            status=status,
            review=ReviewRequirement.HUMAN_REVIEW,
            model=model,
            cost_usd=cost,
        )
    )


def test_empty_state_renders(admin_client):
    body = admin_client.get("/admin/ai-cost").text
    assert "AI cost" in body
    assert "No AI runs in this window" in body


def test_totals_and_by_capability(admin_client):
    _run(Capability.VISION, cost=0.0)  # local challenger, $0 but reported
    _run(Capability.VISION, cost=0.0123)
    _run(Capability.CONTENT, cost=None)  # no cost reported -> counts as a run, $0 spend
    _run(Capability.VISION, cost=None, status=ResultStatus.PROVIDER_ERROR)

    body = admin_client.get("/admin/ai-cost").text
    assert "$0.0123" in body  # summed spend
    assert "Vision" in body and "Content" in body
    # caveat: only the runs that reported a cost are counted as costed (2 of 4)
    assert "2 of 4 runs reported a cost" in body


def test_window_selector_validates(admin_client):
    # a bogus window falls back to the 30-day default without error
    r = admin_client.get("/admin/ai-cost?days=999")
    assert r.status_code == 200
    assert "Last 30d" in r.text
    assert admin_client.get("/admin/ai-cost?days=7").status_code == 200


def test_csv_export(admin_client):
    _run(Capability.VISION, cost=0.5)
    r = admin_client.get("/admin/ai-cost.csv?days=30")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Day,Runs,Cost (USD)" in r.text


def test_ai_cost_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/ai-cost", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
