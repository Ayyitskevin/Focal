# ADR 0052 — Hosted trust pages (terms, privacy, support)

**Status:** Accepted (trust surface — hosted mode; table stakes for taking money)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

The managed plan charges real money and holds a studio's clients, contracts, and media, but
the marketing surface had no Terms of Service, Privacy Policy, or Support page — table stakes
for a paid SaaS (Stripe expects them, buyers look for them, and a privacy statement is the
natural place to make the "we don't train on your client media" promise explicit).

## Decision

Add three public pages at the platform root — `/terms`, `/privacy`, `/support` — rendered from
one `saas/legal.html` template selected by a `doc` key, with three small route handlers
(`legal_terms`/`legal_privacy`/`legal_support`). They are added to `_platform_path` so the
tenant middleware serves them at the root host instead of redirecting to `/pricing`. Footer
links appear on the marketing home and pricing pages, and the signup form now carries an
explicit "by starting a trial you agree to the Terms and Privacy Policy" line next to the
submit button.

The **support contact** comes from `SAAS_SUPPORT_EMAIL`, falling back to the operator's Gmail
sender so the page is never a dead end; when no address is configured the support page tells
the user to reply to any studio email instead.

The copy is written to match Mise's *actual* architecture rather than generic boilerplate:
physical per-studio isolation, one-click export/delete, Stripe-held card details, client
payments flowing to the studio's own connected account (not Mise's), a dunning grace period,
and an explicit statement that **client media and studio content are never used to train AI
models** — reinforcing §11.4 (model proposes, human approves).

## Consequences

- **The paid product now has the trust surface buyers and Stripe expect** — content pages only:
  three tiny routes, one template, two config-free footer edits, and one config knob. No money
  path, no auth, no schema, no migration.
- **Self-hosted mode is unaffected** — the routes live on the SaaS router (platform root); the
  pages describe the *hosted* service and say so.
- **Green-light change** (no red-light surface), shipped as a reviewed draft PR with tests per
  the branch discipline, not self-applied.

## Alternatives considered

- **Link out to a hosted legal generator / external ToS.** Rejected — an external dependency for
  a page that must always render, and it couldn't make the product-specific promises (isolation,
  no-AI-training, export/delete) that are the actual selling points.
- **A required consent checkbox on signup.** Deferred — a visible agree-by-signing-up line next
  to the button is the common SaaS pattern and keeps the `/start-trial` POST contract unchanged;
  a hard checkbox can be added later if counsel wants clickwrap.

## Operator note

This copy is a solid, product-accurate starting point, **not** legal advice — the operator
should have counsel review the Terms and Privacy Policy before relying on them for a specific
jurisdiction. The wording is deliberately plain so that review is easy.
