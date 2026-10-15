import asyncio
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cancellation
import database
import royalty
import settlement


class FakeCallbackQuery:
    _next_id = 0

    def __init__(self, data, chat_id):
        type(self)._next_id += 1
        self.id = f"security-test-{type(self)._next_id}"
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id)
        self.answer = AsyncMock(return_value=True)
        self.edit_message_text = AsyncMock(return_value=None)


def fake_update(user_id, data):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        callback_query=FakeCallbackQuery(data, user_id),
    )


def fake_context():
    return SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
            delete_message=AsyncMock(return_value=None),
        ),
    )


class BuyerPayoutRecoverySecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.tempdir.name, "security-test.db")
        patches = (
            patch.object(database, "DB_PATH", self.database_path),
            patch.object(royalty, "ROYALTY_MODE", "ACCOUNTING_TEST"),
            patch.object(royalty, "ROYALTY_DESTINATION_KIND", "LIGHTNING_ADDRESS"),
            patch.object(royalty, "ROYALTY_DESTINATION", "royalty@example.test"),
            patch.object(royalty, "ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED", True),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        database.init_database()
        self.addCleanup(self.tempdir.cleanup)

    def insert_order(
        self,
        *,
        status="BUYER_INVOICE_RECEIVED",
        payout_status=None,
        invoice="ln-security-invoice",
        code=None,
    ):
        conn = database.db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders
                (order_code, seller_id, buyer_id, amount_sats, fee_percent,
                 buyer_invoice, order_side, status, payout_status, created_at)
            VALUES (?, 101, 202, 50999, 0.5, ?, 'SELL', ?, ?,
                    '2026-01-01 00:00:00')
            """,
            (code or f"SEC-{threading.get_ident()}", invoice, status, payout_status),
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id

    def order_state(self, order_id):
        conn = database.db_connect()
        row = conn.execute(
            """
            SELECT status, payout_status, payout_started_at_unix,
                   payout_coinos_id, payout_error
            FROM orders WHERE id = ?
            """,
            (order_id,),
        ).fetchone()
        conn.close()
        return row

    def obligation_count(self, order_id):
        conn = database.db_connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM royalty_obligations WHERE order_id = ?",
            (order_id,),
        ).fetchone()[0]
        conn.close()
        return count

    @staticmethod
    def confirmed_payment(invoice="ln-security-invoice", payment_id="paid-1"):
        return {
            "id": payment_id,
            "ref": f"ref-{payment_id}",
            "confirmed": True,
            "amount": -50999,
            "type": "lightning",
            "hash": invoice,
            "created": 2_000_000,
        }

    async def test_inflight_success_after_stale_recovery_completes_locally(self):
        order_id = self.insert_order()
        request_started = asyncio.Event()
        release_response = asyncio.Event()
        payment = self.confirmed_payment()

        async def slow_confirmed_request(*args, **kwargs):
            request_started.set()
            await release_response.wait()
            return payment

        context = fake_context()
        with patch.object(
            settlement, "coinos_request", AsyncMock(side_effect=slow_confirmed_request)
        ) as payment_call:
            payout_task = asyncio.create_task(
                settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-security-invoice"
                )
            )
            await asyncio.wait_for(request_started.wait(), timeout=2)
            processing = self.order_state(order_id)
            self.assertEqual(processing[1], "PROCESSING")
            self.assertEqual(
                settlement.recover_stale_buyer_payouts(
                    now_unix=processing[2] + 120, stale_seconds=120
                ),
                1,
            )
            self.assertEqual(self.order_state(order_id)[1], "UNKNOWN")
            release_response.set()
            self.assertTrue(await asyncio.wait_for(payout_task, timeout=2))

        payment_call.assert_awaited_once_with(
            "POST", "/payments", {"payreq": "ln-security-invoice"}
        )
        state = self.order_state(order_id)
        self.assertEqual(state[0:2], ("COMPLETED", "CONFIRMED"))
        self.assertEqual(state[3], "paid-1")
        self.assertEqual(self.obligation_count(order_id), 1)

    async def test_timeout_and_restart_states_never_resubmit(self):
        order_id = self.insert_order()
        context = fake_context()
        request = AsyncMock(side_effect=TimeoutError("mock timeout after submission"))
        with patch.object(settlement, "coinos_request", request):
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-security-invoice"
                )
            )
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-security-invoice"
                )
            )
        request.assert_awaited_once()
        self.assertEqual(self.order_state(order_id)[1], "UNKNOWN")

        processing_id = self.insert_order(invoice="ln-restart")
        self.assertIsNotNone(
            settlement.claim_buyer_payout(processing_id, 202, "ln-restart")
        )
        never_call = AsyncMock(side_effect=AssertionError("duplicate remote POST"))
        with patch.object(settlement, "coinos_request", never_call):
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, processing_id, 202, "ln-restart"
                )
            )
            started = self.order_state(processing_id)[2]
            self.assertEqual(
                settlement.recover_stale_buyer_payouts(
                    now_unix=started + 120, stale_seconds=120
                ),
                1,
            )
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, processing_id, 202, "ln-restart"
                )
            )
        never_call.assert_not_awaited()

    async def test_confirmed_payment_then_database_open_failure_never_resubmits(self):
        order_id = self.insert_order()
        context = fake_context()
        payment_request = AsyncMock(return_value=self.confirmed_payment())
        real_connect = settlement.db_connect
        connect_count = 0

        def fail_completion_connect(*args, **kwargs):
            nonlocal connect_count
            connect_count += 1
            if connect_count == 2:
                raise RuntimeError("mock local database unavailable")
            return real_connect(*args, **kwargs)

        with patch.object(settlement, "coinos_request", payment_request), patch.object(
            settlement, "db_connect", side_effect=fail_completion_connect
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-security-invoice"
                )
            self.assertFalse(
                await settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-security-invoice"
                )
            )

        payment_request.assert_awaited_once()
        state = self.order_state(order_id)
        self.assertEqual(state[1], "PROCESSING")
        self.assertEqual(
            settlement.recover_stale_buyer_payouts(
                now_unix=state[2] + 120, stale_seconds=120
            ),
            1,
        )
        self.assertEqual(self.order_state(order_id)[1], "UNKNOWN")

    async def test_simultaneous_recovery_workers_transition_once(self):
        order_id = self.insert_order(payout_status="PROCESSING")
        conn = database.db_connect()
        conn.execute(
            "UPDATE orders SET payout_started_at_unix = 100 WHERE id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()
        barrier = threading.Barrier(2)

        def recover():
            barrier.wait(timeout=5)
            return settlement.recover_stale_buyer_payouts(
                now_unix=220, stale_seconds=120
            )

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = await asyncio.gather(
                loop.run_in_executor(pool, recover),
                loop.run_in_executor(pool, recover),
            )
        self.assertEqual(sorted(results), [0, 1])
        self.assertEqual(self.order_state(order_id)[1], "UNKNOWN")

    async def test_concurrent_reconciliation_is_idempotent_and_accounts_once(self):
        order_id = self.insert_order(payout_status="UNKNOWN")
        conn = database.db_connect()
        conn.execute(
            "UPDATE orders SET payout_started_at_unix = 1 WHERE id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()
        both_requests_started = asyncio.Event()
        release_history = asyncio.Event()
        request_count = 0
        history = {"payments": [self.confirmed_payment(payment_id="history-paid")]}

        async def synchronized_history(*args, **kwargs):
            nonlocal request_count
            request_count += 1
            if request_count == 2:
                both_requests_started.set()
            await release_history.wait()
            return history

        context = fake_context()
        with patch.object(settlement, "COINOS_TOKEN", "offline-test-token"), patch.object(
            settlement, "coinos_request", AsyncMock(side_effect=synchronized_history)
        ):
            workers = [
                asyncio.create_task(settlement.reconcile_unknown_buyer_payouts(context))
                for _ in range(2)
            ]
            await asyncio.wait_for(both_requests_started.wait(), timeout=2)
            release_history.set()
            await asyncio.wait_for(asyncio.gather(*workers), timeout=2)
            await settlement.reconcile_unknown_buyer_payouts(context)

        self.assertEqual(request_count, 2)
        self.assertEqual(self.order_state(order_id)[0:2], ("COMPLETED", "CONFIRMED"))
        self.assertEqual(self.obligation_count(order_id), 1)
        self.assertEqual(context.bot.send_message.await_count, 3)

    async def test_live_success_competing_with_reconciliation_completes_once(self):
        order_id = self.insert_order()
        post_started = asyncio.Event()
        release_post = asyncio.Event()
        payment = self.confirmed_payment(payment_id="shared-payment")

        async def payment_transport(method, path, payload=None):
            if method == "POST":
                post_started.set()
                await release_post.wait()
                return payment
            if method == "GET":
                return {"payments": [payment]}
            raise AssertionError(f"unexpected request: {method} {path}")

        context = fake_context()
        request = AsyncMock(side_effect=payment_transport)
        with patch.object(settlement, "COINOS_TOKEN", "offline-test-token"), patch.object(
            settlement, "coinos_request", request
        ):
            payout_task = asyncio.create_task(
                settlement.auto_pay_buyer_invoice(
                    context, order_id, 202, "ln-security-invoice"
                )
            )
            await asyncio.wait_for(post_started.wait(), timeout=2)
            started = self.order_state(order_id)[2]
            payment["created"] = started * 1000
            self.assertEqual(
                settlement.recover_stale_buyer_payouts(
                    now_unix=started + 120, stale_seconds=120
                ),
                1,
            )
            await settlement.reconcile_unknown_buyer_payouts(context)
            release_post.set()
            self.assertFalse(await asyncio.wait_for(payout_task, timeout=2))

        self.assertEqual(request.await_count, 2)
        self.assertEqual(self.order_state(order_id)[0:2], ("COMPLETED", "CONFIRMED"))
        self.assertEqual(self.obligation_count(order_id), 1)
        self.assertEqual(context.bot.send_message.await_count, 3)

    async def test_history_matching_is_fail_closed_under_current_unverified_contract(self):
        invoice = "lightning:LN-SECURITY-INVOICE"
        valid = self.confirmed_payment(invoice="ln-security-invoice")
        self.assertIs(
            settlement._find_confirmed_coinos_payout(
                {"payments": [valid]}, invoice, 50999, 1
            ),
            valid,
        )
        for field, wrong_value in (
            ("confirmed", False),
            ("amount", -50000),
            ("type", "onchain"),
            ("hash", "different-invoice"),
            ("created", 0),
        ):
            candidate = dict(valid)
            candidate[field] = wrong_value
            with self.subTest(field=field):
                self.assertIsNone(
                    settlement._find_confirmed_coinos_payout(
                        {"payments": [candidate]}, invoice, 50999, 120
                    )
                )
        self.assertIsNone(
            settlement._find_confirmed_coinos_payout(
                {"payments": [valid, dict(valid)]}, invoice, 50999, 1
            )
        )

    async def test_failed_new_trade_gate_does_not_block_existing_safety_paths(self):
        gate_source = self.insert_order(
            status="COMPLETED", payout_status="CONFIRMED", code="FAILED-GATE"
        )
        conn = database.db_connect()
        cursor = conn.cursor()
        royalty.create_royalty_obligation(cursor, gate_source, "MANUAL", 100)
        cursor.execute(
            "UPDATE royalty_obligations SET status = 'FAILED' WHERE order_id = ?",
            (gate_source,),
        )
        conn.commit()
        conn.close()
        self.assertEqual(royalty.royalty_new_trade_block_reason(), "OBLIGATION_FAILED")

        settlement_id = self.insert_order(invoice="ln-existing-settlement")
        settlement_context = fake_context()
        with patch.object(
            settlement,
            "coinos_request",
            AsyncMock(
                return_value=self.confirmed_payment(
                    invoice="ln-existing-settlement", payment_id="existing-paid"
                )
            ),
        ):
            self.assertTrue(
                await settlement.auto_pay_buyer_invoice(
                    settlement_context, settlement_id, 202, "ln-existing-settlement"
                )
            )

        cancel_id = self.insert_order(
            status="SELLER_SATS_CONFIRMED", invoice="ln-existing-refund"
        )
        with patch.object(
            cancellation, "notify_escrow_cancellation", AsyncMock(return_value=None)
        ):
            await cancellation.cancel_selected_order(
                fake_update(101, f"cancel_order:{cancel_id}"), fake_context()
            )
        conn = database.db_connect()
        conn.execute(
            "UPDATE orders SET payout_status = 'UNKNOWN' WHERE id = ?", (cancel_id,)
        )
        conn.commit()
        conn.close()
        refund = settlement.complete_reconciled_buyer_payout(
            cancel_id,
            202,
            "ln-existing-refund",
            {"id": "refund-paid", "ref": "refund-ref"},
        )
        self.assertEqual(refund[-1], "REFUNDED")
        self.assertEqual(self.obligation_count(cancel_id), 0)

        manual_id = self.insert_order(payout_status="UNKNOWN", invoice="ln-admin")
        self.assertIsNotNone(
            settlement.complete_manually_confirmed_buyer_payout(manual_id, "SELL")
        )
        self.assertEqual(self.obligation_count(manual_id), 1)

        stale_id = self.insert_order(payout_status="PROCESSING", invoice="ln-stale")
        conn = database.db_connect()
        conn.execute(
            "UPDATE orders SET payout_started_at_unix = 100 WHERE id = ?", (stale_id,)
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            settlement.recover_stale_buyer_payouts(
                now_unix=220, stale_seconds=120
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
