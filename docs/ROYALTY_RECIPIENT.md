# Public BIFp2p distribution: operator wallets and royalty beneficiary

Scope: **the separate source-available public distribution only**. The creator's already-running private production installation is out of scope. Do not discuss, migrate, configure, test against, or modify that installation as part of the public release.

## Roles

- **Independent operator**: anyone who deploys the distributed BIFp2p code configures their own Coinos wallet, credentials, and operational settings.
- **Fixed royalty beneficiary**: the project receives royalties at `bitcoinisfreedombot@coinos.io` (Coinos username `bitcoinisfreedombot`; public URL `https://coinos.io/bitcoinisfreedombot`).
- **Contributors**: developers may review and improve the public code according to the applicable source-available license and contribution terms.

## Commercial terms proposed for the public distribution

- Royalty: 0.1% of each successfully completed trade's original amount, rounded down to whole satoshis (`amount_sats // 1000`).
- The operator funds the royalty from their own existing commission, not as an extra fee to end users.
- Each independent operator uses their own wallet to pay the fixed beneficiary.
- The fixed beneficiary address is public; no Coinos tokens or secrets from any account are included in the source code.
- A local software check is not tamper-proof enforcement; the royalty is also a contractual obligation under the eventual license.

## Release boundary

This document defines the intended architecture, not a claim that automated royalty settlement is already operational. Keep live payment transport disabled until production-grade accounting integration, Coinos API verification, reconciliation tests, and license review are complete. The creator's private production system must remain separate and unchanged.
