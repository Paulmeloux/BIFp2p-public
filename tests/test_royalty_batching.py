"""Offline tests for the isolated royalty batching ledger."""
import sqlite3
import unittest
from royalty_batching import install_batch_schema, create_batch, mark_batch_unknown

class BatchLedgerTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE orders(id INTEGER PRIMARY KEY,status TEXT NOT NULL);
            CREATE TABLE royalty_obligations(
                order_id INTEGER PRIMARY KEY REFERENCES orders(id),
                status TEXT NOT NULL, royalty_sats INTEGER NOT NULL,
                destination_kind TEXT, destination_snapshot TEXT
            );
        """)
        install_batch_schema(self.db)

    def tearDown(self):
        self.db.close()

    def add(self, oid, status="COMPLETED", royalty_status="DUE",
            amount=10, destination="alice"):
        self.db.execute("INSERT INTO orders VALUES (?,?)",(oid,status))
        self.db.execute(
            "INSERT INTO royalty_obligations VALUES (?,?,?,?,?)",
            (oid,royalty_status,amount,"COINOS_USERNAME",destination)
        )
        self.db.commit()

    def test_immutable_exact_sum_and_no_duplicate(self):
        self.add(1,amount=7)
        self.add(2,amount=11)
        batch=create_batch(self.db,"COINOS_USERNAME","alice")
        self.assertEqual(self.db.execute(
            "SELECT total_sats FROM royalty_batches WHERE id=?",(batch,)
        ).fetchone()[0],18)
        self.assertIsNone(create_batch(self.db,"COINOS_USERNAME","alice"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO royalty_batch_items VALUES (?,?,?)",(999,1,7)
            )

    def test_excludes_refunds_tests_and_other_destinations(self):
        self.add(1,status="REFUNDED")
        self.add(2,royalty_status="ACCOUNTED")
        self.add(3,destination="bob")
        self.add(4)
        batch=create_batch(self.db,"COINOS_USERNAME","alice")
        self.assertEqual(self.db.execute(
            "SELECT order_id FROM royalty_batch_items WHERE batch_id=?",(batch,)
        ).fetchall(),[(4,)])

    def test_no_payment_transport_and_fail_closed(self):
        self.add(1)
        batch=create_batch(self.db,"COINOS_USERNAME","alice")
        self.assertFalse(mark_batch_unknown(self.db,batch))
        self.db.execute("UPDATE royalty_batches SET status='IN_FLIGHT' WHERE id=?",(batch,))
        self.db.commit()
        self.assertTrue(mark_batch_unknown(self.db,batch))
        self.assertFalse(mark_batch_unknown(self.db,batch))
        self.assertEqual(self.db.execute(
            "SELECT status FROM royalty_batches WHERE id=?",(batch,)
        ).fetchone()[0],"UNKNOWN")

    def test_invalid_inputs_do_not_reserve(self):
        self.add(1)
        with self.assertRaises(ValueError):
            create_batch(self.db,"COINOS_USERNAME","alice",limit=0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM royalty_batch_items"
        ).fetchone()[0],0)

if __name__ == "__main__":
    unittest.main()
