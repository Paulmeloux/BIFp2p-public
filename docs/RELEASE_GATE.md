# Publication and operator readiness — 2026-10-18

## Confirmed decisions and completed documentation

- Copyright holder: **Paul**. Maintainer: https://github.com/Paulmeloux
- Contact: **paulmeloux@proton.me**.
- [LICENSE](../LICENSE) contains the approved source-available v1.0 terms;
  licensing is no longer an unresolved draft.
- Preserve original attribution and distinguish independent operators.
- Royalty: `original_amount_sats // 1000` from the operator's commission,
  payable separately per completed trade to **bitcoinisfreedombot@coinos.io**.
- No batching. The previous batch design is superseded, not work to complete.
- Immediate payment; technical faults must be reported and resolved within
  72 hours of trade completion. No email per successful payment.
- Overdue unresolved obligations stop new orders, never safe settlement or
  refunds for existing users. This is the required behavior, not yet an
  implemented 72-hour runtime feature.
- No selected governing country or exclusive court. Ethical commitment does
  not make license conditions optional.
- Historical non-secret administrator/price-endpoint references are retained
  by the owner's decision; no history rewrite is planned for those references.

## Existing evidence

At revision `805d2cc`, 44 current files and 67 ancestor commits (88 distinct
file versions) were inspected. No live token or private key was found in
the checks performed. No tracked user database, backup or private log was found.
The current code has no books or BIFWallet integration. This is not a guarantee
that all possible sensitive data or financial defects have been excluded.

Earlier Windows and CI runs passed. The latest inspected run for `805d2cc`,
[39062777713](https://github.com/Paulmeloux/BIFp2p-public/actions/runs/39062777713),
compiled successfully but failed **8 of 49 tests**. Old test fixtures still
use a destination rejected by the fixed-recipient check; not all failures
have been repaired or independently explained.

Paul reports successful operation of the original Telegram service. That
separate deployment is outside this work and does not certify this branch.

## Remaining source preparation

1. Complete per-trade royalty payment integration and remove superseded
   batch code/tests in a separately authorized implementation change.
2. Resolve current test failures and verify the resulting revision.
3. Finish the operator setup guide for credentials, wallet/payment adapter,
   configuration and payment-incident handling.
4. Review the final files and privacy findings, then obtain the owner's
   express approval before merging or changing repository visibility.

This documentation step does not change executable code, runtime settings,
payment activation, branch protection, repository visibility or deployment.

## Before independent operation with real funds

- Verify the actual wallet's invoice resolution, payment API, fee behavior,
  independent payment lookup and reliable proof of settlement.
- Test one obligation/payment per trade, concurrency, crashes, restart,
  unknown outcomes, refund handling and backup restoration without duplicate
  transfers. Do not demand or implement a batch lifecycle.
- Verify immediate payment, the 72-hour rule and operator incident reporting;
  keep existing-trade safety paths available while new orders are paused.
- Verify Coinos escrow/payout/refund/recovery behavior and operator configuration.

Do not enable payment transport merely to clear a gate.
Financial activation or live testing requires separate express authorization.

## Work boundary

Work remains on `dev/royalty-batching-reconciliation`; the historical branch
name is not the payment policy. PR #1 remains a draft. No merge, publication,
private-repository access, live payment or production-server change is authorized
by completing this document.
