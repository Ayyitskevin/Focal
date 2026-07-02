# ADR 0058 — Proxy-aware client IP (per-IP protections actually per-IP)

**Status:** Accepted (launch Phase 2, slice 2)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

`client_ip()` returned the TCP peer, honoring `CF-Connecting-IP` only when the peer was
loopback (the bare-metal cloudflared assumption). In the **shipped compose topology** the
peer of every request is the **Caddy container's bridge IP** — uvicorn's
`--proxy-headers` doesn't rewrite it because Caddy's IP isn't in uvicorn's default trust
list. Consequence: every visitor of every tenant shared one IP, so per-IP rate limits
were global (one abuser throttled everyone), the PIN lockout locked out **all** users
after one attacker's five failures, the signup throttle was meaningless, and audit-log
IPs (contract signatures, proposal views) were wrong.

## Decision

Resolve the client in `client_ip()` itself — one explicit seam, not uvicorn flag
semantics — trusting forwarded headers **only when the peer is one of our own ingress
proxies** (`MISE_TRUSTED_PROXY_CIDRS`, default loopback + RFC1918, which covers the
compose bridge and cloudflared; the app port is never published directly in the shipped
deploys). From a trusted peer, in order:

1. `CF-Connecting-IP` — set by Cloudflare when it fronts the deploy (and the legacy
   cloudflared-on-localhost path, byte-for-byte unchanged);
2. the **rightmost** `X-Forwarded-For` entry — the one our own Caddy stamped. Caddy
   replaces client-supplied XFF unless the sender is a configured trusted proxy, so the
   rightmost entry is not client-forgeable; leftmost entries are attacker-controlled and
   are never used.

A public peer is returned as-is — headers from arbitrary internet clients are never
trusted. A typo'd CIDR in the env is skipped (fail-safe: no trust granted, app stays up).
The design is correct under **both** candidate TLS topologies (Cloudflare fronting or a
DNS-challenge Caddy build), so it deliberately does not wait on that decision.

## Consequences

- Rate limiting, PIN lockout, the signup throttle, and audit-log IPs work per-visitor in
  the shipped deploy, not per-proxy.
- Spoofing is not reintroduced: public peers get no header trust, and only the
  proxy-stamped (rightmost) XFF entry is read from trusted peers.
- Deployments that expose the app directly on a LAN can narrow or empty
  `MISE_TRUSTED_PROXY_CIDRS`; the default is documented in `.env.example`.
- Green-light change; no schema, no money path, single seam with regression tests for
  the spoofing, compose, Cloudflare, legacy, and fail-safe cases.

## Alternatives considered

- **`FORWARDED_ALLOW_IPS="*"` + uvicorn's proxy-headers middleware.** Rejected — uvicorn
  resolves the *leftmost* XFF entry once everything is trusted, which is exactly the
  client-forgeable value; and hiding the trust decision in a CLI flag makes it invisible
  to tests and reviews.
- **Caddy `trusted_proxies` + always trusting XFF app-side.** Rejected — correctness
  would then depend on ingress config the app can't see; the app-side CIDR check keeps
  the invariant enforceable (and testable) in one place.
