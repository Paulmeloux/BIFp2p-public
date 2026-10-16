# Royalty batching and Coinos reconciliation: implementation plan

Status: DESIGN ONLY. No production transport, live payment, or deployment is authorized.

## Existing invariants

- Each legitimately completed order creates one durable royalty obligation in the same SQLite transaction.
- Royalty amount is `original_amount_sats // 1000`; it is funded from the operator's existing commission.
- Refunded and canceled trades must not create payable royalties.
- `ROYALTY_PAYMENT_TRANSPORT_ENABLED = False` remains unchanged until an independently reviewed live-payment implementation is approved.
- New-trade safety gates must never prevent settlement, cancellation, refund, recovery, or authorized administration of existing trades.

## Proposed batch ledger

Add tables only in a reviewed migration, with foreign keys and unique constraints:

- `royalty_batches`: `id`, `status`, `destination_kind`, `destination_snapshot`, `total_sats`, `created_at_unix`, `updated_at_unix`, `payment_reference`, `failure_code`.
- `royalty_batch_items`: `batch_id`, `order_id`, `royalty_sats`; enforce `UNIQUE(order_id)` and `UNIQUE(batch_id, order_id)`.
- `royalty_batch_attempts`: durable attempt identifier, batch ID, state (`PREPARED`, `IN_FLIGHT`, `UNKNOWN`, `CONFIRMED`, `FAILED_DEFINITIVE`), request fingerprint, timestamps, and confirmed remote reference. Avoid storing invoice secrets or tokens.

Build batches under `BEGIN IMMEDIATE`. Select only eligible unpaid, positive-value obligations with the same destination snapshot; atomically reserve them and insert immutable batch items. Verify `SUM(items.royalty_sats) = batch.total_sats`. Never reuse an order across batches. Do not mix destinations or reinterpret historical destination snapshots.

Batching reduces the number of outgoing payments but does **not** solve remote idempotency. A timeout or crash after submitting a transfer must result in `UNKNOWN`, never an automatic second POST. A second transfer requires independently established proof that the first did not execute, or explicit operator intervention with a recorded audit trail. Restore-from-backup and database-loss cases require manual reconciliation.

## Coinos contract validation before enabling transfers

Verify against the deployed Coinos version and sanitized real fixtures:

1. Outgoing transfer request format, destination type, fee behavior, and amount units.
2. Exact meaning of `confirmed`, `hash`, `id`, `ref`, `type`, `amount`, and `created`.
3. Whether history includes internal transfers and their unique identifiers.
4. Pagination ordering, `limit`, `offset`, `count`, retention, and possible concurrent inserts.
5. Whether the server supports a caller-supplied idempotency key or a unique transfer reference that can be reliably queried.
6. Which routing errors prove a payment definitely failed; ambiguous errors must remain `UNKNOWN`.

Never treat absence from one history page as proof of nonpayment. Never match only by amount and approximate timestamp. For ambiguous or multiple matches, fail closed and request manual review.

## Offline acceptance tests

- Batch creation is atomic under concurrent workers.
- No order is included in two batches; no refunded trade is included.
- A destination change does not redirect existing obligations.
- Totals use integer satoshis and equal the sum of immutable items.
- Simulated timeout, crash after remote success, restart, and database errors do not cause a second POST.
- Multiple or missing history matches remain `UNKNOWN`.
- History pagination handles more than one page and stops safely on API failures.
- Concurrent reconciliation completes once, and notifications are not duplicated.
- Trade blocking never blocks existing-order settlement or refund.

## Release gate

Keep all payment transport disabled and work on `dev/royalty-batching-reconciliation` only. Implement offline ledger and tests first. Run complete regression tests before any merge. No public release or production deployment without Coinos contract verification, a separate security review, and explicit operator approval.
