# ADR 0046 - Company billing readiness

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0034, 0041, 0043)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

The company command view now carries the commercial relationship from project pipeline through AR
follow-up and sent-message history. One remaining operational gap sits before sending: a company or
venue can have a draft or past-due invoice while its billing/AP profile is incomplete, so the
operator has to remember whether invoices are going to a real AP address, a fallback client email,
or nowhere useful.

## Decision

Add a read-only billing readiness section to the company command view.

- Roll up existing `clients` billing fields across the root company and child venues: AP email,
  billing address, tax ID, and invoice recipient fallback.
- Show per-account gaps and whether invoice delivery would use AP email, client email fallback, or
  no recipient.
- Add a top-ranked derived action when a draft or past-due invoice belongs to an account without
  an AP email; link it back to the billing readiness section.
- Keep the slice read-only. It creates no schema, task, send, invoice mutation, or automation.

## Consequences

- The operator can see AP readiness before issuing drafts or chasing overdue AR.
- The Studio Activity commercial queue now prioritizes fixing missing AP email when that gap would
  block clean invoice follow-up.
- The tradeoff is that readiness is only as complete as the existing client billing fields; fixing
  a row still happens on the client detail surface.
