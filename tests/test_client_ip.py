"""client_ip behind the shipped proxy topology (ADR 0058).

Before this fix the compose deploy made every request appear as Caddy's bridge
IP: per-IP rate limits and the PIN lockout were effectively global — one abuser
locked out every visitor of every tenant.
"""

import pytest
from starlette.requests import Request

from app import config, security

pytestmark = pytest.mark.unit


def _req(peer: str, **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": (peer, 50000),
        }
    )


def test_public_peer_never_trusts_headers():
    # An internet client spoofing forwarded headers stays itself.
    ip = security.client_ip(
        _req("203.0.113.9", **{"x-forwarded-for": "1.2.3.4", "cf-connecting-ip": "5.6.7.8"})
    )
    assert ip == "203.0.113.9"


def test_compose_bridge_peer_uses_caddy_stamped_forwarded_for():
    # The shipped topology: peer is Caddy's docker-bridge IP, XFF carries the client.
    ip = security.client_ip(_req("172.18.0.5", **{"x-forwarded-for": "198.51.100.7"}))
    assert ip == "198.51.100.7"


def test_client_supplied_forwarded_entries_are_ignored():
    # Caddy appends its own view last; leftmost entries are attacker-controlled.
    ip = security.client_ip(_req("172.18.0.5", **{"x-forwarded-for": "6.6.6.6, 198.51.100.7"}))
    assert ip == "198.51.100.7"


def test_cloudflare_header_wins_when_fronted():
    ip = security.client_ip(
        _req(
            "172.18.0.5",
            **{"cf-connecting-ip": "198.51.100.7", "x-forwarded-for": "10.0.0.9"},
        )
    )
    assert ip == "198.51.100.7"


def test_legacy_cloudflared_on_localhost_unchanged():
    ip = security.client_ip(_req("127.0.0.1", **{"cf-connecting-ip": "198.51.100.7"}))
    assert ip == "198.51.100.7"


def test_trusted_peer_without_headers_returns_peer():
    assert security.client_ip(_req("172.18.0.5")) == "172.18.0.5"


def test_trust_can_be_narrowed_by_env(monkeypatch):
    # MISE_TRUSTED_PROXY_CIDRS="" -> trust no proxy at all; headers ignored everywhere.
    monkeypatch.setattr(config, "TRUSTED_PROXY_NETS", config._parse_proxy_cidrs(""))
    ip = security.client_ip(_req("172.18.0.5", **{"x-forwarded-for": "1.2.3.4"}))
    assert ip == "172.18.0.5"


def test_garbage_cidr_config_fails_safe():
    # A typo'd CIDR is skipped (not fatal) and grants no trust.
    nets = config._parse_proxy_cidrs("not-a-cidr, 10.0.0.0/8")
    assert len(nets) == 1
