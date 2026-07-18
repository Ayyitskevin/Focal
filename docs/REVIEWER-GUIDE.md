# Reviewer guide

This guide is for a software peer evaluating Mise without needing private state,
vendor accounts, a signing identity, or product-history context.

## Fifteen-minute path

### 1. See the product surface

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python scripts/reviewer_demo.py
```

Open `http://localhost:8400/demo`, `/pricing`, and `/healthz`. The launcher binds
only to `127.0.0.1`, removes all inherited `MISE_*` configuration, ignores `.env`,
uses a temporary data directory, and deletes that directory after `Ctrl+C`.

The tour is intentionally static. Do **not** run `scripts/seed_demo_tenant.py` or
point any command at hosted state; [issue #185](https://github.com/Ayyitskevin/mise/issues/185)
holds that separate reviewer-account path.

### 2. Orient in the architecture

Read [ARCHITECTURE.md](ARCHITECTURE.md), then inspect:

- `app/main.py` for application composition and middleware order;
- `app/saas.py` for host-first tenant selection and control-plane behavior;
- `app/mobile_api.py` and `app/mobile_auth.py` for the native trust boundary;
- `ios/Mise/` for feature models, repositories, networking, and session handling;
- `docs/adr/` for the decision history rather than just the current implementation.

### 3. Reproduce the gates

```bash
source .venv/bin/activate
python -m pytest tests/ -m unit
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  MISE_ENV_FILE=/nonexistent python -m pytest tests/ -m "not unit"
ruff check .
ruff format --check .
```

The second partition needs `ffmpeg`. iOS source changes are validated by the
separate macOS `build-test` workflow because Linux cannot run the Xcode gate.

### 4. Pick a review seam

| Review interest | Suggested path | Invariant to challenge |
| --- | --- | --- |
| Multi-tenancy | `app/saas.py`, tenant tests | The request host selects state before domain code; client input cannot cross tenants. |
| Native security | `app/mobile_auth.py`, iOS Security/Networking | Refresh rotation, revocation, scope, and tenant origin stay coherent under concurrency. |
| Retry correctness | `app/mobile_idempotency.py`, `app/booking_workflow.py` | A timeout or app restart cannot silently duplicate a consequential command. |
| Payments | `app/public/pay.py`, payment ADRs/tests | Signatures, replay guards, and expected amounts protect invoice truth. |
| Product architecture | `app/`, `templates/`, `ios/Mise/Features/` | Domain seams remain legible without a premature service layer. |
| AI boundary | `app/providers/`, `AI-DEVELOPMENT.md` | Optional adapters fail dormant and require human review before persistence. |

## Questions worth asking

- Does instance-per-tenant SQLite create an operational burden the product price
  cannot support, and are recovery semantics strong enough?
- Is the modular monolith still discoverable as domain count grows, or are a few
  high-churn modules becoming coordination bottlenecks?
- Are API idempotency and workflow-state mechanisms consistent, or are there two
  subtly different retry models?
- Which native journeys materially improve a photographer's day instead of
  duplicating a responsive web UI?
- Are the ADRs recording durable decisions, or accumulating plans that no longer
  match code?
- Which tests prove trust boundaries, and which only preserve current output?

## Current holds to keep in frame

Do not review the repository as if it claims production or App Store readiness.
The most material open items are:

- [#182](https://github.com/Ayyitskevin/mise/issues/182): native product scope is
  narrower than the pocket-OS direction;
- [#181](https://github.com/Ayyitskevin/mise/issues/181): missing hosted tenant
  storage can be recreated instead of failing loud;
- [#180](https://github.com/Ayyitskevin/mise/issues/180): App Store purchase/IAP
  distribution strategy is undecided;
- [#179](https://github.com/Ayyitskevin/mise/issues/179): privacy manifest and
  label evidence are incomplete;
- [#185](https://github.com/Ayyitskevin/mise/issues/185): the reviewer-account
  provisioner needs a new durable identity and lifecycle design.

The narrower issue tracker contains additional native correctness findings. These
are evidence of an active review process, not hidden completion claims.

## Review hygiene

- Use only disposable data you create.
- Never paste secrets into issues, test output, or AI tools.
- Report vulnerabilities privately through [SECURITY.md](../SECURITY.md).
- Separate correctness, product-scope, and style feedback.
- Include a minimal reproduction or a testable invariant with each material
  finding.
