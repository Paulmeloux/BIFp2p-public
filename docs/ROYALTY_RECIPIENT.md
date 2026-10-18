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

## Initial payment validation

Before accepting real user trades, test the configured royalty payment flow
with Paul where he is available. If he is unavailable, pay the same fixed
beneficiary independently and verify and retain settlement evidence.
Owner acknowledgement is not required for independently verified payment.
Use operator-owned funds; the test must exercise the configured adapter and
lookup. A manual payment alone does not validate an untested adapter.
Do not count a standalone test payment as credit for future trades, or pay
an existing obligation twice. Reconcile any uncertain payment before retrying.
See [the validation procedure](ROYALTY_WALLET.md#required-real-payment-validation).

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

The public branch calculates obligations and runs a per-trade background worker.
The built-in wallet adapter is LNbits, separate from Coinos trade escrow.
Other trusted wallet adapters can implement the same interface. See
[wallet configuration and reconciliation](ROYALTY_WALLET.md).

Outbound payments are disabled by default. They require explicit configuration
and transport enablement in production mode. Accounting-test obligations are
never automatically converted into live payments. The worker implements the
72-hour new-order gate and local incident notices; reporting faults to Paul
remains the operator's responsibility, not an automatic email feature.

Superseded batch code and tests have been removed. The implementation is
verified offline only; it has not made a live royalty payment.

The original private production service, its wallet, credentials and server
remain outside this work. These instructions do not authorize live testing,
deployment, merging or publication.
