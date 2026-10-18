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

- Royalty payments are disabled by default and require an explicitly configured
  wallet and transport enablement. LNbits is the built-in adapter; the Coinos
  trade wallet is a separate concern.
- Each trade has a separate payment. Superseded batching code/tests are removed.
- A signed invoice is obtained from the fixed beneficiary and its payment hash
  is committed before the wallet submission. Settlement requires a separate
  authenticated outgoing lookup and an amount-matching preimage/hash proof.
- The 72-hour deadline pauses only new orders. Incidents notify the operator's
  administrator locally; the operator must report them to Paul without secrets.
- This integration is verified offline only, not against a funded wallet.
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

The publication review at `90fd69f` inspected 44 current files and 70 ancestor
commits (115 distinct file versions). No live token/private key or tracked user
database, backup or private log was found in the checks performed. Authors use
Paul aliases and the approved public contact email. Historical non-secret admin
and price-endpoint references remain by the owner's decision. Current operator
settings use environment variables; the beneficiary and deadline are fixed.
This is a source/privacy review, not a guarantee of absence or a financial audit.

The payment implementation at `c799209` passed **56 offline tests** locally and
in GitHub Actions on Python 3.11, including 21 new wallet/payment tests. Static
checks also passed. The following documentation-only revision `90fd69f` passed
CI as well: [offline tests](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39131921299)
and [static checks](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39131917144).
No live royalty transfer or independent deployment has been tested here.

For deployment, follow [OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md). Real-payment
validation with Paul, or independent verified payment when he is unavailable,
is an operator requirement. A payment with unknown outcome must not be repeated
blindly. Database rollback/loss requires wallet-history reconciliation before
resuming the worker; exactly-once behavior after lost database state is not
claimed. See [RELEASE_GATE.md](docs/RELEASE_GATE.md) for the source-release boundary.
