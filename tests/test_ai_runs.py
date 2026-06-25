"""Phase 1 unit tests: ai_runs provenance recording + the content-facade flag.

Pure unit (no DB): ai_runs.record() is exercised with a db.run spy so we assert the
exact bound-parameter mapping from a ProviderResult, and the flag is exercised with a
patched config — both run in the fast `-m unit` gate.
"""

from unittest.mock import patch

import pytest

from app import ai_runs, features
from app.providers import Capability, ProviderResult, ResultStatus, ReviewRequirement

pytestmark = pytest.mark.unit


def test_record_maps_provenance_and_subject(monkeypatch):
    captured = {}

    def spy(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return 7

    monkeypatch.setattr(ai_runs.db, "run", spy)

    pr = ProviderResult(
        capability=Capability.CONTENT,
        provider="odysseus",
        status=ResultStatus.OK,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"caption": "a bright plate"},
        model="grok-x",
        latency_ms=42,
        cost_usd=0.01,
        tokens=12,
    )
    rid = ai_runs.record(
        pr, subject_type="retainer_caption", subject_id=99, correlation_id="corr-1"
    )

    assert rid == 7
    assert "INSERT INTO ai_runs" in captured["sql"]
    # column order: capability, provider, status, review, model, latency_ms, cost_usd,
    # tokens, error, subject_type, subject_id, correlation_id, idempotency_key
    assert captured["params"] == (
        "content",
        "odysseus",
        "ok",
        "human_review",
        "grok-x",
        42,
        0.01,
        12,
        None,
        "retainer_caption",
        99,
        "corr-1",
        None,
    )
    # the output payload is provenance-excluded — it must never reach the ledger
    assert "a bright plate" not in captured["params"]


def test_record_failure_logs_error_and_status(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ai_runs.db, "run", lambda sql, params=(): captured.update(params=params) or 1
    )

    pr = ProviderResult.failure(
        Capability.VISION, "argus", ResultStatus.PROVIDER_ERROR, "timed out", latency_ms=5
    )
    ai_runs.record(pr, subject_type="gallery", subject_id=3)

    p = captured["params"]
    assert p[0] == "vision" and p[1] == "argus" and p[2] == "provider_error"
    assert p[8] == "timed out" and p[9] == "gallery" and p[10] == 3


def test_content_provider_facade_flag():
    with patch("app.features.config") as cfg:
        cfg.PROVIDER_FACADE_CONTENT = False
        assert features.content_provider_facade_enabled() is False
        cfg.PROVIDER_FACADE_CONTENT = True
        assert features.content_provider_facade_enabled() is True
