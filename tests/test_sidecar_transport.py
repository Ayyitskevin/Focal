"""Cleartext-transport detection for armed AI sidecars (MISE-REVIEW §6.2 / ADR 0069).

`config.insecure_sidecar_endpoints()` is the pure, side-effect-free half of the O4
credential-hygiene work: it reports which armed outbound sidecar endpoints would send a
bearer token (and, for the vision challenger, client-media derivatives) over cleartext
`http://` to a non-loopback host. `app.main`'s lifespan turns the result into a startup
warning; it changes no transport and blocks nothing, so these tests pin the *detection*,
not any enforcement.
"""

import pytest

from app import config

pytestmark = pytest.mark.unit

# Every outbound sidecar URL the detector considers; cleared to a known-empty baseline so
# each test controls exactly the endpoints it arms (real env must not leak into the result).
_SIDECAR_URL_ATTRS = (
    "ARGUS_URL",
    "ODYSSEUS_CAPTION_URL",
    "PLATEKIT_API_BASE",
    "VISION_CHALLENGER_URL",
    "REOPEN_NOTIFY_URL",
    "PRODUCTS_RENDER_URL",
)


@pytest.fixture
def disarmed(monkeypatch):
    for attr in _SIDECAR_URL_ATTRS:
        monkeypatch.setattr(config, attr, "")


def test_nothing_armed_is_empty(disarmed):
    assert config.insecure_sidecar_endpoints() == []


def test_non_loopback_http_is_flagged(disarmed, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://mickey:8010")
    assert config.insecure_sidecar_endpoints() == [("MISE_ARGUS_URL", "http://mickey:8010")]


def test_https_is_not_flagged(disarmed, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "https://mickey:8010")
    assert config.insecure_sidecar_endpoints() == []


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8010",
        "http://127.0.0.1:8010",
        "http://127.0.0.5:8010",
        "http://[::1]:8010",
    ],
)
def test_loopback_http_is_not_flagged(disarmed, monkeypatch, url):
    # A sidecar on the same box never puts cleartext on a network — no warning.
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", url)
    assert config.insecure_sidecar_endpoints() == []


def test_vision_challenger_media_endpoint_flagged(disarmed, monkeypatch):
    # The challenger is the sharpest case: it POSTs base64 client-media derivatives.
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    assert config.insecure_sidecar_endpoints() == [
        ("MISE_VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    ]


def test_only_the_insecure_endpoints_are_returned(disarmed, monkeypatch):
    # A realistic mix: two armed over cleartext LAN, one over https, one on loopback.
    monkeypatch.setattr(config, "ARGUS_URL", "http://mickey:8010")
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", "https://odysseus.example.com/caption")
    monkeypatch.setattr(config, "PLATEKIT_API_BASE", "http://localhost:9000")
    assert config.insecure_sidecar_endpoints() == [
        ("MISE_ARGUS_URL", "http://mickey:8010"),
        ("MISE_VISION_CHALLENGER_URL", "http://mickeybot:11434/v1"),
    ]


@pytest.mark.parametrize(
    "host,loopback",
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("127.0.1.1", True),
        ("::1", True),
        ("[::1]", True),
        ("mickeybot", False),
        ("10.0.0.4", False),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback_host(host, loopback):
    assert config._is_loopback_host(host) is loopback
