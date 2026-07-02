# ADR 0059 — Wildcard TLS via Cloudflare fronting

**Status:** Accepted (operator decision — launch Phase 2, slice 3)
**Date:** 2026-07-02
**Deciders:** Kevin (owner — chose Cloudflare fronting over a custom Caddy build)

## Context

Tenant subdomains (`slug.your-domain`) need a `*.your-domain` certificate. The stock
`caddy:2.8` image cannot complete the DNS-01 challenge wildcard issuance requires, so the
shipped deploy served TLS errors on every tenant subdomain — the audit's "fails on first
real signup" finding. Two candidate fixes were put to the operator: front the deploy with
Cloudflare, or maintain a custom Caddy build with a DNS-provider module.

## Decision

**Cloudflare fronting** (operator's choice, as recommended):

- Cloudflare's edge terminates public TLS for the apex and `*.your-domain` with its own
  wildcard certificate (proxied/orange-cloud DNS records).
- The origin serves HTTPS **to Cloudflare only**, with a 15-year **Cloudflare Origin CA
  certificate** (`certs/cloudflare-origin.{pem,key}`, gitignored, mounted read-only into
  Caddy) and the zone in **Full (strict)** mode — encrypted and authenticated end to end,
  no ACME on the origin at all.
- `Caddyfile.cloudflare` ships ready to copy over `Caddyfile`; the compose file now
  mounts `./certs`. The default Caddyfile remains correct for single-domain self-host
  deploys and now points hosted operators at the Cloudflare variant.
- Client IPs remain correct by construction: Cloudflare sends `CF-Connecting-IP`, which
  `security.client_ip` already prefers from trusted proxies (ADR 0058 was designed for
  both TLS outcomes).

## Consequences

- **Tenant subdomains get valid TLS from the first signup**, plus CDN caching and DDoS
  absorption for free — meaningful for a solo-operated host.
- Cloudflare sits in the traffic path (the accepted trade-off; the self-host escape
  hatch remains Cloudflare-free).
- **Custom tenant domains need Cloudflare for SaaS** (Custom Hostnames — free tier, 100
  hostnames) because a plain CNAME to a proxied hostname cannot get valid TLS. Deferred
  to post-beta and documented; beta tenants use their built-in subdomain.
- Deploy-config change only — no app code, no schema. Ships as a reviewed draft PR per
  the red-light deploy rule.

## Alternatives considered

- **Custom Caddy build (xcaddy + DNS provider module).** Rejected by the operator — no
  third party in the traffic path, but a custom image to rebuild on every Caddy release
  and a DNS API token living on the box; more moving parts for a solo host.
- **Cloudflare Flexible mode / plain-HTTP origin.** Rejected — traffic between Cloudflare
  and the origin must be encrypted and authenticated; Full (strict) with an Origin CA
  cert costs nothing extra.
