"""Phase 2 unit tests: shadow comparison + the asset-safe, inert-by-default runner.

Pure unit (no DB): the comparison is a pure function; the runner's no-op guards (flag
off, no challenger, no completed legacy run) are exercised with a spy on ai_runs.record
so we prove it writes nothing in those cases. The happy path (records two linked rows)
needs a real DB and lives in tests/test_smoke_vision_shadow.py.
"""

from unittest.mock import patch

import pytest

from app import config, features, vision_shadow
from app.providers import (
    Capability,
    ProviderResult,
    ResultStatus,
    ReviewRequirement,
    registry,
    shadow,
)
from app.providers.mocks import MockVisionAdapter, MockVisionChallengerAdapter

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset()
    yield
    registry.reset()


def _ok(provider, *, model, latency_ms=0, cost_usd=None):
    return ProviderResult(
        capability=Capability.VISION,
        provider=provider,
        status=ResultStatus.OK,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"run_id": 1},
        model=model,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )


# ── compare (pure) ───────────────────────────────────────────────────────────


def test_compare_both_ok_with_deltas():
    legacy = _ok("argus", model="argus", latency_ms=100, cost_usd=0.05)
    chal = _ok("local", model="qwen-vl", latency_ms=40, cost_usd=0.0)
    c = shadow.compare(legacy, chal)
    assert c["both_ok"] is True and c["status_agree"] is True
    assert c["legacy_provider"] == "argus" and c["challenger_provider"] == "local"
    assert c["latency_delta_ms"] == -60  # challenger faster
    assert c["cost_delta_usd"] == -0.05  # challenger cheaper


def test_compare_status_mismatch():
    legacy = _ok("argus", model="argus")
    chal = ProviderResult.failure(
        Capability.VISION, "local", ResultStatus.PROVIDER_ERROR, "boom", latency_ms=3
    )
    c = shadow.compare(legacy, chal)
    assert c["both_ok"] is False
    assert c["status_agree"] is False
    assert c["challenger_status"] == "provider_error"


def test_compare_missing_metric_delta_is_none():
    legacy = _ok("argus", model="argus", latency_ms=None, cost_usd=None)
    chal = _ok("local", model="x", latency_ms=10, cost_usd=0.0)
    c = shadow.compare(legacy, chal)
    assert c["latency_delta_ms"] is None and c["cost_delta_usd"] is None


# ── flag + challenger seam ─────────────────────────────────────────────────────


def test_vision_shadow_flag_default():
    with patch("app.features.config") as cfg:
        cfg.VISION_SHADOW = False
        assert features.vision_shadow_enabled() is False
        cfg.VISION_SHADOW = True
        assert features.vision_shadow_enabled() is True


def test_challenger_seam_defaults_none_and_restores():
    assert registry.challenger(Capability.VISION) is None
    mock = MockVisionChallengerAdapter()
    with registry.use_challenger(Capability.VISION, mock):
        assert registry.challenger(Capability.VISION) is mock
    assert registry.challenger(Capability.VISION) is None


# ── runner no-op guards (pure: ai_runs.record spied) ───────────────────────────


def _spy_record(monkeypatch):
    calls = []
    monkeypatch.setattr(vision_shadow.ai_runs, "record", lambda *a, **k: calls.append((a, k)) or 1)
    return calls


def test_runner_noop_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "VISION_SHADOW", False)
    calls = _spy_record(monkeypatch)
    with registry.use_challenger(Capability.VISION, MockVisionChallengerAdapter()):
        assert vision_shadow.run_for_gallery(1) is None
    assert calls == []


def test_runner_noop_when_no_challenger(monkeypatch):
    monkeypatch.setattr(config, "VISION_SHADOW", True)
    calls = _spy_record(monkeypatch)
    # registry reset by fixture -> no challenger
    assert vision_shadow.run_for_gallery(1) is None
    assert calls == []


def test_runner_noop_when_no_completed_legacy_run(monkeypatch):
    monkeypatch.setattr(config, "VISION_SHADOW", True)
    monkeypatch.setattr(vision_shadow.db, "one", lambda sql, params=(): None)
    calls = _spy_record(monkeypatch)
    seen = []

    class Spy(MockVisionChallengerAdapter):
        def analyze_gallery(self, gallery_id, *, skip_dedup=False):
            seen.append(gallery_id)
            return super().analyze_gallery(gallery_id)

    with registry.use_challenger(Capability.VISION, Spy()):
        assert vision_shadow.run_for_gallery(1) is None
    assert calls == [] and seen == []  # challenger never called, nothing recorded


def test_legacy_result_for_maps_done_run(monkeypatch):
    monkeypatch.setattr(
        vision_shadow.db,
        "one",
        lambda sql, params=(): {"argus_last_run_id": 42, "argus_last_status": "done"},
    )
    pr = vision_shadow.legacy_result_for(7)
    assert pr is not None and pr.ok and pr.provider == "argus" and pr.output["run_id"] == 42


def test_legacy_result_for_none_when_not_done(monkeypatch):
    monkeypatch.setattr(
        vision_shadow.db,
        "one",
        lambda sql, params=(): {"argus_last_run_id": None, "argus_last_status": "queued"},
    )
    assert vision_shadow.legacy_result_for(7) is None


def test_mock_vision_and_challenger_differ():
    # the two mocks must produce a comparable difference for shadow tests
    legacy = MockVisionAdapter().analyze_gallery(3)
    chal = MockVisionChallengerAdapter().analyze_gallery(3)
    assert legacy.output["run_id"] != chal.output["run_id"]
    assert legacy.model != chal.model
