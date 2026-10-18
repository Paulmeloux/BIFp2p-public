# Independent operator: attribution and per-trade royalty

The governing terms are the [BIFp2p Source-Available License v1.0](../LICENSE).
This guide summarizes them; the license controls if wording differs.

## Original project and contact

- BIFp2p | Bitcoin Is Freedom | Created by Paul.
- Copyright holder and original maintainer: **Paul**.
- Maintainer: https://github.com/Paulmeloux
- Canonical source: https://github.com/Paulmeloux/BIFp2p-public
- Community: https://t.me/BitcoinIsFreedom
- Developers, operators and payment incidents: **paulmeloux@proton.me**.

Keep original attribution in the README, license, source notices and accessible
`/about` command or equivalent view. Identify your own operator and support.
Do not impersonate the original project or claim endorsement.

## One completed trade, one royalty payment

- Amount: `original_amount_sats // 1000`, or 0.1% rounded down to whole sats.
- Base: the original trade principal before operator fees.
- Fixed beneficiary: **bitcoinisfreedombot@coinos.io**.
- Each completed trade has a distinct obligation and a separate payment.
  **No batching.** A zero-satoshi obligation requires no payment.
- Pay from the operator's commission, without an additional trader charge.
  The operator funds any shortfall and payment/network costs.
- Cancelled, failed and refunded trades do not generate completed-trade
  royalties. Never count one successful trade twice. For a later full refund,
  follow the license's trade-specific correction procedure.
- Retain the supplied royalty functions or an equivalent working implementation.
  Do not change the beneficiary or bypass attribution/payment obligations.

## Payment deadlines and incidents

Pay immediately after each trade completes. If a technical fault prevents
payment, the debt remains due and must be resolved within **72 hours of trade
completion**. Notify Paul without undue delay at **paulmeloux@proton.me** of
payment faults, delays or unresolved outcomes.

**No email is required for a successful payment.** Keep successful payment
records locally. The notification requirement is an operator responsibility;
automatic email is not implied or required by this guide.

If an obligation remains unpaid or unresolved after 72 hours, stop creating
and accepting new orders until overdue obligations are resolved. Continue
existing settlements, refunds, disputes and recovery safely. Never block the
return of user funds to enforce royalties.

A timeout is not evidence of failure. Treat `UNKNOWN` as potentially successful,
reconcile first, and never blindly repeat a transfer or mark an unverified
payment `PAID`. The deadline must not cause duplicate payments.

Keep minimal trade-to-payment evidence and provide redacted evidence on
reasonable request. Reports must exclude user identities, bank details,
tokens, seeds and keys.

## Wallet and current code

An operator may use any suitable Lightning wallet; it need not be Coinos.
Coinos escrow for trades is separate from the royalty-paying wallet.

The current public branch calculates and records obligations. The per-trade
wallet interface is a prototype, not an integrated payment worker.
`ROYALTY_PAYMENT_TRANSPORT_ENABLED = False` and `PRODUCTION_ENFORCED`
currently blocks new orders. The license's 72-hour rule and incident procedure
are requirements to implement and verify, not completed runtime features.

The former batching experiment is superseded. Its remaining code and tests
are not a supported payment path and must not be enabled or integrated.
They will be addressed in the separate implementation step.

The original private production service, its wallet, credentials and server
are outside this distribution's change scope. This document does not
authorize live transfers, deployment, merging or publication.
