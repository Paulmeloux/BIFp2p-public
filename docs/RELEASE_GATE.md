# Source publication readiness — 2026-10-18

## Scope and conclusion

Preparation is complete for **public source review as a development preview**,
subject to the owner's separate approval to merge and change repository visibility.
This is not certification of independent financial deployments.

The original private BIFp2p repository, Telegram service, wallets and server have
not been accessed or changed. No real royalty payment was made during this work.

## Completed preparation

- [LICENSE](../LICENSE): final source-available v1.0 terms, copyright Paul,
  attribution, redistribution/modification conditions and fixed per-trade royalty.
- Public contact: **paulmeloux@proton.me**; maintainer https://github.com/Paulmeloux.
- Current code excludes books and BIFWallet modules and their runtime imports.
- Tokens, operator identity, channel, database and price endpoint are configured
  by the independent operator. No real credentials are supplied in the template.
- Royalty: `original_amount_sats // 1000`, payable separately per completed trade
  to **bitcoinisfreedombot@coinos.io**, from the operator's commission.
- Integrated LNbits wallet adapter and per-trade worker; no batching path remains.
  Other trusted local wallet modules may implement the documented interface.
- Signed invoice verification, exact amount/metadata/expiry checks, durable hash
  and wallet identity before submission, independent lookup and preimage proof.
- Unknown submissions are reconciliation-only. A 72-hour unresolved debt pauses
  new order creation/acceptance while existing settlement/refund/recovery remains
  available. Payment defaults are disabled; no existing environment was activated.
- Successful payments require local records, not emails. Incidents notify the
  operator's admin; reporting to Paul is the operator's responsibility.
- [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md): installation, credentials, runtime modes,
  price-service contract, startup, validation, incidents, backups and recovery.
- Operators must test actual payment with Paul where available, or pay the fixed
  beneficiary independently and verify settlement if he is unavailable. Owner
  acknowledgement is not required for independently verified payment.

## Evidence

| Check | Result |
| --- | --- |
| Code reference | `c799209`; later `90fd69f` changes license/docs only. |
| Offline suite | 56 passing tests, including 21 new wallet/payment tests. |
| Compilation | Passed locally and in GitHub Actions on Python 3.11. |
| Static exclusions | Passed; prohibited modules/imports absent. |
| Implementation CI | [39131208052](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39131208052), success. |
| Subsequent CI | [39131921299](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39131921299), success. |
| Subsequent static CI | [39131917144](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39131917144), success. |
| Source/history privacy review | At `90fd69f`: 44 current files, 70 ancestor commits, 115 distinct file versions. |
| Credentials/private data findings | No live token/private key, tracked user database, backup or private log found in the checks performed. |
| Author metadata | Paul aliases and the approved public contact email. |
| Historical references | Non-secret administrator ID and old price endpoint remain by the owner's decision. |

Earlier failures at `805d2cc` were resolved by aligning destination fixtures with
the fixed beneficiary and updating the deadline test. Superseded experimental
tests were removed, and 21 new tests cover the integrated payment path.

The tests exercise separate payments, concurrency, timeout/restart, preparation
failure retry, duplicate invoice rejection, proof/amount mismatch, wallet changes,
accounting-test exclusion, refund exclusion, deadline boundaries and existing
settlement while the new-order gate is closed. No funded service was contacted.

## Independent operator requirements

The original service's successful operation is reported by Paul; it does not
certify another operator's infrastructure or the new royalty wallet integration.
Before accepting third-party funds, the operator must verify their actual Coinos
escrow/payout/refund/recovery and royalty wallet behavior using controlled funds.
Follow [ROYALTY_WALLET.md](ROYALTY_WALLET.md), including independent validation
when Paul is unavailable. A standalone manual transfer does not validate an
untested automatic adapter or prepay future royalties.

Database rollback or loss can remove evidence of a real payment. Reconcile wallet
history before resuming submissions. Exactly-once operation after database loss
is not claimed. No independent security audit or universal wallet compatibility
is claimed. These are deployment limitations, not a requirement to restart source
preparation or implement batching.

## Publication boundary

The development branch remains `dev/royalty-batching-reconciliation`; its name is
historical. PR #1 remains a draft, `main` is unchanged, and the repository is private
until the owner explicitly authorizes publication. Preparing this document does
not itself authorize merging, changing visibility, deploying or transferring funds.

