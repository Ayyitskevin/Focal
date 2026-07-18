# Contributing to Mise

Mise is a pre-release product-incubation repository. Contributions are welcome,
but a passing test suite is not the same as launch approval: money, tenant data,
authentication, legal documents, schema, and deployment changes receive a stricter
human review.

## Before writing code

1. Read [README.md](README.md) for the current product truth and known holds.
2. Search open issues and pull requests to avoid duplicating active work.
3. For a substantial change, open or claim an issue and state the intended scope.
4. Read [AGENTS.md](AGENTS.md) if automation or an AI coding agent will participate.

Keep each change to one logical unit. Do not bundle a cleanup, schema change, and
product feature merely because they touch nearby files.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Python 3.12 and `ffmpeg` are required for the full gate. iOS development also
requires a current Xcode, an iOS 17+ simulator, and XcodeGen; see
[ios/README.md](ios/README.md).

## Required verification

Run the same Python partitions as CI:

```bash
python -m pytest tests/ -m unit
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  MISE_ENV_FILE=/nonexistent python -m pytest tests/ -m "not unit"
ruff check .
ruff format --check .
```

Add focused tests for the behavior you changed. In the pull request, record the
exact commands and results; “CI is green” is not enough evidence for a non-trivial
change. iOS changes must also pass the repository's macOS `build-test` workflow.

## Risk boundaries

Always use a pull request and leave the merge to the maintainer when a change
touches any of these boundaries:

- Stripe, invoices, payment state, totals, deposits, or billing lifecycle;
- migrations, table shape, constraints, or data repair;
- authentication, sessions, cookies, CSRF, rate limits, secrets, or tenant choice;
- contracts, signatures, legal copy, licensing, or privacy declarations;
- deployment, backups, restore paths, or production configuration.

Treat an ambiguous change as high-risk. State the failure mode, rollback, and any
manual verification a reviewer must perform.

## Pull requests

Use the checked-in pull request template and include:

- the problem and the intentionally limited scope;
- user/developer impact and any current behavior that remains;
- exact validation evidence;
- rollback instructions;
- risk level and affected trust boundaries;
- linked issue or ADR when the change modifies a durable decision.

Do not commit credentials, customer data, generated databases, signing material,
or real integration endpoints. Tests must remain hermetic and must not make live
vendor calls.

## AI-assisted contributions

AI assistance is allowed and common in this repository. Disclose material use in
the pull request or commit trailers, identify the human/maintainer responsible for
the result, and provide reproducible evidence. Never send secrets or real customer
data to a model. The full policy is in
[docs/AI-DEVELOPMENT.md](docs/AI-DEVELOPMENT.md).

## License of contributions

By submitting a contribution, you agree that it may be distributed under the
repository's [AGPL-3.0-only license](LICENSE). Do not contribute code, media, fonts,
or data that you do not have the right to license on those terms.
