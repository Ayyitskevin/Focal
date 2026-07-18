# AI-assisted development policy

Mise is openly developed with substantial AI assistance. This page distinguishes
development authorship from the product's optional AI capabilities and defines the
evidence expected before agent-authored work is trusted.

## Accountability

The repository owner is the maintainer and decision-maker. An AI agent may propose,
implement, test, review, or document a change; it cannot accept legal risk, own a
credential, approve a money/schema/security boundary, or substitute for the human
merge decision required by `AGENTS.md`.

Material AI participation should be visible in the pull request description,
commit trailers, or both. Disclosure names the tool/agent when practical and never
includes a private conversation, credential, or customer data.

## Evidence standard

An AI-authored change is held to the same or stronger bar as any other change:

1. identify the exact problem and limit the scope;
2. inspect the current code and repository instructions before editing;
3. add or update tests at the affected trust boundary;
4. run focused checks and the repository gates appropriate to the risk;
5. state what was not tested and why;
6. document rollback and remaining limitations;
7. leave human-gated changes unmerged for the maintainer.

Generated prose is not evidence. A confident explanation cannot replace an
assertion, a reproducible command, a source citation, or a manual artifact from the
environment that actually matters.

## Data and tool boundaries

Agents must not receive or expose:

- production credentials, session tokens, signing keys, or `.env` contents;
- real client names, contact data, contracts, invoices, media, or databases;
- private operational logs containing identifiers;
- third-party data that the maintainer lacks permission to share.

Tests and demos use synthetic, disposable state. External writes—GitHub changes,
Notion records, deployment, email, billing, or other person-directed actions—must
stay inside the user's explicit request and the repository's safety contract.

## Review discipline

AI reviews should prioritize falsifiable correctness over volume. Findings include
the affected behavior, concrete evidence, severity, and smallest safe next step.
Agents should not manufacture issues to appear useful, silently broaden a task, or
declare launch readiness from unit tests alone.

Competing agents coordinate through issues, branches, and draft pull requests. A
stale branch is a hint, not ownership; an open, scoped PR is the reviewable claim.

## Product AI is a separate boundary

Mise's optional AI/media capabilities use adapters and sidecars described in
`ARCHITECTURE.md` and the ADRs. Their product rules are independent of whether an
AI coding agent helped write the repository:

- capability configuration is dormant by default;
- evaluation-only providers cannot serve production paths;
- adapters do not write business state directly;
- outputs require human review before consequential use;
- tests do not make live model calls;
- client data is not used to train models by Mise.

## Provenance is not endorsement

A co-author trailer or AI disclosure says how work was produced, not that it is
correct. Trust comes from review, tests, explicit invariants, and the maintainer's
decision at the relevant risk boundary.
