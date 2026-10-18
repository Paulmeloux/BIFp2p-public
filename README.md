# BIFp2p

### Bitcoin is freedom. Exchange should not require permission.

The ability to exchange value is part of individual freedom. It should not depend on surrendering identity documents, obtaining an intermediary's approval, or living on the right side of a financial border.

When access to money can be withdrawn, financial independence becomes fragile. A blocked account, an inaccessible exchange, or a demand for more personal information can separate people from the means to act for themselves.

These problems require working tools.

**BIFp2p** is a Telegram-based, peer-to-peer Bitcoin exchange built under **Bitcoin Is Freedom**. It enables direct fiat settlement between buyers and sellers, with Bitcoin transferred over Lightning. Its purpose is to give communities a practical way to exchange Bitcoin and operate their own infrastructure.

## Freedom must be usable

A person should be able to acquire Bitcoin without presenting identity documents to this bot. A community should be able to inspect its exchange software, understand its rules, and choose the people responsible for running it.

BIFp2p requires a Telegram username. Its registration does not require identity documents or banking credentials. Buyers and sellers arrange fiat payment directly; the bot does not take custody of fiat. Counterparties may exchange settlement details themselves and must protect that information.

The original service grew out of the Persian-speaking Bitcoin community. Paul reports that it operates successfully and that identified issues in that deployment have been addressed. This source distribution makes the work available for inspection, improvement, translation, and independent operation.

The code should be examined. Its assumptions should be questioned. Defects should be reported and corrected.

## How it works

1. A buyer or seller creates an offer.
2. Another participant accepts it.
3. The seller funds a Lightning escrow invoice through Coinos.
4. Buyer and seller settle fiat directly.
5. After the required confirmations, the bot pays the buyer's Lightning invoice.

Disputes, refunds, and uncertain payments follow separate recovery paths. Operators are responsible for support and arbitration.

The boundaries must remain clear: **Coinos is a custodial provider. Telegram and Bitcoin do not guarantee anonymity.** Fiat counterparties and independent operators introduce their own risks. Publishing source code makes those choices inspectable; it does not remove them.

## Independent operation, honest attribution

Independent operators use their own bot, channel, administrator, infrastructure, credentials, and wallets. They are responsible for the service they provide and the funds entrusted to it.

Keep the original attribution visible:

**BIFp2p | Bitcoin Is Freedom | Created by Paul**

Retain it in the source notices, README, license, and accessible `/about` view. Identify your own deployment clearly. Do not claim to be its original creator or imply that Paul operates or endorses it.

## Sustaining the work

Each successfully completed trade owes the original project **0.1% of its original Bitcoin amount**, rounded down to whole satoshis:

`royalty_sats = original_amount_sats // 1000`

The royalty comes from the operator's existing commission, without an additional charge to either trader. Cancelled, failed, and refunded trades do not generate completed-trade royalties. Each trade has a separate obligation and payment; there is no batching.

**Fixed beneficiary:** `bitcoinisfreedombot@coinos.io`

Payment is due immediately. Technical faults must be reported to Paul and resolved within 72 hours of trade completion. Overdue unresolved debt pauses new orders; existing settlements, refunds, and recovery continue. Successful payments need local records, not emails. An uncertain payment must be verified before any retry.

The code includes a per-trade worker and an LNbits wallet adapter, separate from Coinos trade escrow. Other suitable wallets need a compatible adapter. Payment submission is disabled by default and requires explicit configuration.

## Read, verify, then operate

This distribution is a **development preview**. Its code passed 56 offline tests and GitHub checks. The new royalty payment path has not been tested with real funds by this preparation work.

Before accepting users' funds, validate your own deployment and actual payment flow with Paul where he is available. If he is unavailable, pay the same fixed beneficiary independently and retain verified settlement evidence. A standalone manual transfer does not validate an untested automated adapter or prepay future royalties.

- [Operator setup and recovery](docs/OPERATOR_GUIDE.md)
- [Royalty wallet and verification](docs/ROYALTY_WALLET.md)
- [Security and private disclosure](SECURITY.md)
- [Verification evidence and limitations](docs/RELEASE_GATE.md)

Use separate credentials and protect your database and backups. Never blindly repeat an unknown payment.

## License and contact

The [BIFp2p Source-Available License v1.0](LICENSE) permits inspection, modification, translation, redistribution, and independent operation subject to attribution and royalty conditions. This is a custom source-available license, not OSI-approved open source.

Copyright © 2026 **Paul**.

[Bitcoin Is Freedom](https://t.me/BitcoinIsFreedom) ·
[Paul](https://github.com/Paulmeloux) ·
[Canonical source](https://github.com/Paulmeloux/BIFp2p-public)

**Developers, operators, and private reports:** paulmeloux@proton.me

Financial freedom needs tools that people can understand and use. Inspect this one. Improve it. Take responsibility for the service you build with it.

