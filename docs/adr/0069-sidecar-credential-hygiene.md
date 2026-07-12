# ADR 0069 — Sidecar credential hygiene: rotation policy + cleartext transport

**Status:** Proposed
**Date:** 2026-07-12
**Deciders:** Kevin (owner), security review
**Related:** `docs/MISE-REVIEW.md` §6 (source flags), ADR 0068 (consolidation — subsumes
most of this), ADR 0065 (security slice 5 — rotation/eviction precedent), `docs/SECURITY.md`

## Context

`docs/MISE-REVIEW.md` §6 routed three security-adjacent flags to a review. This ADR is
that review's disposition: each flag is **remediated**, **scheduled** (behind ADR 0068's
consolidation, which removes the surface entirely), or **accepted with rationale**. It is
the O4 deliverable in `docs/HANDOFF-QUEUE.md`.

The three flags, grounded in the current code:

**Flag 1 — long-lived static bearer tokens, no rotation story.** Mise authenticates to its
sidecars with a single static bearer read once from env at import, on both sides:

| Direction | Token | Where |
| --- | --- | --- |
| Mise → Odysseus (captions) | `MISE_ODYSSEUS_CAPTION_TOKEN` | `app/caption_ai.py:50` |
| Mise → Argus (vision) | `MISE_ARGUS_TOKEN` | `app/argus_analyze.py:174` |
| Mise → Dionysus/Platekit (packs) | `MISE_PLATEKIT_API_TOKEN` | `app/platekit.py:129,207` |
| Mise → vision challenger | `MISE_VISION_CHALLENGER_TOKEN` | `app/providers/vision_challenger.py:73` |
| Mise → Odysseus (reopen notify) | `MISE_REOPEN_NOTIFY_TOKEN` | `app/config.py` (dormant) |
| Argus → Mise (`GET /api/galleries`) | `MISE_ARGUS_TOKEN` | `app/security.py:360` |
| Odysseus → Mise (`GET /api/shots`) | `MISE_SHOTS_TOKEN` | `app/security.py:373` |

The two **inbound** gates already compare in constant time (`secrets.compare_digest`,
`app/security.py:369,384`) and fail *disarmed* (503) when unset — that part is sound. The
gap is lifecycle: there is no versioning, no expiry, no rotation procedure in the runbook,
and rotation requires editing env + restarting on both peers in lock-step (no overlap
window), so in practice the tokens never rotate.

**Flag 2 — plain-`http://` endpoints carrying client-media derivatives.** No code anywhere
validates the scheme of a sidecar URL; every adapter passes the env value straight to
`urllib.request.urlopen`. The documented defaults are cleartext LAN hosts —
`MISE_ARGUS_URL=http://mickey:8010`, `MISE_VISION_CHALLENGER_URL=http://mickeybot:11434/v1`
(`.env.example`). If armed as documented:

- the **vision challenger** POSTs base64 **client-media derivatives** (downsized web JPEGs)
  plus an optional bearer, in cleartext, to a non-loopback host
  (`app/providers/vision_challenger.py:60–81`);
- **Argus / Odysseus / Platekit** POST a bearer token (and caption context) in cleartext.
  They don't send media on the wire — Argus resolves originals via its own
  `ARGUS_MISE_MEDIA_ROOT` — but the token itself is exposed.

**Flag 3 — stale post-075 homelab config in `.env.example`.** **Already remediated.** The
`MISE_ALBUM_CHALLENGER_URL` block (`.env.example:138` at review time) was removed by queue
item S2; `grep ALBUM_CHALLENGER .env.example` is now empty, and no live code reads it. The
remaining `mickey`/`mickeybot` hosts in `.env.example` are *commented examples* for
live-but-dormant capabilities (Argus, the vision challenger), not dead config — they are
addressed by flag 2's transport note and by ADR 0068's per-capability config deprecation,
not by deletion.

**Relationship to ADR 0068.** Consolidation is the real fix. When each capability becomes
in-process Python or a direct hosted-vendor API call, the per-sidecar bearer tokens and the
plain-`http` LAN hops both disappear: transport becomes vendor TLS, and secrets collapse to
a small set of hosted-API keys (ADR 0068 §Consequences). O4 therefore does **not** build a
bespoke rotation system for sidecar tokens that 0068 is deleting — it sets the interim
policy and adds cheap, non-auth visibility, and defers the auth-touching mechanism.

## Decision

**1. Transport requirement.** An armed sidecar endpoint on a non-loopback host **must** use
`https://`. Cleartext `http://` is permitted **only** to a loopback host (same box, no
network hop). This is stated as policy now and enforced by code at the consolidation cutover
(hosted vendors are TLS by construction); until then it is surfaced, not enforced (below),
because hard-failing a live path is itself a red-light change and Argus is a production
default today.

**2. Rotation policy (interim, until 0068 removes the surface).**
- Sidecar bearer tokens are operator-provisioned secrets and rotate on the **ADR 0065 /
  `docs/SECURITY.md` cadence** (on suspected exposure; otherwise on the standard interval),
  the same as the other env secrets — no new bespoke store.
- Rotation is a **runbook procedure**, added to `docs/SECURITY.md`: because each token is
  symmetric between exactly two peers, rotate by (a) provisioning the new token on the
  *inbound* side alongside the old within a short dual-accept window where feasible, or (b)
  accepting a brief coordinated-restart gap where not. The inbound gates already fail
  disarmed, so a misconfigured rotation degrades to 503 (feature dormant), never to an open
  endpoint.
- No token is ever logged. The startup visibility below logs endpoint **labels and
  scheme/host only**, never token values (ADR 0065 no-secrets-in-logs).

**3. What ships in this ADR's PR (safe, non-auth).**
- A **startup cleartext-transport warning**: `config.insecure_sidecar_endpoints()` (pure,
  side-effect-free) reports each armed outbound endpoint that is `http://` to a non-loopback
  host; `app/main.py`'s lifespan logs one `WARNING` per such endpoint at boot. It changes no
  transport, blocks nothing, and cannot disarm a capability — it only makes flag 2 visible to
  the operator instead of silent. Unit-tested in `tests/test_sidecar_transport.py`.
- This ADR + `docs/SECURITY.md` rotation procedure + `.env.example` transport note.

**4. What is deferred as its own red-light PR (auth-touching).**
- **Hard scheme enforcement** (refuse to arm / hard-fail on cleartext non-loopback) — a
  behavior change on the live Argus path; ship only with Kevin's sign-off, or fold into the
  0068 cutover where the endpoint becomes a TLS vendor anyway.
- **Any rotation *mechanism* beyond procedure** (token versioning / dual-accept columns) —
  only if 0068 slips and a sidecar must live on for an extended period. Default: don't build
  it; let consolidation delete the surface.

## Disposition summary

| Flag | Disposition | Basis |
| --- | --- | --- |
| 1 — static tokens, no rotation | **Scheduled** — policy + runbook now; mechanism deferred | Subsumed by ADR 0068; inbound gates already constant-time + fail-disarmed |
| 2 — cleartext client-media transport | **Partially remediated** — startup warning now; hard enforcement deferred (red-light) | Can't hard-fail the live Argus default without sign-off; TLS lands at 0068 cutover |
| 3 — stale `.env.example` homelab config | **Remediated** (S2) | `MISE_ALBUM_CHALLENGER_URL` removed; grep clean; no live reader |

## Consequences

- An operator who arms a sidecar over cleartext to a LAN/tailnet host now gets a boot-time
  warning naming the env var and (for the challenger) the client-media exposure — a real
  signal where there was silence. Loopback and https endpoints stay quiet.
- No live behavior changes: no request path, auth check, or transport is altered by this PR.
  The two genuinely auth-touching fixes are named, scoped, and left for reviewed red-light
  PRs, honoring "review and plan; auth-touching ships as red-light" (CLAUDE.md, O4).
- The rotation story now exists in `docs/SECURITY.md` where an operator will find it, instead
  of being absent. It is deliberately lightweight because ADR 0068 is expected to delete the
  tokens it governs.

## Alternatives considered

- **Hard-fail on cleartext non-loopback now.** Rejected as the default: Argus ships as a
  cleartext-LAN production default today, so a hard fail is a live-path behavior change —
  itself red-light, and premature when 0068 will make the point moot. Offered instead as a
  named, sign-off-gated follow-up.
- **Build token rotation with versioning/overlap now.** Rejected: it hardens a surface ADR
  0068 is removing. Interim procedure + fail-disarmed inbound gates cover the residual risk;
  revisit only if consolidation slips.
- **Move sidecar tokens into a secrets manager.** Rejected for this niche: single-operator,
  single-box deploys; env + `chmod 600` + the 0065 rotation cadence is the established model,
  and the token count is *shrinking* under 0068, not growing.
