"""Real-schema, offline payment lifecycle and wallet verification tests."""
import asyncio
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
import royalty
import royalty_wallet as wallets
import royalty_worker as worker

PREIMAGE = "12" * 32
PAYMENT_HASH = hashlib.sha256(bytes.fromhex(PREIMAGE)).hexdigest()


class FakeWallet:
    identity = "offline-wallet-1"

    def __init__(self, proof=True, timeout=False):
        self.calls = []
        self.lookups = []
        self.proof = proof
        self.timeout = timeout

    def pay_invoice(self, invoice):
        self.calls.append(invoice)
        if self.timeout:
            raise TimeoutError("remote may have accepted")

    def lookup_payment(self, payment_hash):
        self.lookups.append(payment_hash)
        if self.proof:
            return wallets.PaymentEvidence(payment_hash, 50, PREIMAGE)
        return None


class RoyaltyWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(database, "DB_PATH", str(Path(self.tmp.name) / "test.db")),
            patch.object(royalty, "ROYALTY_MODE", "PRODUCTION_ENFORCED"),
            patch.object(royalty, "ROYALTY_PAYMENT_TRANSPORT_ENABLED", True),
            patch.object(royalty, "ROYALTY_DESTINATION_KIND", "LIGHTNING_ADDRESS"),
            patch.object(royalty, "ROYALTY_DESTINATION", wallets.BENEFICIARY),
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network in tests")),
        ]
        for p in self.patches:
            p.start()
        database.init_database()
        self.wallet = FakeWallet()
        self.now = int(time.time())
        self.invoice = wallets.RoyaltyInvoice("offline-invoice", PAYMENT_HASH, 50, self.now + 3600)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def add_trade(self, amount=50000, status="COMPLETED", completed=None):
        completed = self.now if completed is None else completed
        conn = database.db_connect()
        try:
            oid = conn.execute("INSERT INTO orders(amount_sats,fee_percent,status,completed_at_unix) "
                               "VALUES (?,1,?,?)", (amount, status, completed)).lastrowid
            if status == "COMPLETED":
                royalty.create_royalty_obligation(conn.cursor(), oid, "NORMAL", completed)
            conn.commit()
            return oid
        finally:
            conn.close()

    def row(self, oid):
        conn = database.db_connect()
        try:
            return conn.execute("SELECT status FROM royalty_obligations WHERE order_id=?", (oid,)).fetchone()[0]
        finally:
            conn.close()

    async def run_once(self, now=None, wallet=None):
        with patch.object(worker, "resolve_invoice", return_value=self.invoice):
            await worker.process_royalties_once(wallet or self.wallet, self.now if now is None else now)

    async def test_one_trade_one_send_and_independent_proof(self):
        oid = self.add_trade()
        await self.run_once()
        await self.run_once()
        self.assertEqual(len(self.wallet.calls), 1)
        self.assertEqual(self.wallet.lookups, [PAYMENT_HASH])
        self.assertEqual(self.row(oid), "PAID")
        conn = database.db_connect()
        self.assertEqual(conn.execute("SELECT payment_request,invoice_payment_hash FROM royalty_payment_attempts").fetchone(),
                         (None, PAYMENT_HASH))
        conn.close()

    async def test_two_equal_trades_require_distinct_invoice_hashes(self):
        one, two = self.add_trade(), self.add_trade()
        await self.run_once()
        self.assertEqual(len(self.wallet.calls), 1)
        self.assertEqual(self.row(one), "PAID")
        self.assertEqual(self.row(two), "FAILED")

    async def test_separate_trades_are_never_aggregated(self):
        one, two = self.add_trade(), self.add_trade()
        preimage2 = "34" * 32
        hash2 = hashlib.sha256(bytes.fromhex(preimage2)).hexdigest()
        inv2 = wallets.RoyaltyInvoice("offline-invoice-2", hash2, 50, self.now + 3600)
        with patch.object(worker, "resolve_invoice", side_effect=[self.invoice, inv2]), patch.object(
            self.wallet, "lookup_payment", side_effect=lambda h: wallets.PaymentEvidence(h, 50, PREIMAGE if h == PAYMENT_HASH else preimage2)
        ):
            await worker.process_royalties_once(self.wallet, self.now)
        self.assertEqual([i.amount_sats for i in self.wallet.calls], [50, 50])
        self.assertEqual([self.row(one), self.row(two)], ["PAID", "PAID"])

    async def test_timeout_reconciles_success_without_second_send(self):
        oid = self.add_trade()
        self.wallet.timeout, self.wallet.proof = True, False
        await self.run_once()
        self.assertEqual(self.row(oid), "UNKNOWN")
        await self.run_once()
        self.assertEqual(len(self.wallet.calls), 1)
        self.wallet.proof = True
        await self.run_once()
        self.assertEqual(self.row(oid), "PAID")
        self.assertEqual(len(self.wallet.calls), 1)

    async def test_wrong_amount_or_preimage_cannot_mark_paid(self):
        oid = self.add_trade()
        for proof in [wallets.PaymentEvidence(PAYMENT_HASH, 51, PREIMAGE),
                      wallets.PaymentEvidence(PAYMENT_HASH, 50, "ff" * 32)]:
            with patch.object(self.wallet, "lookup_payment", return_value=proof):
                await self.run_once()
                self.assertEqual(self.row(oid), "UNKNOWN")
        self.assertEqual(len(self.wallet.calls), 1)

    async def test_restart_after_persistence_never_resends(self):
        oid = self.add_trade()
        attempt, _ = worker._claim(oid, self.wallet.identity, self.now)
        worker._prepare(oid, attempt, self.invoice, self.now)
        royalty.recover_royalty_state_on_startup(self.now)
        self.wallet.proof = False
        await self.run_once()
        self.assertEqual(self.wallet.calls, [])
        self.assertEqual(self.row(oid), "UNKNOWN")
        self.wallet.proof = True
        await self.run_once()
        self.assertEqual(self.row(oid), "PAID")

    async def test_crash_before_submission_can_retry_but_legacy_cannot(self):
        oid = self.add_trade()
        worker._claim(oid, self.wallet.identity, self.now)
        royalty.recover_royalty_state_on_startup(self.now)
        await self.run_once()
        self.assertEqual(self.row(oid), "FAILED")
        await self.run_once(self.now + 60)
        self.assertEqual(len(self.wallet.calls), 1)

    async def test_legacy_unknown_without_wallet_identity_is_never_retried(self):
        oid = self.add_trade()
        conn = database.db_connect()
        conn.execute("UPDATE royalty_obligations SET status='UNKNOWN' WHERE order_id=?", (oid,))
        conn.execute("INSERT INTO royalty_payment_attempts "
                     "(order_id,attempt_number,status,started_at_unix) VALUES (?,1,'UNKNOWN',?)", (oid, self.now))
        conn.commit()
        conn.close()
        await self.run_once()
        self.assertEqual(self.wallet.calls, [])
        self.assertEqual(self.row(oid), 'UNKNOWN')

    async def test_preparation_failure_is_safe_to_retry_after_backoff(self):
        oid = self.add_trade()
        with patch.object(worker, "resolve_invoice", side_effect=ValueError("bad response")):
            await worker.process_royalties_once(self.wallet, self.now)
        self.assertEqual(self.row(oid), "FAILED")
        self.assertEqual(self.wallet.calls, [])
        await self.run_once()
        self.assertEqual(self.wallet.calls, [])
        await self.run_once(self.now + 61)
        self.assertEqual(self.row(oid), "PAID")

    async def test_concurrent_workers_only_send_once(self):
        oid = self.add_trade()
        with patch.object(worker, "resolve_invoice", return_value=self.invoice):
            await asyncio.gather(worker.process_royalties_once(self.wallet, self.now),
                                 worker.process_royalties_once(self.wallet, self.now))
        self.assertEqual(len(self.wallet.calls), 1)
        self.assertEqual(self.row(oid), "PAID")

    async def test_disabled_and_test_modes_never_send(self):
        oid = self.add_trade()
        with patch.object(royalty, "ROYALTY_PAYMENT_TRANSPORT_ENABLED", False):
            await self.run_once()
        with patch.object(royalty, "ROYALTY_MODE", "ACCOUNTING_TEST"):
            await self.run_once()
        self.assertEqual(self.wallet.calls, [])
        self.assertEqual(self.row(oid), "DUE")

    async def test_accounting_obligations_not_converted_to_live_payments(self):
        with patch.object(royalty, "ROYALTY_MODE", "ACCOUNTING_TEST"):
            oid = self.add_trade()
        await self.run_once()
        self.assertEqual(self.wallet.calls, [])
        self.assertEqual(self.row(oid), "ACCOUNTED")

    async def test_refunded_changed_and_zero_trades_not_paid(self):
        self.add_trade(status="REFUNDED")
        zero = self.add_trade(amount=999)
        changed = self.add_trade()
        conn = database.db_connect()
        conn.execute("UPDATE orders SET status='REFUNDED' WHERE id=?", (changed,))
        conn.commit()
        conn.close()
        await self.run_once()
        self.assertEqual(self.wallet.calls, [])
        self.assertEqual(self.row(zero), "ZERO")
        self.assertEqual(self.row(changed), "ZERO")

    async def test_deadline_all_unresolved_states_and_success_reopens(self):
        oid = self.add_trade()
        for status in ['DUE', 'FAILED', 'UNKNOWN', 'PAYING', 'CONFIG_ERROR']:
            conn = database.db_connect()
            conn.execute("UPDATE royalty_obligations SET status=? WHERE order_id=?", (status, oid))
            conn.commit()
            conn.close()
            with patch("royalty_wallet.configured_wallet", return_value=self.wallet):
                self.assertIsNone(royalty.royalty_new_trade_block_reason(now_unix=self.now + 259199))
                self.assertEqual(royalty.royalty_new_trade_block_reason(now_unix=self.now + 259200), "OVERDUE_" + status)

    async def test_overdue_gate_does_not_prevent_existing_settlement(self):
        import settlement
        self.add_trade(completed=self.now - 259201)
        conn = database.db_connect()
        oid = conn.execute("INSERT INTO orders(amount_sats,fee_percent,status,payout_status,buyer_invoice,order_side) "
                           "VALUES (50000,1,'BUYER_INVOICE_RECEIVED','UNKNOWN','offline-invoice','SELL')").lastrowid
        conn.commit()
        conn.close()
        with patch('royalty_wallet.configured_wallet', return_value=self.wallet):
            self.assertEqual(royalty.royalty_new_trade_block_reason(now_unix=self.now), 'OVERDUE_DUE')
        self.assertIsNotNone(settlement.complete_manually_confirmed_buyer_payout(oid, 'SELL'))
        self.assertEqual(self.row(oid), 'DUE')

    async def test_verified_late_payment_reopens_new_trade_gate(self):
        oid = self.add_trade(completed=self.now - 259201)
        with patch('royalty_wallet.configured_wallet', return_value=self.wallet):
            self.assertEqual(royalty.royalty_new_trade_block_reason(now_unix=self.now), 'OVERDUE_DUE')
            await self.run_once()
            self.assertEqual(self.row(oid), 'PAID')
            self.assertIsNone(royalty.royalty_new_trade_block_reason(now_unix=self.now))

    async def test_wallet_change_does_not_lookup_or_resend(self):
        oid = self.add_trade()
        self.wallet.proof = False
        await self.run_once()
        self.wallet.identity = "different-wallet"
        self.wallet.lookups.clear()
        await self.run_once()
        self.assertEqual(self.wallet.lookups, [])
        self.assertEqual(len(self.wallet.calls), 1)
        self.assertEqual(self.row(oid), "UNKNOWN")

    async def test_success_no_notice_incident_once_and_overdue_notice(self):
        self.add_trade()
        await self.run_once()
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await worker.notify_royalty_incidents(app, self.now)
        app.bot.send_message.assert_not_awaited()
        oid = self.add_trade()
        conn = database.db_connect()
        conn.execute("UPDATE royalty_obligations SET status='UNKNOWN' WHERE order_id=?", (oid,))
        conn.commit()
        conn.close()
        await worker.notify_royalty_incidents(app, self.now)
        await worker.notify_royalty_incidents(app, self.now)
        self.assertEqual(app.bot.send_message.await_count, 1)
        self.assertIn("paulmeloux@proton.me", app.bot.send_message.call_args.kwargs['text'])
        await worker.notify_royalty_incidents(app, self.now + 259200)
        self.assertEqual(app.bot.send_message.await_count, 2)


class WalletTests(unittest.TestCase):
    def test_lnbits_uses_separate_get_and_outgoing_record(self):
        wallet = wallets.LNbitsWallet("https://wallet.example", "offline-key")
        invoice = wallets.RoyaltyInvoice("test-invoice", PAYMENT_HASH, 50, 9999999999)
        reply = {"paid": True, "preimage": PREIMAGE,
                 "details": {"payment_hash": PAYMENT_HASH, "amount": -50000}}
        with patch.object(wallets, "request_json", side_effect=[{"payment_hash": PAYMENT_HASH}, reply]) as request:
            self.assertIsNone(wallet.pay_invoice(invoice))
            proof = wallet.lookup_payment(PAYMENT_HASH)
            self.assertTrue(wallets.verified_evidence(proof, PAYMENT_HASH, 50))
            self.assertEqual(request.call_args_list[0].kwargs['payload'], {"out": True, "bolt11": "test-invoice"})
            self.assertNotIn('payload', request.call_args_list[1].kwargs)
        for amount in [50000, -50001, "-50000", True]:
            reply['details']['amount'] = amount
            with patch.object(wallets, "request_json", return_value=reply):
                self.assertIsNone(wallet.lookup_payment(PAYMENT_HASH))

    def test_endpoint_validation(self):
        for url in ['http://wallet.example', 'https://key@wallet.example', 'https://wallet.example?key=x']:
            with self.assertRaises(ValueError):
                wallets.LNbitsWallet(url, "offline-key")

    def test_fixed_beneficiary_and_bolt11_validation(self):
        from bolt11 import Bolt11, Tags, TagChar, encode
        from bolt11.models.tags import Tag
        now = int(time.time())
        metadata = '[["text/plain","BIF royalty"]]'
        def invoice(amount=50000, date=now, description=None):
            return encode(Bolt11(currency="bc", date=date, amount_msat=amount, tags=Tags([
                Tag(TagChar.payment_hash, PAYMENT_HASH), Tag(TagChar.payment_secret, "33" * 32),
                Tag(TagChar.description_hash, description or hashlib.sha256(metadata.encode()).hexdigest()),
            ])), private_key="11" * 32)
        params = {"tag": "payRequest", "metadata": metadata, "minSendable": 1000,
                  "maxSendable": 100000, "callback": "https://coinos.io/lnurl/callback"}
        for pr, valid in [(invoice(), True), (invoice(51000), False), (invoice(date=now - 5000), False),
                          (invoice(description='00' * 32), False), ('lnbc-broken', False)]:
            with patch.object(wallets, "request_json", side_effect=[params, {"pr": pr}]):
                if valid:
                    result = wallets.resolve_invoice(50)
                    self.assertEqual(result.payment_hash, PAYMENT_HASH)
                else:
                    with self.assertRaises(Exception):
                        wallets.resolve_invoice(50)
        with patch.object(wallets, "request_json", return_value={**params, 'callback': 'https://evil.example/pay'}):
            with self.assertRaises(ValueError):
                wallets.resolve_invoice(50)


if __name__ == '__main__':
    unittest.main()
