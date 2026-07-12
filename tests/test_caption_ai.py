"""Contract tests for the caption provider trust boundary."""

import json
import urllib.error

import pytest

from app import caption_ai, config

_URL = "https://captions.internal.test/draft"
_TOKEN = "test-provider-token"
_IDEMPOTENCY_KEY = "123e4567-e89b-12d3-a456-426614174000"


class _Response:
    def __init__(self, raw):
        self.raw = raw
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        if isinstance(self.raw, bytes) and size >= 0:
            return self.raw[:size]
        return self.raw


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", _URL)
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_TOKEN", _TOKEN)
    monkeypatch.setattr(config, "ODYSSEUS_TIMEOUT", 7.5)


def _json_response(caption="draft", model="model"):
    return json.dumps({"caption": caption, "model": model}).encode("utf-8")


def _install_response(monkeypatch, raw):
    response = _Response(raw)
    captured = {}

    def fake_open_provider(request, *, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(caption_ai, "_open_provider", fake_open_provider)
    return captured, response


def test_draft_caption_normalizes_and_sends_bounded_contract(monkeypatch):
    raw = _json_response("  Cafe\u0301 👨‍👩‍👧\n\tready  ", " mo\u0301del ")
    captured, response = _install_response(monkeypatch, raw)

    result = caption_ai.draft_caption(
        {
            "label": "Cafe\u0301",
            "note": "line one\n\tline two",
            "client": "Client",
            "period": "2026-07",
            "plan_title": "Plan",
            "instruction": "Keep 👩‍💻 in the copy",
        },
        idempotency_key=_IDEMPOTENCY_KEY,
    )

    assert result == {"caption": "Café 👨‍👩‍👧\n\tready", "model": "módel"}
    request = captured["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == _URL
    assert request.method == "POST"
    assert captured["timeout"] == 7.5
    assert headers == {
        "accept": "application/json",
        "authorization": f"Bearer {_TOKEN}",
        "content-type": "application/json",
        "idempotency-key": _IDEMPOTENCY_KEY,
    }
    assert json.loads(request.data) == {
        "client": "Client",
        "instruction": "Keep 👩‍💻 in the copy",
        "label": "Café",
        "note": "line one\n\tline two",
        "period": "2026-07",
        "plan_title": "Plan",
    }
    assert len(request.data) <= 32 * 1024
    assert response.read_sizes == [64 * 1024 + 1]


def test_draft_caption_omits_optional_header_and_preserves_unknown_model(monkeypatch):
    captured, _ = _install_response(monkeypatch, _json_response(" draft ", " \t"))

    result = caption_ai.draft_caption({"label": "label"})

    headers = {key.lower(): value for key, value in captured["request"].header_items()}
    assert "idempotency-key" not in headers
    assert result == {"caption": "draft", "model": "unknown"}


@pytest.mark.parametrize(
    "context",
    [
        [],
        {1: "value"},
        {"unknown": "value"},
        {"label": 1},
        {"label": "x" * 501},
        {"note": "x" * 4_001},
        {"client": "x" * 501},
        {"period": "x" * 33},
        {"plan_title": "x" * 1_001},
        {"instruction": "x" * 4_001},
        {"label": "unsafe\x00text"},
        {"label": "unsafe\u202etext"},
        {"label": "unsafe\u2060text"},
        {"label": "unsafe\ud800text"},
        {"note": "😀" * 4_000, "instruction": "😀" * 4_000, "label": "😀" * 500},
    ],
)
def test_draft_caption_rejects_invalid_context_before_network(monkeypatch, context):
    monkeypatch.setattr(
        caption_ai,
        "_open_provider",
        lambda *args, **kwargs: pytest.fail("network must not be called"),
    )

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption(context)

    assert str(exc_info.value) == "AI drafting request is invalid"
    assert exc_info.value.provider_attempted is False


@pytest.mark.parametrize(
    "key",
    [
        123,
        "not-a-uuid",
        " 123e4567-e89b-12d3-a456-426614174000",
        "{123e4567-e89b-12d3-a456-426614174000}",
        "123E4567-E89B-12D3-A456-426614174000",
    ],
)
def test_draft_caption_requires_canonical_uuid_idempotency_key(monkeypatch, key):
    monkeypatch.setattr(
        caption_ai,
        "_open_provider",
        lambda *args, **kwargs: pytest.fail("network must not be called"),
    )

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption({"label": "label"}, idempotency_key=key)

    assert str(exc_info.value) == "AI drafting request is invalid"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff",
        b"[]",
        b'{"caption":"one","caption":"two","model":"m"}',
        json.dumps({"caption": "draft"}).encode(),
        json.dumps({"caption": "draft", "model": "m", "extra": "x"}).encode(),
        json.dumps({"caption": 1, "model": "m"}).encode(),
        json.dumps({"caption": "draft", "model": 1}).encode(),
        _json_response(" \n\t ", "m"),
        _json_response("unsafe\x00caption", "m"),
        _json_response("unsafe\u202ecaption", "m"),
        _json_response("draft", "unsafe\u2060model"),
        _json_response("x" * 10_001, "m"),
        _json_response("😀" * 5_121, "m"),
        _json_response("draft", "m" * 201),
        b"{" + b"x" * (64 * 1024),
        "not bytes",
    ],
)
def test_draft_caption_rejects_malformed_or_oversized_response(monkeypatch, raw):
    _install_response(monkeypatch, raw)

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption({"label": "label"})

    assert str(exc_info.value) == "AI drafting provider returned an invalid response"
    assert exc_info.value.provider_attempted is True


def test_draft_caption_maps_deep_json_to_safe_invalid_response(monkeypatch):
    raw = b'{"caption":' + b"[" * 10_000 + b"0" + b"]" * 10_000 + b',"model":"m"}'
    _install_response(monkeypatch, raw)

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption({"label": "label"})

    assert str(exc_info.value) == "AI drafting provider returned an invalid response"


def test_draft_caption_accepts_exact_output_boundaries(monkeypatch):
    caption = "😀" * 5_120
    model = "m" * 200
    _install_response(monkeypatch, _json_response(caption, model))

    assert caption_ai.draft_caption({"label": "label"}) == {
        "caption": caption,
        "model": model,
    }


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(_URL, 502, "secret-http-reason", {}, None),
        urllib.error.URLError("secret-url-reason"),
        TimeoutError("secret-timeout-reason"),
        RuntimeError("secret-runtime-reason"),
    ],
)
def test_draft_caption_maps_connection_failures_to_safe_error(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(caption_ai, "_open_provider", fail)

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption({"label": "label"})

    assert str(exc_info.value) == "AI drafting provider is unavailable"
    assert "secret" not in str(exc_info.value)
    assert exc_info.value.provider_attempted is True


def test_draft_caption_maps_read_failure_to_safe_error(monkeypatch):
    class _BrokenResponse(_Response):
        def read(self, size=-1):
            raise RuntimeError("secret-provider-body")

    monkeypatch.setattr(
        caption_ai,
        "_open_provider",
        lambda *args, **kwargs: _BrokenResponse(b""),
    )

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption({"label": "label"})

    assert str(exc_info.value) == "AI drafting provider is unavailable"
    assert "secret" not in str(exc_info.value)


def test_draft_caption_disabled_semantics_win_without_network(monkeypatch):
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_TOKEN", "")
    monkeypatch.setattr(
        caption_ai,
        "_open_provider",
        lambda *args, **kwargs: pytest.fail("network must not be called"),
    )

    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption(
            {"unknown": object()},
            idempotency_key="not-a-uuid",
        )

    assert str(exc_info.value) == "AI drafting is not configured"
    assert exc_info.value.provider_attempted is False


@pytest.mark.parametrize(
    "url",
    [
        "http://captions.internal.test/draft",
        "ftp://captions.internal.test/draft",
        "https://user:secret@captions.internal.test/draft",
        "https://captions.internal.test/draft#fragment",
        "https://captions.internal.test:invalid/draft",
        "//captions.internal.test/draft",
    ],
)
def test_draft_caption_rejects_untrusted_provider_url_before_network(monkeypatch, url):
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", url)
    monkeypatch.setattr(
        caption_ai,
        "_open_provider",
        lambda *args, **kwargs: pytest.fail("network must not be called"),
    )

    assert caption_ai.is_enabled() is False
    with pytest.raises(caption_ai.CaptionDraftError) as exc_info:
        caption_ai.draft_caption({"label": "label"})
    assert str(exc_info.value) == "AI drafting is not configured"


def test_provider_opener_installs_redirect_blocker(monkeypatch):
    captured = {}
    response = _Response(_json_response())

    class _Opener:
        def open(self, request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(caption_ai.urllib.request, "build_opener", build_opener)
    request = caption_ai.urllib.request.Request(_URL)

    assert caption_ai._open_provider(request, timeout=3) is response
    assert captured["timeout"] == 3
    assert any(
        isinstance(handler, caption_ai._NoRedirectHandler) for handler in captured["handlers"]
    )
    handler = next(
        handler
        for handler in captured["handlers"]
        if isinstance(handler, caption_ai._NoRedirectHandler)
    )
    assert handler.redirect_request(request, None, 302, "redirect", {}, "https://evil.test") is None


def test_success_log_never_contains_provider_model(monkeypatch, caplog):
    model = "SECRET_PROVIDER_MODEL_VALUE"
    _install_response(monkeypatch, _json_response("safe draft", model))

    with caplog.at_level("INFO", logger="mise.caption_ai"):
        result = caption_ai.draft_caption({"label": "label"})

    assert result["model"] == model
    assert model not in caplog.text
