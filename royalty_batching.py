"""Offline-only royalty batch ledger. No Coinos/network transport.

This module is deliberately not wired into bot startup or order handlers.
Use a dedicated SQLite test connection. Production integration requires review.
"""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS royalty_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 destination_kind TEXT NOT NULL,
 destination_snapshot TEXT NOT NULL,
 total_sats INTEGER NOT NULL CHECK(total_sats > 0),
 status TEXT NOT NULL DEFAULT 'PREPARED'
   CHECK(status IN ('PREPARED','IN_FLIGHT','UNKNOWN','CONFIRMED')),
 created_at_unix INTEGER NOT NULL,
 updated_at_unix INTEGER NOT NULL,
 payment_reference TEXT
);
CREATE TABLE IF NOT EXISTS royalty_batch_items (
 batch_id INTEGER NOT NULL REFERENCES royalty_batches(id) ON DELETE RESTRICT,
 order_id INTEGER NOT NULL UNIQUE REFERENCES royalty_obligations(order_id) ON DELETE RESTRICT,
 royalty_sats INTEGER NOT NULL CHECK(royalty_sats > 0),
 PRIMARY KEY(batch_id, order_id)
);
CREATE TABLE IF NOT EXISTS royalty_batch_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 batch_id INTEGER NOT NULL UNIQUE REFERENCES royalty_batches(id) ON DELETE RESTRICT,
 status TEXT NOT NULL CHECK(status IN ('IN_FLIGHT','UNKNOWN','CONFIRMED')),
 request_fingerprint TEXT NOT NULL UNIQUE,
 started_at_unix INTEGER NOT NULL,
 resolved_at_unix INTEGER,
 payment_reference TEXT
);
"""

def install_batch_schema(conn):
    """Create tables in an explicitly supplied SQLite connection."""
    conn.executescript(SCHEMA)

def create_batch(conn, destination_kind, destination_snapshot, limit=100, now=None):
    """Reserve matching DUE obligations atomically; returns batch ID or None.

    Caller must not already have an open transaction. Only DUE obligations are
    eligible. This avoids accidentally treating ACCOUNTING_TEST as payable.
    """
    if not destination_kind or not destination_snapshot:
        raise ValueError("destination required")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("invalid limit")
    now = int(time.time()) if now is None else int(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute("""
            SELECT r.order_id, r.royalty_sats
            FROM royalty_obligations r
            JOIN orders o ON o.id = r.order_id
            WHERE r.status = 'DUE' AND r.royalty_sats > 0
              AND r.destination_kind = ? AND r.destination_snapshot = ?
              AND o.status = 'COMPLETED'
              AND NOT EXISTS (
                  SELECT 1 FROM royalty_batch_items i WHERE i.order_id = r.order_id
              )
            ORDER BY r.order_id LIMIT ?
        """, (destination_kind, destination_snapshot, limit)).fetchall()
        if not rows:
            conn.commit()
            return None
        total = sum(int(row[1]) for row in rows)
        cur = conn.execute("""
            INSERT INTO royalty_batches
                (destination_kind,destination_snapshot,total_sats,created_at_unix,updated_at_unix)
            VALUES (?,?,?,?,?)
        """, (destination_kind, destination_snapshot, total, now, now))
        batch_id = cur.lastrowid
        conn.executemany("""
            INSERT INTO royalty_batch_items(batch_id,order_id,royalty_sats)
            VALUES (?,?,?)
        """, [(batch_id, int(order_id), int(sats)) for order_id,sats in rows])
        if conn.execute(
            "SELECT SUM(royalty_sats) FROM royalty_batch_items WHERE batch_id=?",
            (batch_id,)
        ).fetchone()[0] != total:
            raise RuntimeError("batch total mismatch")
        conn.commit()
        return batch_id
    except Exception:
        conn.rollback()
        raise

def mark_batch_unknown(conn, batch_id, now=None):
    """Fail closed; never changes UNKNOWN back to PREPARED."""
    now = int(time.time()) if now is None else int(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("""
            UPDATE royalty_batches SET status='UNKNOWN',updated_at_unix=?
            WHERE id=? AND status='IN_FLIGHT'
        """, (now,batch_id))
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise
