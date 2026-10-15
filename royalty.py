"""Durable royalty accounting and fail-closed new-trade gating.

No production payment transport exists in this phase.  The state-transition
helpers are intentionally transport-neutral so they can be tested offline and
later connected only after Coinos reconciliation guarantees are verified.
"""

import hashlib
import re
import time
from decimal import Decimal, InvalidOperation

from database import db_connect
from settings import (
    ROYALTY_DESTINATION,
    ROYALTY_DESTINATION_KIND,
    ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED,
    ROYALTY_MODE,
    ROYALTY_SETTLEMENT_GRACE_SECONDS,
)


ROYALTY_DENOMINATOR = 1000
ROYALTY_PAYMENT_TRANSPORT_ENABLED = False
ROYALTY_MODES = ("ACCOUNTING_TEST", "PRODUCTION_ENFORCED")
ROYALTY_BLOCKING_STATUSES = ("CONFIG_ERROR", "FAILED", "UNKNOWN")


def royalty_amount_sats(original_amount_sats):
    return max(int(original_amount_sats), 0) // ROYALTY_DENOMINATOR


def operator_commission_sats(original_amount_sats, fee_percent):
    try:
        value = Decimal(str(fee_percent))
    except (InvalidOperation, TypeError, ValueError):
        value = Decimal("1.0")
    if value < Decimal("0.5") or value > Decimal("1.0"):
        value = Decimal("1.0")
    return int(Decimal(int(original_amount_sats)) * value / Decimal("100"))


def royalty_destination_is_valid(kind=None, destination=None):
    kind = ROYALTY_DESTINATION_KIND if kind is None else str(kind).strip().upper()
    destination = (
        ROYALTY_DESTINATION if destination is None else str(destination).strip()
    )
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", kind or ""):
        return False
    if not destination or len(destination) > 512:
        return False
    if destination.upper().startswith("REPLACE_WITH_"):
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in destination)


def create_royalty_obligation(
    cursor, order_id, completion_source, completed_at_unix=None
):
    """Create the one obligation for a legitimately completed trade.

    The caller must hold the same SQLite transaction that changes the order to
    COMPLETED.  Refund/cancellation callers must not invoke this function.
    """
    if completion_source not in ("NORMAL", "RECONCILED", "MANUAL"):
        raise ValueError("invalid royalty completion source")

    row = cursor.execute(
        """
        SELECT amount_sats, fee_percent, status
        FROM orders
        WHERE id = ?
        """,
        (order_id,),
    ).fetchone()
    if row is None or row[2] != "COMPLETED":
        raise RuntimeError("royalty obligation requires a completed order")

    original_amount, fee_percent, _ = row
    royalty_sats = royalty_amount_sats(original_amount)
    commission_sats = operator_commission_sats(original_amount, fee_percent)
    now_unix = int(time.time()) if completed_at_unix is None else int(completed_at_unix)
    destination_valid = royalty_destination_is_valid()

    failure_code = None
    if royalty_sats == 0:
        status = "ZERO"
    elif royalty_sats > commission_sats:
        status = "CONFIG_ERROR"
        failure_code = "INSUFFICIENT_OPERATOR_COMMISSION"
    elif not destination_valid:
        status = "CONFIG_ERROR"
        failure_code = "INVALID_DESTINATION"
    elif ROYALTY_MODE == "ACCOUNTING_TEST":
        status = "ACCOUNTED"
    else:
        status = "DUE"

    destination_kind = ROYALTY_DESTINATION_KIND if destination_valid else None
    destination_snapshot = ROYALTY_DESTINATION if destination_valid else None
    cursor.execute(
        """
        INSERT INTO royalty_obligations
            (order_id, original_amount_sats, operator_commission_sats,
             royalty_sats, destination_kind, destination_snapshot, mode,
             completion_source, status, settlement_due_at_unix,
             created_at_unix, updated_at_unix, failure_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO NOTHING
        """,
        (
            order_id,
            original_amount,
            commission_sats,
            royalty_sats,
            destination_kind,
            destination_snapshot,
            ROYALTY_MODE,
            completion_source,
            status,
            now_unix + ROYALTY_SETTLEMENT_GRACE_SECONDS,
            now_unix,
            now_unix,
            failure_code,
        ),
    )
    inserted = cursor.rowcount == 1
    existing = cursor.execute(
        """
        SELECT original_amount_sats, operator_commission_sats, royalty_sats,
               completion_source
        FROM royalty_obligations
        WHERE order_id = ?
        """,
        (order_id,),
    ).fetchone()
    expected = (original_amount, commission_sats, royalty_sats, completion_source)
    if existing != expected:
        raise RuntimeError("existing royalty obligation does not match completed trade")
    return inserted


def _royalty_new_trade_block_reason(cursor, now_unix=None):
    now_unix = int(time.time()) if now_unix is None else int(now_unix)
    if ROYALTY_MODE not in ROYALTY_MODES:
        return "INVALID_MODE"
    if ROYALTY_MODE == "ACCOUNTING_TEST" and not ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED:
        return "ACCOUNTING_TEST_NOT_ACKNOWLEDGED"

    blocked = cursor.execute(
        """
        SELECT status
        FROM royalty_obligations
        WHERE status IN (?, ?, ?)
        ORDER BY order_id
        LIMIT 1
        """,
        ROYALTY_BLOCKING_STATUSES,
    ).fetchone()
    if blocked is not None:
        return f"OBLIGATION_{blocked[0]}"

    missing = missing_post_migration_royalty_obligations(cursor, limit=1)
    if missing:
        return "MISSING_ROYALTY_OBLIGATION"

    if not royalty_destination_is_valid():
        return "INVALID_DESTINATION"

    if ROYALTY_MODE == "PRODUCTION_ENFORCED":
        if not ROYALTY_PAYMENT_TRANSPORT_ENABLED:
            return "PAYMENT_TRANSPORT_DISABLED"
        overdue = cursor.execute(
            """
            SELECT status
            FROM royalty_obligations
            WHERE status IN ('ACCOUNTED', 'DUE', 'PAYING')
              AND settlement_due_at_unix <= ?
            ORDER BY order_id
            LIMIT 1
            """,
            (now_unix,),
        ).fetchone()
        if overdue is not None:
            return f"OVERDUE_{overdue[0]}"
    return None


def royalty_new_trade_block_reason(cursor=None, now_unix=None):
    if cursor is not None:
        return _royalty_new_trade_block_reason(cursor, now_unix)
    conn = db_connect()
    try:
        return _royalty_new_trade_block_reason(conn.cursor(), now_unix)
    finally:
        conn.close()


def royalty_gate_message():
    return (
        "Royalty safety lock is active. New order creation and acceptance "
        "are temporarily disabled; existing trades remain available."
    )


def missing_post_migration_royalty_obligations(cursor, limit=20):
    """Return successful post-epoch trades that are missing their ledger row."""
    epoch_row = cursor.execute(
        "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
        ("royalty_accounting_started_at_unix",),
    ).fetchone()
    if epoch_row is None:
        return [(None, None)]
    try:
        epoch = int(epoch_row[0])
    except (TypeError, ValueError):
        return [(None, None)]
    return cursor.execute(
        """
        SELECT o.id, o.order_code
        FROM orders o
        LEFT JOIN royalty_obligations r ON r.order_id = o.id
        WHERE o.status = 'COMPLETED'
          AND o.completed_at_unix IS NOT NULL
          AND o.completed_at_unix >= ?
          AND r.order_id IS NULL
        ORDER BY o.id
        LIMIT ?
        """,
        (epoch, int(limit)),
    ).fetchall()


def recover_royalty_state_on_startup(now_unix=None):
    """A process restart makes every in-flight payment outcome uncertain."""
    now_unix = int(time.time()) if now_unix is None else int(now_unix)
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        """
        UPDATE royalty_obligations
        SET status = 'UNKNOWN', updated_at_unix = ?,
            failure_code = 'PROCESS_RESTART',
            failure_detail = 'Payment process restarted before a durable outcome.'
        WHERE status = 'PAYING'
        """,
        (now_unix,),
    )
    changed = cursor.rowcount
    cursor.execute(
        """
        UPDATE royalty_payment_attempts
        SET status = 'UNKNOWN', resolved_at_unix = ?, error_class = 'PROCESS_RESTART'
        WHERE status = 'PAYING'
        """,
        (now_unix,),
    )
    conn.commit()
    conn.close()
    return changed


def claim_royalty_payment_for_test(order_id, payment_request, now_unix=None):
    """Claim one mocked attempt; unavailable outside ACCOUNTING_TEST mode."""
    if ROYALTY_MODE != "ACCOUNTING_TEST":
        raise RuntimeError("test payment claims require ACCOUNTING_TEST mode")
    if not payment_request:
        raise ValueError("payment_request is required")
    now_unix = int(time.time()) if now_unix is None else int(now_unix)
    fingerprint = hashlib.sha256(payment_request.encode("utf-8")).hexdigest()
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        """
        SELECT status, royalty_sats
        FROM royalty_obligations
        WHERE order_id = ?
        """,
        (order_id,),
    ).fetchone()
    if row is None or row[0] not in ("ACCOUNTED", "DUE") or row[1] <= 0:
        conn.rollback()
        conn.close()
        return None
    attempt_number = cursor.execute(
        "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM royalty_payment_attempts WHERE order_id = ?",
        (order_id,),
    ).fetchone()[0]
    cursor.execute(
        """
        UPDATE royalty_obligations
        SET status = 'PAYING', updated_at_unix = ?, failure_code = NULL,
            failure_detail = NULL
        WHERE order_id = ? AND status IN ('ACCOUNTED', 'DUE')
        """,
        (now_unix, order_id),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return None
    cursor.execute(
        """
        INSERT INTO royalty_payment_attempts
            (order_id, attempt_number, payment_request_fingerprint,
             status, started_at_unix)
        VALUES (?, ?, ?, 'PAYING', ?)
        """,
        (order_id, attempt_number, fingerprint, now_unix),
    )
    conn.commit()
    conn.close()
    return attempt_number


def resolve_royalty_payment_for_test(
    order_id, attempt_number, outcome, payment_id=None, payment_ref=None,
    error_class=None, now_unix=None
):
    """Resolve a mocked payment as PAID, FAILED, or UNKNOWN exactly once."""
    if ROYALTY_MODE != "ACCOUNTING_TEST":
        raise RuntimeError("test payment resolution requires ACCOUNTING_TEST mode")
    if outcome not in ("PAID", "FAILED", "UNKNOWN"):
        raise ValueError("invalid royalty payment outcome")
    now_unix = int(time.time()) if now_unix is None else int(now_unix)
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        """
        UPDATE royalty_payment_attempts
        SET status = ?, resolved_at_unix = ?, coinos_payment_id = ?,
            coinos_payment_ref = ?, error_class = ?
        WHERE order_id = ? AND attempt_number = ? AND status = 'PAYING'
        """,
        (
            outcome, now_unix, payment_id, payment_ref, error_class,
            order_id, attempt_number,
        ),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return False
    cursor.execute(
        """
        UPDATE royalty_obligations
        SET status = ?, updated_at_unix = ?,
            confirmed_at_unix = CASE WHEN ? = 'PAID' THEN ? ELSE confirmed_at_unix END,
            failure_code = CASE WHEN ? = 'PAID' THEN NULL ELSE ? END
        WHERE order_id = ? AND status = 'PAYING'
        """,
        (outcome, now_unix, outcome, now_unix, outcome, error_class, order_id),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return False
    conn.commit()
    conn.close()
    return True


def royalty_status_snapshot(now_unix=None):
    now_unix = int(time.time()) if now_unix is None else int(now_unix)
    conn = db_connect()
    cursor = conn.cursor()
    counts = dict(
        cursor.execute(
            "SELECT status, COUNT(*) FROM royalty_obligations GROUP BY status"
        ).fetchall()
    )
    unresolved = cursor.execute(
        """
        SELECT r.order_id, o.order_code, r.status, r.royalty_sats
        FROM royalty_obligations r
        JOIN orders o ON o.id = r.order_id
        WHERE r.status NOT IN ('PAID', 'ZERO')
        ORDER BY r.order_id
        LIMIT 20
        """
    ).fetchall()
    reason = _royalty_new_trade_block_reason(cursor, now_unix)
    missing_obligations = missing_post_migration_royalty_obligations(cursor)
    conn.close()
    return {
        "mode": ROYALTY_MODE,
        "transport_enabled": ROYALTY_PAYMENT_TRANSPORT_ENABLED,
        "destination_valid": royalty_destination_is_valid(),
        "production_compliant": (
            ROYALTY_MODE == "PRODUCTION_ENFORCED"
            and ROYALTY_PAYMENT_TRANSPORT_ENABLED
            and royalty_destination_is_valid()
            and reason is None
        ),
        "gate_reason": reason,
        "counts": counts,
        "unresolved": unresolved,
        "missing_obligations": missing_obligations,
    }
