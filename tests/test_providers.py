"""Phase 0 provider-facade contract tests.

All pure unit (no DB, no network): the legacy trigger / draft functions are
monkeypatched, so these run in the fast `-m unit` gate. They prove:

* the typed contract (ProviderResult status / review / provenance / factories);
* legacy adapters reproduce legacy outputs and map every failure to a non-OK status;
* a provider failure writes NOTHING — exercised against the *real* trigger code path
  with a failing urlopen and a db.run spy (audit invariant: no partial write);
* the disabled-feature path stays dormant;
* deterministic mock adapters;
* the registry defaults to the legacy adapter and `use()` overrides cleanly.
"""

import pytest

from app import argus_analyze, caption_ai, platekit, plutus_recommend, providers
from app.providers import adapters, mocks, registry
from app.providers.contracts import (
    Capability,
    ProviderResult,
    ResultStatus,
    ReviewRequirement,
)

pytestmark = pytest.mark.unit


# ── contract ─────────────────────────────────────────────────────────────────


def test_result_ok_and_provenance():
    r = ProviderResult(
        capability=Capability.VISION,
        provider="argus",
        status=ResultStatus.OK,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"run_id": 7},
        model="argus",
        latency_ms=12,
    )
    assert r.ok is True
    prov = r.provenance()
    assert prov["capability"] == "vision"
    assert prov["provider"] == "argus"
    assert prov["status"] == "ok"
    assert prov["review"] == "human_review"
    assert prov["model"] == "argus"
    assert prov["latency_ms"] == 12
    # provenance never leaks the output payload, only the metadata
    assert "output" not in prov


def test_disabled_and_failure_factories_are_not_ok():
    d = ProviderResult.disabled(Capability.CONTENT, "odysseus")
    assert d.status is ResultStatus.DISABLED
    assert d.ok is False
    assert d.review is ReviewRequirement.HUMAN_REVIEW

    f = ProviderResult.failure(
        Capability.OFFERS, "plutus", ResultStatus.PROVIDER_ERROR, "boom", latency_ms=3
    )
    assert f.status is ResultStatus.PROVIDER_ERROR
    assert f.ok is False
    assert f.error == "boom"
    assert f.latency_ms == 3


def test_failure_rejects_ok_status():
    with pytest.raises(ValueError):
        ProviderResult.failure(Capability.VISION, "argus", ResultStatus.OK, "nope")


# ── legacy VISION adapter (Argus) ──────────────────────────────────────────────


def test_argus_adapter_disabled(monkeypatch):
    monkeypatch.setattr(argus_analyze, "is_enabled", lambda: False)
    r = adapters.LegacyArgusVisionAdapter().analyze_gallery(1)
    assert r.status is ResultStatus.DISABLED
    assert r.provider == "argus"
    assert r.capability is Capability.VISION


def test_argus_adapter_ok_queued(monkeypatch):
    monkeypatch.setattr(argus_analyze, "is_enabled", lambda: True)
    monkeypatch.setattr(
        argus_analyze,
        "trigger_gallery_analyze",
        lambda gid, skip_dedup=False: {"mode": "queued", "job_id": "job-1"},
    )
    r = adapters.LegacyArgusVisionAdapter().analyze_gallery(5)
    assert r.ok
    assert r.output == {"run_id": None, "job_id": "job-1", "mode": "queued"}
    assert r.review is ReviewRequirement.HUMAN_REVIEW
    assert r.latency_ms is not None


def test_argus_adapter_provider_error(monkeypatch):
    monkeypatch.setattr(argus_analyze, "is_enabled", lambda: True)

    def boom(gid, skip_dedup=False):
        raise argus_analyze.ArgusAnalyzeError("Argus unreachable: timed out")

    monkeypatch.setattr(argus_analyze, "trigger_gallery_analyze", boom)
    r = adapters.LegacyArgusVisionAdapter().analyze_gallery(5)
    assert r.status is ResultStatus.PROVIDER_ERROR
    assert "unreachable" in r.error
    assert r.output is None


def test_argus_adapter_invalid_response(monkeypatch):
    monkeypatch.setattr(argus_analyze, "is_enabled", lambda: True)
    monkeypatch.setattr(
        argus_analyze, "trigger_gallery_analyze", lambda gid, skip_dedup=False: {"mode": "sync"}
    )
    r = adapters.LegacyArgusVisionAdapter().analyze_gallery(5)
    assert r.status is ResultStatus.INVALID_RESPONSE
    assert r.output is None


def test_argus_adapter_failure_writes_nothing(monkeypatch):
    """The audit's core invariant: a provider failure mutates no record.

    Drives the REAL trigger_gallery_analyze (so its early gallery read + request build
    run) with a urlopen that times out, and spies on db.run. The adapter must surface
    PROVIDER_ERROR and the db.run spy must record zero writes.
    """
    from app import config

    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    monkeypatch.setattr(config, "BASE_URL", "http://mise")

    fake_gallery = {"id": 9, "published": 1, "type": "gallery", "project_id": None}
    monkeypatch.setattr(argus_analyze.db, "one", lambda sql, params=(): fake_gallery)

    writes: list[tuple] = []
    monkeypatch.setattr(
        argus_analyze.db, "run", lambda sql, params=(): writes.append((sql, params))
    )

    def timeout(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(argus_analyze.urllib.request, "urlopen", timeout)

    r = adapters.LegacyArgusVisionAdapter().analyze_gallery(9)
    assert r.status is ResultStatus.PROVIDER_ERROR
    assert writes == [], "provider failure must not write to the database"


def test_argus_adapter_success_is_also_non_mutating(monkeypatch):
    """Even on success the facade only returns a result — recording is the caller's job."""
    import json

    from app import config

    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    monkeypatch.setattr(config, "BASE_URL", "http://mise")
    monkeypatch.setattr(
        argus_analyze.db,
        "one",
        lambda sql, params=(): {"id": 9, "published": 1, "type": "gallery", "project_id": None},
    )
    writes: list[tuple] = []
    monkeypatch.setattr(
        argus_analyze.db, "run", lambda sql, params=(): writes.append((sql, params))
    )

    class FakeResp:
        def read(self):
            return json.dumps({"mode": "queued", "job_id": "job-2"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(argus_analyze.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    r = adapters.LegacyArgusVisionAdapter().analyze_gallery(9)
    assert r.ok
    assert r.output["job_id"] == "job-2"
    assert writes == [], "the provider facade never records — the caller does"


# ── legacy OFFERS adapter (Plutus) ─────────────────────────────────────────────


def test_plutus_adapter_ok(monkeypatch):
    monkeypatch.setattr(plutus_recommend, "is_enabled", lambda: True)
    monkeypatch.setattr(
        plutus_recommend,
        "trigger_gallery_recommend",
        lambda gid: {
            "run_id": 11,
            "bundles": [{"a": 1}, {"b": 2}],
            "estimated_total_cents": 25000,
            "review_url": "http://plutus/runs/11",
        },
    )
    r = adapters.LegacyPlutusOffersAdapter().recommend_gallery(3)
    assert r.ok
    assert r.capability is Capability.OFFERS
    assert r.output["run_id"] == 11
    assert r.output["bundle_count"] == 2
    assert r.output["estimated_total_cents"] == 25000
    assert r.review is ReviewRequirement.HUMAN_REVIEW


def test_plutus_adapter_bundle_count_fallback(monkeypatch):
    monkeypatch.setattr(plutus_recommend, "is_enabled", lambda: True)
    monkeypatch.setattr(
        plutus_recommend,
        "trigger_gallery_recommend",
        lambda gid: {"run_id": 12, "bundle_count": 4},
    )
    r = adapters.LegacyPlutusOffersAdapter().recommend_gallery(3)
    assert r.output["bundle_count"] == 4
    assert r.output["estimated_total_cents"] is None


def test_plutus_adapter_provider_error(monkeypatch):
    monkeypatch.setattr(plutus_recommend, "is_enabled", lambda: True)

    def boom(gid):
        raise plutus_recommend.PlutusRecommendError("Plutus returned HTTP 401")

    monkeypatch.setattr(plutus_recommend, "trigger_gallery_recommend", boom)
    r = adapters.LegacyPlutusOffersAdapter().recommend_gallery(3)
    assert r.status is ResultStatus.PROVIDER_ERROR
    assert "401" in r.error


def test_plutus_adapter_disabled(monkeypatch):
    monkeypatch.setattr(plutus_recommend, "is_enabled", lambda: False)
    r = adapters.LegacyPlutusOffersAdapter().recommend_gallery(3)
    assert r.status is ResultStatus.DISABLED


# ── legacy CONTENT adapters (Odysseus caption + Dionysus packs) ─────────────────


def test_caption_adapter_reproduces_legacy_output(monkeypatch):
    monkeypatch.setattr(caption_ai, "is_enabled", lambda: True)
    monkeypatch.setattr(
        caption_ai, "draft_caption", lambda ctx: {"caption": "A bright plate.", "model": "grok-x"}
    )
    r = adapters.LegacyOdysseusCaptionAdapter().draft({"label": "Hero"})
    assert r.ok
    assert r.capability is Capability.CONTENT
    assert r.output == {"caption": "A bright plate."}
    assert r.model == "grok-x"
    assert r.review is ReviewRequirement.HUMAN_REVIEW


def test_caption_adapter_provider_error_is_non_mutating(monkeypatch):
    monkeypatch.setattr(caption_ai, "is_enabled", lambda: True)

    def boom(ctx):
        raise caption_ai.CaptionDraftError("Odysseus returned an empty draft")

    monkeypatch.setattr(caption_ai, "draft_caption", boom)
    r = adapters.LegacyOdysseusCaptionAdapter().draft({"label": "Hero"})
    assert r.status is ResultStatus.PROVIDER_ERROR
    assert r.output is None
    assert "empty draft" in r.error


def test_caption_adapter_disabled(monkeypatch):
    monkeypatch.setattr(caption_ai, "is_enabled", lambda: False)
    r = adapters.LegacyOdysseusCaptionAdapter().draft({"label": "Hero"})
    assert r.status is ResultStatus.DISABLED


def test_dionysus_pack_adapter_ok(monkeypatch):
    monkeypatch.setattr(platekit, "is_enabled", lambda: True)
    monkeypatch.setattr(
        platekit,
        "packs_for_client",
        lambda client, include_drafts=False: {
            "status": "ok",
            "slug": "blue-plate",
            "packs": [{"id": 1}],
        },
    )
    r = adapters.LegacyDionysusPackAdapter().packs({"name": "x"})
    assert r.ok
    assert r.output["slug"] == "blue-plate"
    assert r.output["packs"] == [{"id": 1}]


def test_dionysus_pack_adapter_error_status(monkeypatch):
    monkeypatch.setattr(platekit, "is_enabled", lambda: True)
    monkeypatch.setattr(
        platekit,
        "packs_for_client",
        lambda client, include_drafts=False: {
            "status": "not_found",
            "slug": "x",
            "message": "No Platekit org 'x'",
            "packs": [],
        },
    )
    r = adapters.LegacyDionysusPackAdapter().packs({"name": "x"})
    assert r.status is ResultStatus.PROVIDER_ERROR
    assert "No Platekit org" in r.error


def test_dionysus_pack_adapter_disabled(monkeypatch):
    monkeypatch.setattr(platekit, "is_enabled", lambda: False)
    r = adapters.LegacyDionysusPackAdapter().packs({"name": "x"})
    assert r.status is ResultStatus.DISABLED


# ── mock adapters ───────────────────────────────────────────────────────────


def test_mock_adapters_are_deterministic():
    v = mocks.MockVisionAdapter().analyze_gallery(5)
    assert v.ok and v.output["run_id"] == 1005 and v.model == "mock-vision-1"
    assert mocks.MockVisionAdapter().analyze_gallery(5).output == v.output

    o = mocks.MockOffersAdapter().recommend_gallery(7)
    assert o.ok and o.output["run_id"] == 2007 and o.output["bundle_count"] == 3

    c = mocks.MockCaptionAdapter().draft({"label": "Hero"})
    assert c.ok and c.output["caption"] == "[mock caption] Hero" and c.tokens == 12


def test_mock_adapter_disabled():
    assert mocks.MockVisionAdapter(enabled=False).analyze_gallery(1).status is ResultStatus.DISABLED


def test_failing_adapter_is_non_ok_for_every_capability():
    for cap, call in (
        (Capability.VISION, lambda a: a.analyze_gallery(1)),
        (Capability.OFFERS, lambda a: a.recommend_gallery(1)),
        (Capability.CONTENT, lambda a: a.draft({})),
    ):
        result = call(mocks.FailingAdapter(cap))
        assert result.status is ResultStatus.PROVIDER_ERROR
        assert result.ok is False


# ── registry seam ───────────────────────────────────────────────────────────


def test_registry_defaults_to_legacy():
    registry.reset()
    assert isinstance(registry.resolve(Capability.VISION), adapters.LegacyArgusVisionAdapter)
    assert isinstance(registry.resolve(Capability.OFFERS), adapters.LegacyPlutusOffersAdapter)
    assert isinstance(registry.resolve(Capability.CONTENT), adapters.LegacyOdysseusCaptionAdapter)


def test_registry_use_overrides_then_restores():
    registry.reset()
    mock = mocks.MockVisionAdapter()
    with providers.use(Capability.VISION, mock):
        assert registry.resolve(Capability.VISION) is mock
    # restored to legacy after the block
    assert isinstance(registry.resolve(Capability.VISION), adapters.LegacyArgusVisionAdapter)


def test_registry_reset_clears_overrides():
    registry.reset()
    registry._overrides[Capability.OFFERS] = mocks.MockOffersAdapter()
    registry.reset()
    assert isinstance(registry.resolve(Capability.OFFERS), adapters.LegacyPlutusOffersAdapter)
