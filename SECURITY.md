# Security policy

## Release status

BIFp2p is a **source-available development preview**, not audited financial
software. Do not deploy with real user funds until you have independently
reviewed its security and completed end-to-end testing.

The code for independent operators is maintained separately from any other
installation. No private production infrastructure or credentials are part
of the public release scope.

## Responsible disclosure

Please **do not open a public GitHub issue containing an exploitable
vulnerability, credentials, private transaction details, or personal data**.
Contact the project maintainer privately through
https://github.com/Paulmeloux (use a private contact method where available).
Do not test vulnerabilities against running services or third-party funds
without authorization.

## Known security and deployment blockers

- Coinos royalty payment transport is disabled; production royalty enforcement
  cannot open the new-trade gate.
- Royalty batching is currently an isolated offline prototype and is not
  integrated with the live obligation/payment lifecycle.
- Coinos payout-history and payment response semantics require verification
  against the deployed API, especially after ambiguous network failures.
- Independent-operator settings need review: admin ID, bot username, Telegram
  channels/groups, and market-price endpoint must be configured per operator.
- Lightning escrow, payout, refunds, recovery, and disputes need staged
  end-to-end verification before financial use.
- No guarantee of remote idempotency has been established. Do not retry an
  ambiguous payment without verified reconciliation.

## Operator precautions

- Never commit Telegram bot tokens, Coinos tokens, wallet seeds, real databases,
  backup archives, logs containing user data, or environment files.
- Use a distinct wallet and credentials for your own independent deployment.
- Restrict database, configuration, and backup permissions.
- Treat every `UNKNOWN` payment outcome as potentially successful until
  authoritative evidence proves otherwise.
- Run offline tests before making changes and verify migrations on a copy of
  non-sensitive test data.
- Do not expose a development deployment as a production financial service.

## Current automated checks

The GitHub Actions offline test suite has completed successfully on the
development branch. Passing unit tests is not a security audit or authorization
to deploy.
