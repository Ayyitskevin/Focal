"""Unit tests for the internal vision challenger (Qwen3-VL, OpenAI-compatible).

Pure unit (no network, no real files): the endpoint call (_analyze) and image gathering
are monkeypatched, so we assert the dormant-by-env gate, the OK/failure mapping, and that
the registry auto-registers the challenger only when it is configured.
"""

from pathlib import Path

import pytest

from app import config
from app.providers import Capability, ResultStatus, registry
from app.providers import vision_challenger as vc
from app.providers.vision_challenger import InternalVisionChallengerAdapter

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset()
    yield
    registry.reset()


def test_disabled_when_url_unset(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "")
    a = InternalVisionChallengerAdapter()
    assert a.is_enabled() is False
    assert a.analyze_gallery(1).status is ResultStatus.DISABLED


def test_enabled_when_url_set(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    assert InternalVisionChallengerAdapter().is_enabled() is True


def test_ok_mapping(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(config, "VISION_CHALLENGER_MODEL", "qwen3-vl:32b")
    monkeypatch.setattr(
        vc, "_gather_image_paths", lambda gid, limit: [Path("a.jpg"), Path("b.jpg")]
    )
    monkeypatch.setattr(
        InternalVisionChallengerAdapter,
        "_analyze",
        lambda self, paths: {"choices": [{"message": {"content": "  keywords: plated dish  "}}]},
    )
    r = InternalVisionChallengerAdapter().analyze_gallery(5)
    assert r.ok
    assert r.capability is Capability.VISION and r.provider == "qwen3-vl"
    assert r.model == "qwen3-vl:32b"
    assert r.output["image_count"] == 2
    assert r.output["analysis"] == "keywords: plated dish"
    assert r.cost_usd == 0.0 and r.latency_ms is not None


def test_no_images_is_invalid(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(vc, "_gather_image_paths", lambda gid, limit: [])
    r = InternalVisionChallengerAdapter().analyze_gallery(5)
    assert r.status is ResultStatus.INVALID_RESPONSE
    assert r.output is None


def test_provider_error_is_non_raising(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(vc, "_gather_image_paths", lambda gid, limit: [Path("a.jpg")])

    def boom(self, paths):
        raise TimeoutError("timed out")

    monkeypatch.setattr(InternalVisionChallengerAdapter, "_analyze", boom)
    r = InternalVisionChallengerAdapter().analyze_gallery(5)
    assert r.status is ResultStatus.PROVIDER_ERROR
    assert "timed out" in r.error and r.output is None


def test_unexpected_shape_is_invalid(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(vc, "_gather_image_paths", lambda gid, limit: [Path("a.jpg")])
    monkeypatch.setattr(
        InternalVisionChallengerAdapter, "_analyze", lambda self, paths: {"oops": 1}
    )
    r = InternalVisionChallengerAdapter().analyze_gallery(5)
    assert r.status is ResultStatus.INVALID_RESPONSE


def test_registry_autoregisters_only_when_configured(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "")
    assert registry.challenger(Capability.VISION) is None  # dormant

    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    chal = registry.challenger(Capability.VISION)
    assert isinstance(chal, InternalVisionChallengerAdapter)


def test_registry_override_still_wins(monkeypatch):
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    from app.providers.mocks import MockVisionChallengerAdapter

    mock = MockVisionChallengerAdapter()
    with registry.use_challenger(Capability.VISION, mock):
        assert registry.challenger(Capability.VISION) is mock
    # falls back to the configured default after the override block
    assert isinstance(registry.challenger(Capability.VISION), InternalVisionChallengerAdapter)
