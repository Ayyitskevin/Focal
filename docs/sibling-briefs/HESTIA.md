# Hestia brief — architecture doctrine (NOT a worker)

**Hestia is not a runtime component and must NEVER become a Mise dependency.** It's the
architecture **doctrine** — the north star the ADRs already draw on. So there's no worker to
align and no contract to conform to; the only useful work is keeping the doctrine current with
what the consolidation actually proved. Excluded by design (audit §19.1): multi-tenancy, SaaS
signup, subscription billing, a Hestia runtime dependency, a Hestia DB migration, any code
integration into Mise.

This is a **docs/doctrine** task, not a worker build. Use it anytime — it has no dependency on
the other repos and no bearing on the live cutover.

---

## The prompt (doctrine review & update)

````
You are updating the **Hestia** repo — the architecture DOCTRINE for the studio OS. Hestia is
guidance ONLY: it has no runtime role and must NEVER become a dependency of Mise (no shared
service, no DB migration, no code integration). This is a docs review → update task.

Context: "Mise Solo Studio OS" has consolidated its photography-AI sidecars (vision, offers,
content, albums, products) behind one provider facade, and proved a set of patterns worth
codifying as doctrine. Review Hestia's current doctrine against these and reconcile/extend it so
it stays the north star:

- One product + one data spine; modules over microservices; Mise is the sole transaction
  authority, the engines are disposable workers ("consolidate the chassis, not the engines").
- A provider facade + a normalized result contract ("propose, never mutate"), and a stable
  WORKER CONTRACT that turns each engine into a stateless, contract-true worker: idempotency,
  signed callbacks, provenance + per-call cost, structured-output JSON schemas, /healthz,
  mock-only reproducible CI.
- Strangler migration with an INTERLOCKED cutover seam + feature-flag rollback; never a
  flag-day swap; decommission a service only after a parity gate + an observation period.
- The "dormant foundation" pattern: build a capability's deterministic guards + schema first,
  inert, before arming it — so the safety floor exists before the risk does.
- An append-only provenance ledger feeding cost + operations dashboards.
- The MONEY/RIGHTS boundary: the model proposes, deterministic code validates, a human
  approves; nothing auto-sends, charges, invoices, prints, or publishes; budget caps + consent
  gates are enforced in code, not convention.
- Human-reviewed AI throughout; reversible drafts; least-privilege media access; local-first
  for client media.

Deliverables (docs only):
(a) a reconciliation of the existing doctrine vs what Mise built — what's confirmed, what to
    revise, what's new;
(b) updated/extended doctrine docs capturing the patterns above as reusable, repo-agnostic
    guidance (not Mise-specific implementation detail);
(c) a short "Lessons from the Mise consolidation" section.

Keep Hestia free of any Mise runtime dependency. Develop on a claude/ branch as a draft PR a
human merges. Deliver the reconciliation + plan first; wait for go before large rewrites.
````
