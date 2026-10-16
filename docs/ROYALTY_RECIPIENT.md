# Royalty recipient and operator wallet separation

Operator clarification, 2026-10-16:

- **Fixed BIFp2p royalty beneficiary**: `bitcoinisfreedombot@coinos.io` (Coinos username: `bitcoinisfreedombot`; public account URL: `https://coinos.io/bitcoinisfreedombot`).
- **Independent operator wallet**: each third-party operator who deploys the publicly released, source-available BIFp2p software configures their **own** Coinos trading/escrow account and credentials. Never ship the creator's Coinos credentials in a public distribution.
- **Royalty policy**: 0.1% of the original amount of each successfully completed trade, calculated in integer satoshis as `original_amount_sats // 1000`. This is funded by the third-party operator from its existing commission, not charged as an extra user fee.
- **Creator's own installation**: the creator's operational trading wallet is also the royalty beneficiary account. It is not a third-party licensee and must not submit a transfer from that wallet to itself.

## Required configuration and safety behavior

1. Keep the royalty destination as a **creator-controlled fixed beneficiary** in the distributed product. Operator trading credentials and wallets must be configured separately. Never conflate operator credentials with beneficiary identity.
2. Detect the creator's own installation / beneficiary-account self-transfer case using authenticated wallet identity, not merely an operator-supplied arbitrary username. Do not falsely mark an actual transfer as paid; if the creator is exempt, represent that exemption explicitly in accounting and licensing rules.
3. For a third-party operator, create one obligation per completed trade; a batch may settle multiple obligations to the fixed beneficiary. A payout is confirmed only after verified Coinos evidence. An ambiguous payout remains `UNKNOWN` and must never be automatically re-sent.
4. Source-available distribution does not itself guarantee enforcement: a modified client can bypass local checks. Any royalty mechanism is partly a contractual obligation; do not claim tamper-proof payment enforcement.
5. Do not enable live payments until the deployed Coinos contract, reconciliation, production batch integration, and legal license are reviewed.

No secret, token, or private credential belongs in this file.
