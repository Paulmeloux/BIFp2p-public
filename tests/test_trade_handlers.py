import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cancellation
import database
import order_acceptance
import order_creation
import royalty
import settlement
import trade_flow


class FakeCallbackQuery:
    _next_id = 0

    def __init__(self, data, chat_id):
        type(self)._next_id += 1
        self.id = f"handler-test-{type(self)._next_id}"
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id)
        self.answer = AsyncMock(return_value=True)
        self.edit_message_text = AsyncMock(return_value=None)
        self.edit_message_reply_markup = AsyncMock(return_value=None)


def fake_update(user_id, data):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username=f"user{user_id}"),
        callback_query=FakeCallbackQuery(data, user_id),
    )


def fake_context(user_data=None):
    return SimpleNamespace(
        user_data={} if user_data is None else user_data,
        bot=SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=700)),
            delete_message=AsyncMock(return_value=None),
        ),
    )


class TradeHandlerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.tempdir.name, "handlers.db")
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

    def insert_open_order(self, side):
        conn = database.db_connect()
        cursor = conn.cursor()
        seller_id = 101 if side == "SELL" else None
        buyer_id = 202 if side == "BUY" else None
        cursor.execute(
            """
            INSERT INTO orders
                (order_code, seller_id, buyer_id, amount_sats, price_type,
                 price_toman, description, fee_percent, order_side,
                 open_generation, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"OPEN-{side}", seller_id, buyer_id, 50_000, "fixed",
                2_000_000, "test", 1.0, side, 1, "OPEN",
                "2026-01-01 00:00:00",
            ),
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id

    def fail_royalty_gate(self):
        conn = database.db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders
                (order_code, seller_id, buyer_id, amount_sats, fee_percent,
                 order_side, status, completed_at_unix, created_at)
            VALUES ('FAILED-ROYALTY', 11, 12, 50000, 1, 'SELL',
                    'COMPLETED', 2000000000, '2026-01-01 00:00:00')
            """
        )
        order_id = cursor.lastrowid
        royalty.create_royalty_obligation(cursor, order_id, "MANUAL", 2000000000)
        cursor.execute(
            "UPDATE royalty_obligations SET status = 'FAILED' WHERE order_id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()

    async def test_actual_buy_and_sell_creation_handlers(self):
        sell_context = fake_context(
            {
                "amount": 40_000,
                "price_type": "fixed",
                "price": 1_500_000,
                "description": "sell",
            }
        )
        buy_context = fake_context(
            {
                "buy_amount": 60_000,
                "buy_price_type": "fixed",
                "buy_price": 2_500_000,
                "buy_description": "buy",
            }
        )
        with patch.object(
            order_creation, "require_order_eligibility", AsyncMock(return_value=True)
        ), patch.object(
            order_creation, "publish_sell_order", AsyncMock(return_value=501)
        ), patch.object(
            order_creation, "publish_buy_order", AsyncMock(return_value=502)
        ):
            await order_creation.confirm_order(
                fake_update(101, "confirm_order"), sell_context
            )
            await order_creation.confirm_buy_order(
                fake_update(202, "confirm_buy_order"), buy_context
            )

        conn = database.db_connect()
        rows = conn.execute(
            "SELECT order_side, status, amount_sats, fee_percent "
            "FROM orders ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertEqual(
            rows,
            [
                ("SELL", "OPEN", 40_000, 1.0),
                ("BUY", "OPEN", 60_000, 1.0),
            ],
        )

    async def test_actual_buy_and_sell_acceptance_recompute_fee_in_transaction(self):
        sell_order = self.insert_open_order("SELL")
        buy_order = self.insert_open_order("BUY")
        snapshot = {
            "amount_sats": 50_000,
            "price_type": "fixed",
            "offset": None,
            "market_price": None,
            "final_price": None,
            "fiat_total": 2_000_000,
        }

        def points_inside_transaction(cursor, user_id):
            self.assertTrue(cursor.connection.in_transaction)
            return 100_000

        with patch.object(
            order_acceptance, "parse_open_order_callback",
            side_effect=[(sell_order, 1), (buy_order, 1)],
        ), patch.object(
            order_acceptance, "require_order_eligibility", AsyncMock(return_value=True)
        ), patch.object(
            order_acceptance, "acceptance_price_snapshot", AsyncMock(return_value=snapshot)
        ), patch.object(
            order_acceptance, "channel_membership_points", AsyncMock(return_value=0)
        ), patch.object(
            order_acceptance, "historical_reputation_points_with_cursor",
            side_effect=points_inside_transaction,
        ), patch.object(
            order_acceptance, "send_coinos_escrow_invoice", AsyncMock(return_value=True)
        ):
            await order_acceptance.confirm_sell_order(
                fake_update(303, "signed-sell-callback"), fake_context()
            )
            await order_acceptance.confirm_buy_order_as_seller(
                fake_update(404, "signed-buy-callback"), fake_context()
            )

        conn = database.db_connect()
        sell_row = conn.execute(
            "SELECT buyer_id, status, fee_percent FROM orders WHERE id = ?",
            (sell_order,),
        ).fetchone()
        buy_row = conn.execute(
            "SELECT seller_id, status, fee_percent FROM orders WHERE id = ?",
            (buy_order,),
        ).fetchone()
        conn.close()
        self.assertEqual(sell_row, (303, "BUYER_ASSIGNED", 0.9))
        self.assertEqual(buy_row, (404, "SELLER_ASSIGNED", 0.9))

    async def test_all_actual_new_trade_handlers_fail_closed(self):
        sell_order = self.insert_open_order("SELL")
        buy_order = self.insert_open_order("BUY")
        self.fail_royalty_gate()
        snapshot = {
            "amount_sats": 50_000,
            "price_type": "fixed",
            "offset": None,
            "market_price": None,
            "final_price": None,
            "fiat_total": 2_000_000,
        }
        sell_context = fake_context(
            {"amount": 40_000, "price_type": "fixed", "price": 1, "description": "x"}
        )
        buy_context = fake_context(
            {"buy_amount": 40_000, "buy_price_type": "fixed", "buy_price": 1,
             "buy_description": "x"}
        )
        with patch.object(
            order_creation, "require_order_eligibility", AsyncMock(return_value=True)
        ), patch.object(
            order_acceptance, "require_order_eligibility", AsyncMock(return_value=True)
        ), patch.object(
            order_acceptance, "acceptance_price_snapshot", AsyncMock(return_value=snapshot)
        ), patch.object(
            order_acceptance, "parse_open_order_callback",
            side_effect=[(sell_order, 1), (buy_order, 1)],
        ), patch.object(
            order_acceptance, "channel_membership_points", AsyncMock(return_value=0)
        ):
            await order_creation.confirm_order(
                fake_update(501, "confirm_order"), sell_context
            )
            await order_creation.confirm_buy_order(
                fake_update(502, "confirm_buy_order"), buy_context
            )
            await order_acceptance.confirm_sell_order(
                fake_update(503, "signed-sell"), fake_context()
            )
            await order_acceptance.confirm_buy_order_as_seller(
                fake_update(504, "signed-buy"), fake_context()
            )

        conn = database.db_connect()
        rows = conn.execute(
            "SELECT id, status FROM orders WHERE id IN (?, ?) ORDER BY id",
            (sell_order, buy_order),
        ).fetchall()
        ordinary_order_count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE order_code != 'FAILED-ROYALTY'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(rows, [(sell_order, "OPEN"), (buy_order, "OPEN")])
        self.assertEqual(ordinary_order_count, 2)

    async def test_admin_authorization_and_malformed_manual_callbacks(self):
        helper = Mock(return_value=None)
        with patch.object(trade_flow, "ADMIN_ID", 999), patch.object(
            trade_flow, "complete_manually_confirmed_buyer_payout", helper
        ):
            await trade_flow.confirm_buyer_sats_sent(
                fake_update(100, "buyer_sats_sent:1"), fake_context()
            )
            await trade_flow.confirm_buy_buyer_sats_sent(
                fake_update(999, "buy_buyer_sats_sent:not-a-number"), fake_context()
            )
            await trade_flow.confirm_buyer_sats_sent(
                fake_update(999, "buyer_sats_sent:not-a-number"), fake_context()
            )
        helper.assert_not_called()

    async def test_actual_cancellation_then_refund_is_not_completion(self):
        conn = database.db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders
                (order_code, seller_id, buyer_id, amount_sats, fee_percent,
                 buyer_invoice, order_side, status, created_at)
            VALUES ('REFUND-PATH', 101, 202, 50000, 1, 'ln-refund-handler',
                    'SELL', 'SELLER_SATS_CONFIRMED', '2026-01-01 00:00:00')
            """
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        with patch.object(
            cancellation, "notify_escrow_cancellation", AsyncMock(return_value=None)
        ):
            await cancellation.cancel_selected_order(
                fake_update(101, f"cancel_order:{order_id}"), fake_context()
            )
        conn = database.db_connect()
        conn.execute(
            "UPDATE orders SET payout_status = 'UNKNOWN' WHERE id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()
        result = settlement.complete_reconciled_buyer_payout(
            order_id, 202, "ln-refund-handler", {"id": "refund", "ref": "refund-ref"}
        )
        self.assertEqual(result[-1], "REFUNDED")
        conn = database.db_connect()
        row = conn.execute(
            "SELECT status, completed_at_unix, refunded_at_unix FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        obligation_count = conn.execute(
            "SELECT COUNT(*) FROM royalty_obligations WHERE order_id = ?",
            (order_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(row[0:2], ("REFUNDED", None))
        self.assertIsNotNone(row[2])
        self.assertEqual(obligation_count, 0)


if __name__ == "__main__":
    unittest.main()
