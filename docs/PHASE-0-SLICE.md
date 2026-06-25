# Phase 0 — Foundation slice (implemented on this branch)

> The concrete first implementation slice: exact files, what changed, what deliberately
> did **not**, tests, risks, and rollback. This slice is **green-light**: additive,
> behavior-preserving, no money/schema/security/deploy/contract change.

Branch: `claude/mise-solo-studio-os-s2otpe`. See
[`MISE-CONSOLIDATION-ROADMAP.md`](MISE-CONSOLIDATION-ROADMAP.md) for what comes next.

## Goal

Introduce a small, typed internal **contract/facade for photography-AI capabilities**,
wrap the existing Odysseus / Argus / Plutus / Dionysus calls behind **legacy adapters
without changing their behavior**, add **deterministic mock adapters** and tests, and
prove **legacy behavior is unchanged and failures remain non-mutating** — with **no DB
migration**.

## Exact files

### Added — `app/providers/` (a leaf package nothing in the running app imports)

| File | Purpose |
| --- | --- |
| `app/providers/__init__.py` | Public facade exports (`Capability`, `ProviderResult`, `ResultStatus`, `ReviewRequirement`, the legacy adapters, `resolve`/`use`/`reset`). |
| `app/providers/contracts.py` | Typed contract: `Capability` (VISION/OFFERS/CONTENT), `ResultStatus` (OK/DISABLED/PROVIDER_ERROR/INVALID_RESPONSE), `ReviewRequirement` (NONE/HUMAN_REVIEW/EXPLICIT_COMMIT ≈ approval classes A0–A4), frozen `ProviderResult` dataclass with `ok`, `provenance()`, and `disabled()`/`failure()` factories. No I/O, no DB. |
| `app/providers/adapters.py` | Legacy adapters wrapping the **non-mutating** trigger/read functions: `LegacyArgusVisionAdapter.analyze_gallery` → `argus_analyze.trigger_gallery_analyze`; `LegacyPlutusOffersAdapter.recommend_gallery` → `plutus_recommend.trigger_gallery_recommend`; `LegacyOdysseusCaptionAdapter.draft` → `caption_ai.draft_caption`; `LegacyDionysusPackAdapter.packs` → `platekit.packs_for_client`. Argus/Plutus/Odysseus normalize the return **or the module's typed error** into a `ProviderResult`; the Dionysus pack reader never raises, so its adapter maps the returned status dict (`ok` → OK, `not_configured`/`missing_slug` → DISABLED, else → PROVIDER_ERROR). **No DB writes.** |
| `app/providers/mocks.py` | Deterministic `MockVisionAdapter` / `MockOffersAdapter` / `MockCaptionAdapter` (input-derived fixed outputs) + a `FailingAdapter` usable for any capability. |
| `app/providers/registry.py` | `resolve(capability)` → legacy adapter by default (the production path); `use(capability, adapter)` context manager + `reset()` for test/shadow injection. **The strangler switch point.** |

### Added — test

| File | Purpose |
| --- | --- |
| `tests/test_providers.py` | 30 pure-unit tests (`@pytest.mark.unit`, no DB/network). |

### Changed — none

No existing module, route, template, env var, or migration was modified. `app/main.py`
does **not** import `app/providers/`. Confirmed by diff.

## Why wrapping the *trigger* functions matters

Each legacy module separates the **provider call** (`trigger_*`/`draft_*`, which raises
on failure and writes nothing) from the **business-state write** (`run_for_gallery` /
`apply_callback` / the caller, which records status). The adapters wrap the *former*.
Therefore:

- Exercising an adapter can never mutate a record — provider failure is structurally
  separated from business-state failure (audit §11.3, ADR 0006).
- All existing persistence (`argus_last_*`, `plutus_last_*`, caption writes) stays
  exactly where it is, untouched, so production behavior is unchanged.

## Tests & evidence

Commands (from `AGENTS.md` gates):

```sh
. .venv/bin/activate
python -m pytest tests/test_providers.py -q                       # 30 passed
python -m pytest tests/ --ignore=tests/test_smoke.py -q -m unit    # 66 passed (was 36)
ruff check . && ruff format --check .                              # clean
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  python -m pytest tests/test_smoke.py -q                          # 152 passed, 6 failed*
```

\* The 6 smoke failures are **pre-existing and environmental** in this sandbox: 5 video
tests need `ffmpeg` (absent here) and `test_pin_lockout` is a fresh-DB ordering artifact.
They are present on the baseline **before** this slice and are **unchanged** by it.

What the unit tests prove:

- **Contract:** `ok`/`provenance()`; `disabled()`/`failure()` are non-OK; `failure()`
  rejects an OK status; `provenance()` carries metadata, never the output payload.
- **Legacy parity:** each adapter reproduces the legacy output shape (caption text +
  model, Argus run/job + mode, Plutus run + bundle count/total, Dionysus packs).
- **Failure mapping:** typed errors → `PROVIDER_ERROR`; missing run/job → `INVALID_RESPONSE`;
  unconfigured → `DISABLED`. All non-OK results carry `output=None`.
- **Non-mutating-on-failure** (the audit invariant): drives the *real*
  `trigger_gallery_analyze` **and** `trigger_gallery_recommend` with a timing-out
  `urlopen` and a `db.run` spy — asserts the adapter returns `PROVIDER_ERROR` **and zero
  DB writes**. A companion test proves even a *successful* facade call writes nothing
  (recording is the caller's job). `caption_ai` touches no DB, so its non-mutation is
  structural.
- **Mocks:** deterministic across repeated calls; `FailingAdapter` non-OK for every
  capability.
- **Registry:** defaults to legacy for all three capabilities; `use()` overrides then
  restores (including a nested-override case); `reset()` clears; an autouse fixture
  isolates overrides per test.

## Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| A facade that nothing calls is dead weight | It is the documented strangler seam (steps 1–2); the roadmap wires callers onto it in Phase 1+. Tests keep it honest. |
| Adapter drifts from legacy behavior | Adapters delegate to the *same* legacy functions; parity tests assert output mapping; bundle-meta parse is mirrored inline with a note. |
| Import side effects | `app/providers` is a leaf (imports `argus_analyze`/`plutus_recommend`/`caption_ai`/`platekit` + stdlib); no module imports it back; no DB connection or network at import. |
| Hidden behavior change | Diff shows **only additions**; `app/main.py` unchanged; unit gate +25 green; smoke failures identical to baseline. |

## Rollback

Delete `app/providers/`, `tests/test_providers.py`, and the docs. There is no behavior,
route, env var, or schema to revert — the slice is purely additive and dormant.

## AGENTS.md classification

**Green-light** (new non-money module + new test coverage + docs; no `pay.py`/Stripe,
no `migrations/`, no `app/security.py`/auth/CSRF, no deploy/flow tree, no contracts).
Per the session safety rules it is still developed on the `claude/` branch and shipped
as a **draft PR** for human review rather than self-merged.
