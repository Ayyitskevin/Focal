import asyncio

from starlette.requests import Request
from starlette.responses import Response

from app import config
from app.main import common_headers


def _request(path: str = "/healthz") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [(b"host", b"mise.test"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_common_headers_add_permissions_policy_and_hsts_when_secure(monkeypatch):
    async def call_next(request):
        return Response("ok")

    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    response = asyncio.run(common_headers(_request(), call_next))

    assert response.headers["permissions-policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    assert "strict-transport-security" not in response.headers

    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    secure = asyncio.run(common_headers(_request(), call_next))

    assert secure.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
