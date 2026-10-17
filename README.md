# BIFp2p

### Bitcoin is freedom. Exchange should not require permission.

**BIFp2p** is a source-available, Telegram-based peer-to-peer Bitcoin exchange
project built around a simple conviction: people should be able to exchange
Bitcoin directly, without having to surrender their identity or rely on a
centralized fiat custodian.

The project began in the **Bitcoin Is Freedom** community as a practical
response to the barriers faced by people in restricted financial environments.
Its purpose is not to create another gatekeeper. It is to make direct Bitcoin
exchange more accessible, inspectable, and reproducible by independent
communities.

**Original project:** [Bitcoin Is Freedom](https://t.me/BitcoinIsFreedom)  
**Original author and maintainer:** [Paul](https://github.com/Paulmeloux)  
**Canonical source:** [github.com/Paulmeloux/BIFp2p-public](https://github.com/Paulmeloux/BIFp2p-public)

> Software should make financial choice possible, not demand permission to
> participate. Inspect the code, challenge its assumptions, improve it, and
> operate responsibly.

## Why this exists

For many people, access to Bitcoin is constrained not by the protocol but by
the surrounding financial infrastructure: custodial exchanges, identity
requirements, blocked services, payment intermediaries, and unreliable access
to markets. BIFp2p seeks to lower those barriers.

The design principles are:

- **Direct exchange.** Buyers and sellers settle fiat with one another.
- **Minimal collection.** The bot does not require identity documents or
  collect banking credentials for its own operation.
- **Bitcoin over Lightning.** Trades use Lightning invoices for Bitcoin
  escrow and payout.
- **Independent operation.** Communities can eventually run separate
  deployments using their own infrastructure and escrow credentials. The royalty-paying wallet may be any compatible Lightning wallet.
- **Verifiable behavior.** Source access, tests, and explicit accounting make
  the system open to inspection, criticism, and improvement.
- **Accountability.** A visible project origin, clear operator identity, and
  a sustainable royalty model should coexist with the ability to modify code.

BIFp2p does **not** promise anonymity, trustlessness, or zero counterparty
risk. The current Lightning escrow uses **Coinos**, a third-party custodial
service. Fiat settlement is outside the bot, and disputes can still require
human intervention. Telegram and Coinos have their own privacy and
availability limitations.

## How a trade works

1. A seller or buyer creates an offer specifying a Bitcoin amount and a
   fiat price or pricing method.
2. The offer is published in the operator's configured Telegram channel.
3. A counterparty accepts the offer.
4. The seller funds a Coinos Lightning escrow invoice.
5. Once Bitcoin funding is confirmed, the parties arrange fiat settlement
   directly between themselves.
6. After the required confirmations, the bot attempts the Lightning payout
   to the buyer and records the final trade state.
7. Failed, cancelled, disputed, or ambiguous outcomes follow separate
   recovery paths. A payment of uncertain status must never be blindly
   repeated.

This is a description of the intended workflow, not a claim that every
failure scenario has been proven safe.

## One project, one shared codebase

Developers and independent operators use **the same source tree**.

**Developers** review code, submit pull requests, write tests, examine
financial edge cases, and propose improvements.

**Operators** will be able to deploy reviewed releases using their own
Telegram bot, channel, administrator identity, price service, and Coinos
credentials. They are responsible for their users, infrastructure, funds,
dispute handling, and compliance with applicable law.

There is no separate developer edition and no separate operator edition.
Development branches and tagged releases are stages of one project.

## Project identity and attribution

BIFp2p was created under the **Bitcoin Is Freedom** initiative. The origin
must remain clear when others inspect, fork, modify, or redistribute the code.

Project attribution should appear in:

- The repository README, license and source notices.
- The bot's `/about` command, which displays the original source and community.
- Appropriate documentation for derivative deployments.

Independent operators must be free to identify **their own** service and
support channels. Attribution must not impersonate an official service,
silently redirect customers to the original operator, or interfere with
independent configuration. The original project's attribution and an
operator's own identity are different things.

The original project is associated with
[Bitcoin Is Freedom](https://t.me/BitcoinIsFreedom) and
[Paul](https://github.com/Paulmeloux).

**Implementation note:** The bot now has a `/about` attribution command.
Attribution in every relevant user-facing view has not been implemented or
verified. The existing UI still contains Persian messages and some
community-specific references. Those need to be localized and separated from
operator identity before an English-only operator release.

## Fees and project sustainability

The current design charges a seller-side operator commission determined by
the seller's reputation tier. The buyer does not pay an additional BIFp2p
royalty.

Independent operators owe the original BIFp2p project **0.1% of the original
Bitcoin amount of each successfully completed trade**, rounded down to
whole satoshis:

`royalty_sats = original_amount_sats // 1000`

This royalty comes **out of the operator's existing commission**; it is not
an additional charge to the buyer or seller.

**Project royalty beneficiary:** `bitcoinisfreedombot@coinos.io`

Refunded, cancelled, and failed trades do not generate completed-trade
royalties. The implementation maintains a local obligation ledger, but
royalty payment transport is **not implemented or enabled**. Each trade's
royalty is payable separately, without batching. Independent operators must
configure a suitable Lightning wallet, compatible payment API, and payment
verification mechanism to remit the fixed beneficiary. An accounting entry
is not proof of payment. See [operator royalty requirements](docs/ROYALTY_RECIPIENT.md).

## Technology and architecture

- **Python** and `python-telegram-bot` for the Telegram interface.
- **SQLite** for orders, recovery state, and royalty accounting.
- **Coinos API** for custodial Lightning invoice and payment operations.
- **Telegram channels** for publishing orders and handling counterparties.
- **Operator-defined market-price API** for reference pricing.
- **Offline automated tests** for key accounting and workflow invariants.

Sensitive credentials belong in the operator's environment, never in source
control. The `.env.example` file is an illustrative configuration template
and is not automatically loaded.

## Current development status

**This repository is a development preview. Do not deploy with real funds.**

The offline test suite has passed on the development branch. This is not a
security audit or a guarantee of correct behavior with live Coinos services.

The production royalty payment transport is disabled. Production enforcement
intentionally blocks new orders. The previous batch royalty prototype is not part of the intended payment
policy. Per-trade wallet-neutral payment remains an unconnected prototype. End-to-end verification of escrow, payouts, refunds,
crash recovery, and ambiguous Coinos responses is required before a
production release.

See [SECURITY.md](SECURITY.md) and
[docs/RELEASE_GATE.md](docs/RELEASE_GATE.md) for the current safety gate.

## Contributing

This project welcomes careful, security-minded review, especially of
payment-state transitions, replay prevention, invoice handling, refunds,
race conditions, privacy, and independent-operator configuration.

1. Create a development branch from the shared repository.
2. Install the dependencies listed in `requirements.txt`.
3. Run `python -m unittest discover -s tests -p 'test_*.py' -v`.
4. Run `python -m compileall -q .`.
5. Open a pull request describing the change, its tests, and any financial
   or privacy implications.

Do not submit live credentials, user databases, private logs, or real wallet
material. Do not test against third-party services or funds without
authorization.

## Licensing

BIFp2p is intended to be **source-available**, not OSI-approved open source.
The proposed terms permit source review, modifications and independent
operation subject to attribution and an operator royalty.

The proposed terms are currently in
[LICENSE-DRAFT](LICENSE-DRAFT), **not a finalized license**. Until a final
license is approved and published, GitHub access alone does not grant a
general right to redistribute or commercially operate the software.

## A note on independence

A community does not become financially independent merely by installing a
bot. It must be able to understand the risks, audit the code, run its own
infrastructure, and hold its own operators accountable.

BIFp2p is an attempt to make that possible, one trade and one improvement at
a time.

**Bitcoin Is Freedom**  
[Community](https://t.me/BitcoinIsFreedom) ·
[Original maintainer](https://github.com/Paulmeloux) ·
[Canonical repository](https://github.com/Paulmeloux/BIFp2p-public)
