# BIFp2p

Private staging repository for the standalone BIFp2p Lightning P2P Telegram bot.

**Not ready for public release or production deployment.** The standalone code requires security review, dependency checks, and end-to-end testing.

## Royalty accounting phase

This staging copy records one durable royalty obligation for each legitimately
completed trade. The amount is `original_amount_sats // 1000` and is accounted
within the operator's existing commission; buyer payouts and seller charges are
unchanged.

`ROYALTY_MODE` has no operational default and must be explicitly configured:

- `ACCOUNTING_TEST` records obligations for offline evaluation only. It never
  sends royalty payments and is not a commercially compliant configuration. It
  will not start unless `ROYALTY_ACCOUNTING_TEST_ACK` is exactly
  `I_UNDERSTAND_ACCOUNTING_TEST_IS_NOT_PRODUCTION`.
- `PRODUCTION_ENFORCED` fails closed for new order creation and acceptance.
  Actual royalty transport is deliberately disabled in this phase, so this mode
  cannot open the new-trade gate yet. Existing settlement, recovery,
  cancellation, and dispute paths remain available.

A syntactically valid `ROYALTY_DESTINATION_KIND` and `ROYALTY_DESTINATION` are
required to open the accounting-test new-trade gate. The destination is stored
locally with each obligation and is not sent anywhere. Administrators can use
`/royalty_status` for local status reporting; no telemetry is implemented.

Database migration creates the royalty ledger without retroactively charging
trades completed before the migration epoch. A post-migration successful trade
without its obligation closes the new-trade gate until the inconsistency is
investigated. Reconciled cancellation refunds use the distinct `REFUNDED`
terminal state and never create royalty or successful-trade reputation.

## Staging limitations

This is a private staging milestone, not a public release. The Coinos payment
and history response contract used by payout reconciliation has not yet been
verified against authoritative Coinos documentation. Payouts with an ambiguous
outcome remain `UNKNOWN` and require read-only reconciliation or authorized
manual review; they are never automatically retried. Production or any
payment-enabled deployment is not approved.

Project: https://t.me/BitcoinIsFreedom
Maintainer: https://github.com/Paulmeloux

License and deployment instructions will be added after review.
