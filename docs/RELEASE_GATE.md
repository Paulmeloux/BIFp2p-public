# Release gate — 2026-10-16

## Verified

- Windows: 35/35 offline unit tests passed, including 4 batching tests; Python compilation succeeded; clean git working tree (user-provided test output).
- GitHub Actions: workflow run 38885192147, conclusion `success`, commit `1a7846442af23c6346b7d42bb6243051b228098c`.
- The new batching module is **offline-only** and not wired to the bot. No Coinos transfer occurs.
- The production royalty payment transport is explicitly disabled.

## Blocking items (do not merge or deploy)

1. **Database lifecycle integration:** current `create_batch` selects `DUE` obligations and creates batch items, but does not transition obligation statuses to `PAYING`. Batch states and obligation states must be synchronized transactionally. A completed batch must atomically mark all corresponding obligations `PAID`, and a crashed/ambiguous payment must remain `UNKNOWN` without re-sending. Add real-schema integration tests for all paths.
2. **Remote exactly-once semantics:** confirm the Coinos deployed API contract, history fields, and whether an idempotency key exists. An ambiguous POST outcome must never trigger automatic retry. If the API cannot provide a reliable remote idempotency guarantee, require operator-confirmed reconciliation and a deliberately limited manual payout flow.
3. **Accounting consistency:** verify no order is included in multiple batches; do not permit `ACCOUNTING_TEST` obligations to become payable; validate historical destination snapshots, sum invariants, operator commission funding, and concurrent workers.
4. **Release/legal configuration:** choose the royalty destination kind and recipient, notice email, publisher identity, governing law, and review the source-available license. No recipient secret should be committed.
5. **Operational readiness:** stage with mocked Coinos responses and simulated crashes; document backup/restore and dispute/refund behavior; review new-trade gate with batch `PREPARED`, `IN_FLIGHT`, `UNKNOWN`, and `CONFIRMED` states.

## Safety boundary

This branch is a development prototype. CI success does **not** prove financial safety. Do not enable real royalty payments, merge to main, publish, or deploy before the blockers above are resolved and the operator explicitly approves.
