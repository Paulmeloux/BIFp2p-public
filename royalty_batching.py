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

def recover_in_flight_batches(conn, now=None):
    """After a crash, quarantine every uncertain batch; never retry it."""
    now = int(time.time()) if now is None else int(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM royalty_batches WHERE status='IN_FLIGHT'"
        ).fetchall()]
        for batch_id in ids:
            conn.execute(
                "UPDATE royalty_batches SET status='UNKNOWN', updated_at_unix=? "
                "WHERE id=? AND status='IN_FLIGHT'", (now, batch_id)
            )
            conn.execute(
                "UPDATE royalty_batch_attempts SET status='UNKNOWN', "
                "resolved_at_unix=? WHERE batch_id=? AND status='IN_FLIGHT'",
                (now, batch_id)
            )
        conn.commit()
        return len(ids)
    except Exception:
        conn.rollback()
        raise


def claim_batch_for_offline_test(conn, batch_id, request_fingerprint, now=None):
    """Reserve exactly one simulated attempt. Does not transmit payments.

    An IN_FLIGHT or UNKNOWN batch cannot be claimed again. The caller must
    supply a stable request fingerprint and an idle SQLite connection.
    """
    if not isinstance(request_fingerprint, str) or len(request_fingerprint) != 64:
        raise ValueError("64-character request fingerprint required")
    if any(ch not in "0123456789abcdef" for ch in request_fingerprint):
        raise ValueError("invalid fingerprint")
    now = int(time.time()) if now is None else int(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE royalty_batches SET status='IN_FLIGHT',updated_at_unix=? "
            "WHERE id=? AND status='PREPARED'", (now, batch_id)
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO royalty_batch_attempts "
            "(batch_id,status,request_fingerprint,started_at_unix) "
            "VALUES (?,'IN_FLIGHT',?,?)",
            (batch_id, request_fingerprint, now)
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise

def submit_claimed_batch_once(conn, batch_id, transfer, now=None):
    """One-shot payment orchestration for a PREPARED batch.

    The caller supplies a transfer callable; no transport is enabled at bot
    startup. A durable IN_FLIGHT claim is committed before calling transfer.
    All outcomes remain UNKNOWN until separately reconciled with Coinos.
    """
    import hashlib
    import json
    from royalty_transport import (
        ROYALTY_BENEFICIARY_USERNAME, royalty_transfer_fingerprint,
    )
    row = conn.execute(
        "SELECT status,destination_kind,destination_snapshot,total_sats "
        "FROM royalty_batches WHERE id=?", (batch_id,)
    ).fetchone()
    if row is None:
        raise ValueError("batch not found")
    status, kind, destination, amount = row
    if status != "PREPARED":
        return None
    if kind != "COINOS_USERNAME" or destination != ROYALTY_BENEFICIARY_USERNAME:
        raise ValueError("royalty beneficiary mismatch")
    fingerprint = royalty_transfer_fingerprint(batch_id, amount)
    if not claim_batch_for_offline_test(conn, batch_id, fingerprint, now):
        return None
    try:
        receipt = transfer(amount)
        if not isinstance(receipt, dict) or not isinstance(
            receipt.get("payment_id"), str
        ):
            raise ValueError("missing remote payment identifier")
        reference = receipt["payment_id"]
    except Exception:
        reference = None
    # HTTP success is not confirmed settlement. Preserve the remote ID
    # for manual reconciliation, and prohibit a second outgoing request.
    mark_batch_unknown(conn, batch_id, now)
    conn.execute(
        "UPDATE royalty_batches SET payment_reference=? WHERE id=? "
        "AND status='UNKNOWN'", (reference, batch_id)
    )
    conn.execute(
        "UPDATE royalty_batch_attempts SET payment_reference=? WHERE "
        "batch_id=? AND status='UNKNOWN'", (reference, batch_id)
    )
    conn.commit()
    return {"status": "UNKNOWN", "payment_reference": reference}
