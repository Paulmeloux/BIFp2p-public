import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
import royalty
import settlement
import settings


class RoyaltyAccountingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.tempdir.name, "royalty-test.db")
        self.db_path_patch = patch.object(database, "DB_PATH", self.database_path)
        self.mode_patch = patch.object(royalty, "ROYALTY_MODE", "ACCOUNTING_TEST")
        self.kind_patch = patch.object(
            royalty, "ROYALTY_DESTINATION_KIND", "LIGHTNING_ADDRESS"
        )
        self.destination_patch = patch.object(
            royalty, "ROYALTY_DESTINATION", royalty.FIXED_ROYALTY_DESTINATION
        )
        self.grace_patch = patch.object(
            royalty, "ROYALTY_SETTLEMENT_GRACE_SECONDS", 60
        )
        self.ack_patch = patch.object(
            royalty, "ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED", True
        )
        for active_patch in (
            self.db_path_patch,
            self.mode_patch,
            self.kind_patch,
            self.destination_patch,
            self.grace_patch,
            self.ack_patch,
        ):
            active_patch.start()
            self.addCleanup(active_patch.stop)
        database.init_database()
        self.addCleanup(self.tempdir.cleanup)

    def insert_order(
        self,
        *,
        status="BUYER_INVOICE_RECEIVED",
        side="SELL",
        payout_status=None,
        amount_sats=50_999,
        fee_percent=0.5,
        invoice="ln-test-invoice",
    ):
        conn = database.db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders
                (order_code, seller_id, buyer_id, amount_sats, fee_percent,
                 buyer_invoice, order_side, status, payout_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"TEST-{id(self)}-{threading.get_ident()}-{cursor.lastrowid}",
                101,
                202,
                amount_sats,
                fee_percent,
                invoice,
                side,
                status,
                payout_status,
                "2026-01-01 00:00:00",
            ),
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id

    def obligation(self, order_id):
        conn = database.db_connect()
        row = conn.execute(
            """
            SELECT original_amount_sats, operator_commission_sats, royalty_sats,
                   completion_source, status, mode
            FROM royalty_obligations WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
        conn.close()
        return row

    def complete_directly(self, order_id, source="MANUAL", completed_at=100):
        conn = database.db_connect()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "UPDATE orders SET status = ?, completed_at_unix = ? WHERE id = ?",
            ("COMPLETED", completed_at, order_id),
        )
        royalty.create_royalty_obligation(
            cursor, order_id, source, completed_at_unix=completed_at
        )
        conn.commit()
        conn.close()

    def assert_order_financials_unchanged(self, order_id, amount=50_999, fee=0.5):
        conn = database.db_connect()
        row = conn.execute(
            "SELECT amount_sats, fee_percent FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row, (amount, fee))

    async def test_normal_payout_completion_creates_exact_obligation_once(self):
        order_id = self.insert_order()
        context = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(return_value=None))
        )
        payment = {
            "id": "payment-1",
            "ref": "ref-1",
            "confirmed": True,
            "amount": -50_999,
            "type": "lightning",
            "hash": "ln-test-invoice",
        }
        with patch.object(settlement, "coinos_request", AsyncMock(return_value=payment)) as call:
            self.assertTrue(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-test-invoice"
                )
            )
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-test-invoice"
                )
            )
        call.assert_awaited_once_with(
            "POST", "/payments", {"payreq": "ln-test-invoice"}
        )
        self.assertEqual(
            self.obligation(order_id),
            (50_999, 254, 50, "NORMAL", "ACCOUNTED", "ACCOUNTING_TEST"),
        )
        self.assert_order_financials_unchanged(order_id)

    async def test_reconciled_completion_and_refund_exclusion(self):
        trade_id = self.insert_order(payout_status="UNKNOWN")
        refund_id = self.insert_order(
            status="CANCELED_REFUND_PENDING", payout_status="UNKNOWN",
            invoice="ln-refund-invoice",
        )
        payment = {"id": "history-1", "ref": "history-ref"}
        result = settlement.complete_reconciled_buyer_payout(
            trade_id, 202, "ln-test-invoice", payment
        )
        self.assertIsNotNone(result)
        self.assertEqual(self.obligation(trade_id)[3], "RECONCILED")
        self.assertIsNone(
            settlement.complete_reconciled_buyer_payout(
                trade_id, 202, "ln-test-invoice", payment
            )
        )

        refund_result = settlement.complete_reconciled_buyer_payout(
            refund_id, 202, "ln-refund-invoice", payment
        )
        self.assertIsNotNone(refund_result)
        self.assertIsNone(self.obligation(refund_id))
        conn = database.db_connect()
        refund_row = conn.execute(
            "SELECT status, payout_status FROM orders WHERE id = ?", (refund_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(refund_row, ("REFUNDED", "CONFIRMED"))

    async def test_refund_does_not_earn_successful_trade_reputation(self):
        from users import historical_reputation_points

        refund_id = self.insert_order(
            status="CANCELED_REFUND_PENDING",
            payout_status="UNKNOWN",
            invoice="ln-refund-reputation",
        )
        settlement.complete_reconciled_buyer_payout(
            refund_id,
            202,
            "ln-refund-reputation",
            {"id": "refund-id", "ref": "refund-ref"},
        )
        self.assertEqual(historical_reputation_points(101), 0)
        self.assertEqual(historical_reputation_points(202), 0)
        self.assertIsNone(self.obligation(refund_id))

    async def test_both_manual_completion_callbacks_use_atomic_path(self):
        for side in ("BUY", "SELL"):
            with self.subTest(side=side):
                order_id = self.insert_order(side=side, payout_status="UNKNOWN")
                first = settlement.complete_manually_confirmed_buyer_payout(
                    order_id, side
                )
                second = settlement.complete_manually_confirmed_buyer_payout(
                    order_id, side
                )
                self.assertIsNotNone(first)
                self.assertIsNone(second)
                self.assertEqual(self.obligation(order_id)[3], "MANUAL")
                self.assert_order_financials_unchanged(order_id)

    async def test_manual_and_automatic_reconciliation_preserve_error_history(self):
        manual_id = self.insert_order(side="SELL", payout_status="UNKNOWN")
        reconciled_id = self.insert_order(
            side="BUY", payout_status="UNKNOWN", invoice="ln-error-history"
        )
        conn = database.db_connect()
        conn.execute(
            "UPDATE orders SET payout_error = ? WHERE id IN (?, ?)",
            ("TIMEOUT_AFTER_REMOTE_SUBMISSION", manual_id, reconciled_id),
        )
        conn.commit()
        conn.close()

        self.assertIsNotNone(
            settlement.complete_manually_confirmed_buyer_payout(manual_id, "SELL")
        )
        self.assertIsNotNone(
            settlement.complete_reconciled_buyer_payout(
                reconciled_id,
                202,
                "ln-error-history",
                {"id": "history-id", "ref": "history-ref"},
            )
        )
        conn = database.db_connect()
        errors = conn.execute(
            "SELECT payout_error FROM orders WHERE id IN (?, ?) ORDER BY id",
            (manual_id, reconciled_id),
        ).fetchall()
        conn.close()
        self.assertEqual(
            errors,
            [
                ("TIMEOUT_AFTER_REMOTE_SUBMISSION",),
                ("TIMEOUT_AFTER_REMOTE_SUBMISSION",),
            ],
        )

    async def test_obligation_failure_rolls_back_completion(self):
        order_id = self.insert_order(side="BUY", payout_status="UNKNOWN")
        with patch.object(
            settlement,
            "create_royalty_obligation",
            side_effect=RuntimeError("injected accounting crash"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected accounting crash"):
                settlement.complete_manually_confirmed_buyer_payout(order_id, "BUY")
        conn = database.db_connect()
        row = conn.execute(
            "SELECT status, payout_status, completed_at_unix FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM royalty_obligations WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(row, ("BUYER_INVOICE_RECEIVED", "UNKNOWN", None))
        self.assertEqual(count, 0)

    async def test_post_payout_accounting_crash_enters_reconciliation_without_repay(self):
        order_id = self.insert_order()
        context = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(return_value=None))
        )
        payment = {
            "id": "already-paid",
            "ref": "already-paid-ref",
            "confirmed": True,
            "amount": -50_999,
            "type": "lightning",
            "hash": "ln-test-invoice",
        }
        with patch.object(
            settlement, "coinos_request", AsyncMock(return_value=payment)
        ) as payment_call, patch.object(
            settlement,
            "create_royalty_obligation",
            side_effect=RuntimeError("injected ledger crash"),
        ):
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-test-invoice"
                )
            )
        payment_call.assert_awaited_once()
        conn = database.db_connect()
        state = conn.execute(
            "SELECT status, payout_status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(state, ("BUYER_INVOICE_RECEIVED", "UNKNOWN"))
        self.assertIsNone(self.obligation(order_id))

        reconciled = settlement.complete_reconciled_buyer_payout(
            order_id, 202, "ln-test-invoice", payment
        )
        self.assertIsNotNone(reconciled)
        self.assertEqual(self.obligation(order_id)[3], "RECONCILED")
        self.assert_order_financials_unchanged(order_id)

    async def test_payment_success_failure_timeout_and_duplicate_resolution(self):
        paid_id = self.insert_order()
        self.complete_directly(paid_id)
        attempt = royalty.claim_royalty_payment_for_test(paid_id, "test-request-paid")
        self.assertEqual(attempt, 1)
        self.assertTrue(
            royalty.resolve_royalty_payment_for_test(
                paid_id, attempt, "PAID", payment_id="mock-id"
            )
        )
        self.assertFalse(
            royalty.resolve_royalty_payment_for_test(paid_id, attempt, "PAID")
        )
        self.assertEqual(self.obligation(paid_id)[4], "PAID")

        failed_id = self.insert_order()
        self.complete_directly(failed_id)
        failed_attempt = royalty.claim_royalty_payment_for_test(
            failed_id, "test-request-failed"
        )
        self.assertTrue(
            royalty.resolve_royalty_payment_for_test(
                failed_id, failed_attempt, "FAILED", error_class="DECLINED"
            )
        )
        self.assertEqual(
            royalty.royalty_new_trade_block_reason(), "OBLIGATION_FAILED"
        )

        conn = database.db_connect()
        conn.execute(
            "UPDATE royalty_obligations SET status = 'PAID' WHERE order_id = ?",
            (failed_id,),
        )
        conn.commit()
        conn.close()
        timeout_id = self.insert_order()
        self.complete_directly(timeout_id)
        timeout_attempt = royalty.claim_royalty_payment_for_test(
            timeout_id, "test-request-timeout"
        )
        self.assertTrue(
            royalty.resolve_royalty_payment_for_test(
                timeout_id, timeout_attempt, "UNKNOWN", error_class="TIMEOUT"
            )
        )
        self.assertIsNone(
            royalty.claim_royalty_payment_for_test(
                timeout_id, "must-not-retry-unknown"
            )
        )
        self.assertEqual(
            royalty.royalty_new_trade_block_reason(), "OBLIGATION_UNKNOWN"
        )

    async def test_restart_turns_in_flight_attempt_into_unknown_without_retry(self):
        order_id = self.insert_order()
        self.complete_directly(order_id)
        attempt = royalty.claim_royalty_payment_for_test(order_id, "restart-request")
        self.assertEqual(attempt, 1)
        self.assertEqual(royalty.recover_royalty_state_on_startup(now_unix=200), 1)
        self.assertEqual(self.obligation(order_id)[4], "UNKNOWN")
        self.assertIsNone(
            royalty.claim_royalty_payment_for_test(order_id, "second-request")
        )
        conn = database.db_connect()
        attempt_row = conn.execute(
            "SELECT status, error_class FROM royalty_payment_attempts WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(attempt_row, ("UNKNOWN", "PROCESS_RESTART"))

    async def test_forced_process_termination_recovers_payout_without_repay(self):
        order_id = self.insert_order()
        child = r'''
import asyncio
import os
import sys
from types import SimpleNamespace
import database
database.DB_PATH = sys.argv[1]
import settlement

class PaidResponse(dict):
    def get(self, key, default=None):
        if key == "id":
            os._exit(23)
        return super().get(key, default)

async def fake_request(*args, **kwargs):
    return PaidResponse(
        confirmed=True,
        amount=-50999,
        type="lightning",
        hash="ln-test-invoice",
    )

settlement.coinos_request = fake_request
context = SimpleNamespace(bot=SimpleNamespace(send_message=None))
asyncio.run(settlement.auto_pay_buyer_invoice(
    context, int(sys.argv[2]), 202, "ln-test-invoice"
))
'''
        result = subprocess.run(
            [sys.executable, "-c", child, self.database_path, str(order_id)],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        conn = database.db_connect()
        processing = conn.execute(
            "SELECT payout_status, payout_started_at_unix FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(processing[0], "PROCESSING")

        # A legitimate request still inside the threshold remains in flight.
        self.assertEqual(
            settlement.recover_stale_buyer_payouts(
                now_unix=processing[1] + 119, stale_seconds=120
            ),
            0,
        )
        self.assertEqual(
            settlement.recover_stale_buyer_payouts(
                now_unix=processing[1] + 120, stale_seconds=120
            ),
            1,
        )
        conn = database.db_connect()
        recovered = conn.execute(
            "SELECT status, payout_status, payout_error FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(recovered[:2], ("BUYER_INVOICE_RECEIVED", "UNKNOWN"))
        self.assertIn("STALE_PROCESSING_AFTER_RESTART", recovered[2])

        payment = {"id": "already-paid", "ref": "already-paid-ref"}
        self.assertIsNotNone(
            settlement.complete_reconciled_buyer_payout(
                order_id, 202, "ln-test-invoice", payment
            )
        )
        self.assertEqual(self.obligation(order_id)[3], "RECONCILED")

    async def test_unknown_race_post_payment_completion_is_finalized_atomically(self):
        order_id = self.insert_order()
        context = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(return_value=None))
        )
        payment = {
            "id": "confirmed-but-raced",
            "ref": "race-ref",
            "confirmed": True,
            "amount": -50_999,
            "type": "lightning",
            "hash": "ln-test-invoice",
        }

        async def paid_then_recovery_races(*args, **kwargs):
            conn = database.db_connect()
            conn.execute(
                "UPDATE orders SET payout_status = 'UNKNOWN' WHERE id = ?",
                (order_id,),
            )
            conn.commit()
            conn.close()
            return payment

        raced_request = AsyncMock(side_effect=paid_then_recovery_races)
        with patch.object(settlement, "coinos_request", raced_request):
            self.assertTrue(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-test-invoice"
                )
            )
        raced_request.assert_awaited_once()
        conn = database.db_connect()
        state = conn.execute(
            "SELECT status, payout_status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(state, ("COMPLETED", "CONFIRMED"))
        self.assertEqual(self.obligation(order_id)[3], "NORMAL")

    async def test_concurrent_duplicate_completion_creates_one_obligation(self):
        order_id = self.insert_order(side="SELL", payout_status="UNKNOWN")
        barrier = threading.Barrier(2)

        def complete():
            barrier.wait(timeout=5)
            return settlement.complete_manually_confirmed_buyer_payout(
                order_id, "SELL"
            )

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = await asyncio.gather(
                loop.run_in_executor(pool, complete),
                loop.run_in_executor(pool, complete),
            )
        self.assertEqual(sum(result is not None for result in results), 1)
        conn = database.db_connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM royalty_obligations WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    async def test_failure_commit_wins_race_with_new_trade_gate(self):
        completed_id = self.insert_order()
        self.complete_directly(completed_id)
        new_order_id = self.insert_order(
            status="OPEN", payout_status=None, invoice=None
        )
        failure_ready = threading.Event()
        gate_started = threading.Event()
        allow_failure_commit = threading.Event()
        observed = {}

        def record_failure():
            conn = database.db_connect(timeout=30)
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "UPDATE royalty_obligations SET status = 'UNKNOWN' WHERE order_id = ?",
                (completed_id,),
            )
            failure_ready.set()
            gate_started.wait(timeout=5)
            allow_failure_commit.wait(timeout=5)
            conn.commit()
            conn.close()

        def gate_and_accept():
            failure_ready.wait(timeout=5)
            gate_started.set()
            conn = database.db_connect(timeout=30)
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            reason = royalty.royalty_new_trade_block_reason(cursor)
            if reason is None:
                cursor.execute(
                    "UPDATE orders SET status = 'BUYER_ASSIGNED' WHERE id = ?",
                    (new_order_id,),
                )
            observed["reason"] = reason
            conn.commit()
            conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            failure_future = pool.submit(record_failure)
            gate_future = pool.submit(gate_and_accept)
            self.assertTrue(gate_started.wait(timeout=5))
            allow_failure_commit.set()
            failure_future.result(timeout=10)
            gate_future.result(timeout=10)
        self.assertEqual(observed["reason"], "OBLIGATION_UNKNOWN")
        conn = database.db_connect()
        status = conn.execute(
            "SELECT status FROM orders WHERE id = ?", (new_order_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(status, "OPEN")

    async def test_modes_destination_and_overdue_production_gates(self):
        self.assertIsNone(royalty.royalty_new_trade_block_reason())
        self.assertFalse(royalty.royalty_status_snapshot()["production_compliant"])
        with patch.object(royalty, "ROYALTY_DESTINATION", ""):
            self.assertEqual(
                royalty.royalty_new_trade_block_reason(), "INVALID_DESTINATION"
            )

        with patch.object(royalty, "ROYALTY_MODE", "PRODUCTION_ENFORCED"):
            self.assertEqual(
                royalty.royalty_new_trade_block_reason(),
                "PAYMENT_TRANSPORT_DISABLED",
            )
            with patch.object(royalty, "ROYALTY_PAYMENT_TRANSPORT_ENABLED", True), patch("royalty_wallet.configured_wallet"):

                with patch.object(royalty, "ROYALTY_DESTINATION", ""):
                    self.assertEqual(
                        royalty.royalty_new_trade_block_reason(),
                        "INVALID_DESTINATION",
                    )
                order_id = self.insert_order()
                self.complete_directly(order_id, completed_at=100)
                self.assertEqual(
                    royalty.royalty_new_trade_block_reason(now_unix=100 + 72 * 3600),
                    "OVERDUE_DUE",
                )

        with patch.object(
            royalty, "ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED", False
        ):
            self.assertEqual(
                royalty.royalty_new_trade_block_reason(),
                "ACCOUNTING_TEST_NOT_ACKNOWLEDGED",
            )

        with patch.object(settings, "ROYALTY_MODE", "ACCOUNTING_TEST"), patch.object(
            settings, "ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED", False
        ):
            with self.assertRaisesRegex(RuntimeError, "explicit.*acknowledgement"):
                settings.validate_runtime_config()

    async def test_post_migration_missing_obligation_blocks_but_history_does_not(self):
        conn = database.db_connect()
        epoch = int(
            conn.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
                ("royalty_accounting_started_at_unix",),
            ).fetchone()[0]
        )
        old_id = self.insert_order(status="COMPLETED", payout_status="CONFIRMED")
        new_id = self.insert_order(status="COMPLETED", payout_status="CONFIRMED")
        conn.execute(
            "UPDATE orders SET completed_at_unix = ? WHERE id = ?",
            (epoch - 1, old_id),
        )
        conn.execute(
            "UPDATE orders SET completed_at_unix = ? WHERE id = ?",
            (epoch, new_id),
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            royalty.royalty_new_trade_block_reason(),
            "MISSING_ROYALTY_OBLIGATION",
        )
        conn = database.db_connect()
        conn.execute("DELETE FROM orders WHERE id = ?", (new_id,))
        conn.commit()
        conn.close()
        self.assertIsNone(royalty.royalty_new_trade_block_reason())

    async def test_payment_attempt_stores_fingerprint_not_request(self):
        order_id = self.insert_order()
        self.complete_directly(order_id)
        request = "ln-sensitive-payment-request"
        self.assertEqual(
            royalty.claim_royalty_payment_for_test(order_id, request), 1
        )
        conn = database.db_connect()
        stored = conn.execute(
            "SELECT payment_request, payment_request_fingerprint "
            "FROM royalty_payment_attempts WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        conn.close()
        self.assertIsNone(stored[0])
        self.assertEqual(len(stored[1]), 64)

        # Re-running the migration removes raw requests from an older staging
        # schema while preserving the non-reversible fingerprint.
        conn = database.db_connect()
        conn.execute(
            "UPDATE royalty_payment_attempts SET payment_request = ? "
            "WHERE order_id = ?",
            (request, order_id),
        )
        conn.commit()
        conn.close()
        database.init_database()
        conn = database.db_connect()
        migrated = conn.execute(
            "SELECT payment_request, payment_request_fingerprint "
            "FROM royalty_payment_attempts WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        conn.close()
        self.assertIsNone(migrated[0])
        self.assertEqual(migrated[1], stored[1])

    async def test_floor_formula_zero_and_commission_funding(self):
        self.assertEqual(royalty.royalty_amount_sats(999), 0)
        self.assertEqual(royalty.royalty_amount_sats(1_000), 1)
        self.assertEqual(royalty.royalty_amount_sats(50_999), 50)
        self.assertEqual(royalty.operator_commission_sats(50_999, 0.5), 254)
        zero_id = self.insert_order(amount_sats=999)
        self.complete_directly(zero_id)
        self.assertEqual(self.obligation(zero_id)[2:5], (0, "MANUAL", "ZERO"))

    async def test_legacy_schema_migration_is_repeatable_and_does_not_backfill(self):
        legacy_path = os.path.join(self.tempdir.name, "legacy.db")
        legacy = sqlite3.connect(legacy_path)
        legacy.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, created_at TEXT)"
        )
        legacy.execute(
            "INSERT INTO orders VALUES (1, 'COMPLETED', '2025-01-01 00:00:00')"
        )
        legacy.commit()
        legacy.close()
        with patch.object(database, "DB_PATH", legacy_path):
            database.init_database()
            database.init_database()
            conn = database.db_connect()
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            obligation_count = conn.execute(
                "SELECT COUNT(*) FROM royalty_obligations"
            ).fetchone()[0]
            epoch = conn.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
                ("royalty_accounting_started_at_unix",),
            ).fetchone()
            conn.close()
        self.assertIn("completed_at_unix", columns)
        self.assertEqual(obligation_count, 0)
        self.assertIsNotNone(epoch)


if __name__ == "__main__":
    unittest.main()

