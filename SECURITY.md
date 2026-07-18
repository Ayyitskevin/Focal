# Security policy

Mise is pre-release software with no supported production release. Security work
still receives priority because the repository contains authentication, tenant
isolation, client documents, and payment paths intended for future deployment.

## Report privately

Do not open a public issue for a suspected vulnerability. Use GitHub's private
**Report a vulnerability** flow on this repository when it is available. If it is
not available, contact the repository owner privately through the contact method
on their GitHub profile and include “Mise security” in the subject.

Include:

- the affected commit or branch;
- a minimal reproduction and expected versus observed behavior;
- likely impact and prerequisites;
- whether you accessed any data beyond disposable state you created;
- a suggested mitigation, if you have one.

Do not test against systems, studios, accounts, or data you do not own. Never
include credentials, session tokens, private media, or personal data in the report.

## Supported versions

There is no stable release line yet. Only the current default branch is considered
for fixes; older commits and forks are not supported. Deployment is at the
operator's own risk until the repository's documented launch holds are cleared.

## What happens next

The maintainer will acknowledge a credible report when practical, reproduce it in
disposable state, classify the affected trust boundary, and coordinate disclosure
after a fix is available. No bug bounty or response-time guarantee is offered.

The implementation and incident-response model is documented in the
[Mise security playbook](docs/SECURITY.md). Dependency findings are also monitored
by Dependabot and the scheduled `pip-audit` workflow.
