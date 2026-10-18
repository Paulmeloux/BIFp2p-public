# Security policy

## Distribution and original service

This source-available distribution is a **development preview**.
Do not deploy it with real funds until the operator has reviewed its security
and verified the relevant trade and payment paths.

Paul reports that the original BIFp2p Telegram service operates successfully
and that identified issues there have been addressed. That report is not a
security audit of this branch or a guarantee for independent deployments.

## Responsible disclosure

Send vulnerability reports privately to **paulmeloux@proton.me**.
Maintainer identity: https://github.com/Paulmeloux

Do not open public issues containing exploitable vulnerabilities, credentials,
private transaction details or personal data. Do not test against live services
or third-party funds without authorization. Redact reports and never email
tokens, seeds, private keys or user banking/identity information.

Payment faults, delays and unresolved royalty outcomes also go to that email.
**Successful royalty payments do not require email notifications.**

## Current implementation limits

- Royalty payment transport is disabled. Production enforcement currently blocks
  new orders; the per-trade wallet interface is not connected to a worker.
- The policy is one separate payment per completed trade. The batching experiment
  is superseded and must not be activated; its code remains pending cleanup.
- The license requires immediate payment, a maximum 72-hour technical-fault
  window and suspension of new orders for overdue obligations. This documentation
  step does not implement that runtime behavior.
- Unknown payments must remain unresolved until verified; never blindly retry
  or infer success from a submission response.
- Coinos payout-history and response semantics need verification for independent
  deployment, especially after ambiguous failures. Remote idempotency has not
  been established.
- Independently verify escrow, payout, refund, recovery, dispute and operator
  configuration behavior before financial use.

These operational requirements are distinct from releasing source for review.
See [RELEASE_GATE.md](docs/RELEASE_GATE.md).

## Operator precautions

- Keep tokens, seeds, real databases, backups and private logs out of Git.
- Use your own bot, administrator, channel, price endpoint and wallet credentials.
- Restrict database, environment and backup permissions.
- Treat every `UNKNOWN` payment as potentially successful until reconciled.
- Royalty enforcement must not stop existing settlements, refunds or recovery.
- Verify migrations and backup restoration on non-sensitive test data.

## Review and automated checks

The review of code at `805d2cc` inspected 44 current files and 67 ancestor
commits (88 distinct file versions). No live token or private key was found
in the checks performed; this is not a guarantee of absence or a financial
security audit. Historical administrator and price-endpoint references remain
by the owner's decision. Current operator settings use environment variables.

Earlier offline tests passed, but the latest inspected run for that revision,
[39062777713](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39062777713),
compiled successfully and failed **8 of 49 tests**. Documentation changes do
not repair those failures. Recheck CI after implementation changes.
