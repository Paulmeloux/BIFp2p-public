# BIFp2p independent operator: royalty and original project identity

Scope: the public source-available distribution only. The creator's private production bot, wallet, credentials, and server are out of scope and must not be changed.

## Original project and attribution

- Original project: **BIFp2p**, created under **Bitcoin Is Freedom**.
- Original author and maintainer: **Paul**.
- Original community: https://t.me/BitcoinIsFreedom
- Canonical source: https://github.com/Paulmeloux/BIFp2p-public

Independent operators own and identify their own deployments, Telegram bots, support channels, infrastructure, and wallets. They must not claim to be the original author or imply official endorsement. The original project attribution must remain visible in source notices, the README, and the bot's `/about` command. No legal name is required in public attribution.

## Per-trade royalty (no batching)

- For **each** successfully completed trade, royalty is **0.1% of the original trade amount**, rounded down to whole satoshis: `royalty_sats = original_amount_sats // 1000`.
- Each trade has its **own obligation and payment**. Never aggregate royalties from multiple trades.
- The royalty comes from the **operator's existing commission**; it is not added to the buyer's or seller's charges.
- Cancelled, refunded, or unsuccessful trades owe no completed-trade royalty.
- Fixed beneficiary: **bitcoinisfreedombot@coinos.io**. The beneficiary is not an operator-configurable address.
- Operators may use **any suitable Lightning wallet**, not necessarily Coinos, provided it has a compatible authorized API/payment adapter. The operator is responsible for configuring wallet credentials, an invoice-resolution mechanism for the beneficiary's Lightning Address, and payment verification.
- A wallet without programmatic payment support cannot be assumed to provide automatic payment. Operator action or a compatible adapter is required.
- Each payment must be checked against the corresponding trade amount and recorded with an auditable result. A timeout or uncertain outcome must **not** be blindly retried or marked paid.
- Wallet credentials and secrets must never be committed to the repository.

## Responsibility and current implementation status

The public code currently calculates and stores a royalty obligation for completed trades. A wallet-neutral per-trade interface has been prototyped, but it is **not wired to an operational wallet adapter or automatic payment worker**. The former batching experiment is **not the intended royalty policy** and must not be used for a release. The last per-trade development commit did not pass CI at the time of this policy decision.

The independent operator is responsible for implementing and validating the compatible wallet/API connection and paying each trade's royalty. The software must not claim that an obligation was paid without reliable payment evidence. This policy is also subject to the eventual source-available license; the current LICENSE-DRAFT is not an approved final license.

No live transfers, production configuration changes, or publication are authorized by this document.
