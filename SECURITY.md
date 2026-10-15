# Security and release status

This repository is a PRIVATE STAGING COPY, not an audited or production-ready release.

- Never commit Telegram tokens, Coinos tokens, personal data, wallet keys, or live SQLite databases.
- The original project's Git history is intentionally not imported.
- The application currently contains operator-specific channel, bot, and group identifiers and needs configuration refactoring before third-party deployment.
- Lightning escrow, refunds, settlement, and payout recovery require end-to-end tests before handling real funds.
- The 0.1% royalty is implemented as durable local accounting only. Real royalty
  payment transport is disabled and no automatic royalty payment can occur.
- `ACCOUNTING_TEST` is explicitly non-commercial. `PRODUCTION_ENFORCED` keeps
  new creation and acceptance locked until a separately reviewed payment and
  reconciliation transport is implemented; existing-trade safety paths remain
  enabled.
- `ACCOUNTING_TEST` also requires an exact development/staging acknowledgement;
  `.env.example` deliberately leaves royalty mode unconfigured.
- Abandoned buyer payouts become reconciliation-only after a conservative
  timeout. They are never automatically paid again.
- The Coinos API contract assumed by payout matching is not yet verified.
  `UNKNOWN` payouts require read-only reconciliation or authorized manual
  review; do not infer remote success from undocumented response fields.
- This private staging milestone is not approved for production or any
  payment-enabled deployment and must not be published as a release.
- Royalty destinations are configuration, not credentials. Do not place wallet
  seeds, API tokens, or other secrets in the destination fields.
- A final commercial/source-available license requires legal review and explicit acceptance before public release.
