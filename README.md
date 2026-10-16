# BIFp2p

**Source-available development preview | Lightning P2P trading on Telegram**

BIFp2p is a Telegram-based peer-to-peer Bitcoin trading bot. Traders arrange fiat
settlement directly with each other, while the application coordinates Lightning
escrow and payout through Coinos. The project is intended to help independent
developers inspect and improve the code and, once the financial safety gates are
completed, allow independent operators to deploy their own instances.

**WARNING: NOT READY FOR LIVE FUNDS OR PRODUCTION DEPLOYMENT.** Royalty
payment transport is disabled; the Coinos payment reconciliation contract and
end-to-end safety of escrow, refunds and payouts have not been fully verified.
This repository is provided for code review and offline development.

## One codebase, two roles

Developers and independent operators use the **same codebase**. Contributors
submit changes through branches and pull requests; operators should use only
reviewed, tagged releases when a production-ready release eventually exists.
There is no separate "developer edition" and "operator edition."

## Features and design

- Telegram-based buy and sell order workflows.
- Lightning escrow and payout integration with Coinos.
- Direct fiat settlement between counterparties; no fiat custody by the bot.
- Operator administration, disputes and recovery workflows.
- Local SQLite trade and royalty accounting.
- No requirement for the bot to collect identity or bank account details.
- Source-available code with an independent-operator royalty model.

The user-facing Telegram messages in the current source include Persian text.
**This repository's project documentation is English only.** The user interface
has not yet been fully internationalized.

## Royalty model

An independent operator uses their **own** bot credentials, Coinos account and
infrastructure. For each legitimately completed trade, the project royalty is
0.1% of the original Bitcoin amount, rounded down to whole satoshis:

`royalty_sats = original_amount_sats // 1000`

The royalty is paid **from the operator's existing commission**, not as an
additional fee to the buyer or seller. Intended beneficiary:
`bitcoinisfreedombot@coinos.io`.

**Current implementation is accounting-only.** `ACCOUNTING_TEST` records local
obligations but does not pay them. `PRODUCTION_ENFORCED` blocks new orders
because real royalty payment transport is disabled. Neither mode currently
authorizes live commercial operation. See [SECURITY.md](SECURITY.md) and
[the release gate](docs/RELEASE_GATE.md).

## Offline development

1. Clone this repository and create a development branch.
2. Use a supported Python environment and install dependencies from the
   repository's requirements file.
3. Run the offline tests: `python -m unittest discover -s tests -v`.
4. Run syntax checks: `python -m compileall -q .`.
5. Submit a pull request with test coverage and an explanation of financial
   safety implications.

**Do not connect a real Coinos wallet or Telegram production bot for testing.**
The `.env.example` file documents configuration keys but is not loaded
automatically. No safe production deployment guide exists yet.

## Security and limitations

- Never commit API tokens, private keys, user data, SQLite databases or logs.
- Operator-specific identifiers are still embedded in the source and must be
  refactored before third-party deployment.
- An uncertain payout must never be retried blindly.
- Offline unit tests are not a security audit.
- Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## License status

A proposed source-available license is in [LICENSE-DRAFT](LICENSE-DRAFT).
It is **not yet approved or final**. Until a final license is published, do not
assume permission to redistribute, modify for external distribution, or operate
commercially. GitHub visibility alone does not grant these rights.

## Project

- Community: https://t.me/BitcoinIsFreedom
- Maintainer: https://github.com/Paulmeloux
- Status: development preview; no production release
