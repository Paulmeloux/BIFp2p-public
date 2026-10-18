import unittest
from royalty_per_trade import (
    ROYALTY_ADDRESS, royalty_for_completed_trade,
    validate_exact_invoice, validate_wallet_adapter,
)


class PerTradeRoyaltyTests(unittest.TestCase):
    def test_each_completed_trade_has_own_royalty(self):
        a = royalty_for_completed_trade(11, 100000, "COMPLETED")
        b = royalty_for_completed_trade(12, 50000, "COMPLETED")
        self.assertEqual((a.order_id, a.amount_sats, a.destination),
                         (11, 100, ROYALTY_ADDRESS))
        self.assertEqual((b.order_id, b.amount_sats), (12, 50))
        self.assertNotEqual(a.order_id, b.order_id)

    def test_refunds_and_zero_do_not_pay(self):
        self.assertIsNone(royalty_for_completed_trade(1, 100000, "REFUNDED"))
        self.assertIsNone(royalty_for_completed_trade(1, 999, "COMPLETED"))

    def test_exact_invoice_amount_required(self):
        self.assertEqual(validate_exact_invoice("invoice", 100, lambda _: 100),
                         "invoice")
        with self.assertRaises(ValueError):
            validate_exact_invoice("invoice", 100, lambda _: 101)

    def test_wallet_must_support_payment_lookup(self):
        class Unsupported:
            def pay_invoice(self, invoice):
                pass
        with self.assertRaises(TypeError):
            validate_wallet_adapter(Unsupported())


if __name__ == "__main__":
    unittest.main()
