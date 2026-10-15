"""SQLite connection, schema initialization, and integrity verification."""

import os
import sqlite3
import time
from datetime import datetime
from settings import (
    DB_PATH,
    SELLER_INVOICE_PAYMENT_SECONDS,
)



def db_connect(timeout=30):
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn



def verify_database_integrity():
    conn = db_connect()
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
    finally:
        conn.close()
    if result is None or result[0] != "ok":
        raise RuntimeError("SQLite quick_check failed")



def init_database():
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT,
        seller_id INTEGER,
        buyer_id INTEGER,
        amount_sats INTEGER,
        price_type TEXT,
        price_toman INTEGER,
        market_offset_percent REAL,
        market_reference_toman INTEGER,
        price_locked_at_unix INTEGER,
        description TEXT,
        fee_percent INTEGER,
        manager_invoice TEXT,
        coinos_invoice_id TEXT,
        coinos_payment_hash TEXT,
        coinos_invoice_attempt INTEGER,
        payout_status TEXT,
        payout_started_at_unix INTEGER,
        payout_coinos_id TEXT,
        payout_coinos_ref TEXT,
        payout_error TEXT,
        buyer_invoice TEXT,
        buyer_invoice_prompt_message_id INTEGER,
        order_side TEXT NOT NULL DEFAULT 'SELL',
        channel_message_id INTEGER,
        open_since_unix INTEGER,
        open_generation INTEGER NOT NULL DEFAULT 0,
        assignment_attempt INTEGER NOT NULL DEFAULT 0,
        seller_invoice_attempt INTEGER,
        seller_invoice_sent_at_unix INTEGER,
        seller_invoice_deadline_unix INTEGER,
        seller_payment_confirmed_at_unix INTEGER,
        expired_at_unix INTEGER,
        expiration_notification_sent_at_unix INTEGER,
        expiration_delete_attempted_at_unix INTEGER,
        repost_claimed_at_unix INTEGER,
        completed_at_unix INTEGER,
        refunded_at_unix INTEGER,
        status TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS manager_transactions (
        order_id INTEGER PRIMARY KEY,
        manager_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        manager_prompt_message_id INTEGER,
        assignment_attempt INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reputation_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        order_code TEXT NOT NULL,
        points INTEGER NOT NULL,
        reason TEXT NOT NULL,
        applied_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(order_code, user_id, reason)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bot_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT NOT NULL
    )
    """)

    cursor.execute(
        """
        INSERT OR IGNORE INTO bot_settings (setting_key, setting_value)
        VALUES (?, ?)
        """,
        ("maintenance_mode", "0")
    )

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS blacklist (
        telegram_id INTEGER PRIMARY KEY,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS royalty_obligations (
        order_id INTEGER PRIMARY KEY,
        original_amount_sats INTEGER NOT NULL CHECK (original_amount_sats >= 0),
        operator_commission_sats INTEGER NOT NULL CHECK (operator_commission_sats >= 0),
        royalty_sats INTEGER NOT NULL CHECK (royalty_sats >= 0),
        destination_kind TEXT,
        destination_snapshot TEXT,
        mode TEXT NOT NULL CHECK (mode IN ('ACCOUNTING_TEST', 'PRODUCTION_ENFORCED')),
        completion_source TEXT NOT NULL CHECK (completion_source IN ('NORMAL', 'RECONCILED', 'MANUAL')),
        status TEXT NOT NULL CHECK (status IN ('ACCOUNTED', 'CONFIG_ERROR', 'DUE', 'PAYING', 'PAID', 'FAILED', 'UNKNOWN', 'ZERO')),
        settlement_due_at_unix INTEGER NOT NULL,
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        confirmed_at_unix INTEGER,
        failure_code TEXT,
        failure_detail TEXT,
        FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE RESTRICT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS royalty_payment_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        attempt_number INTEGER NOT NULL,
        payment_request TEXT,
        payment_request_fingerprint TEXT,
        status TEXT NOT NULL CHECK (status IN ('PAYING', 'PAID', 'FAILED', 'UNKNOWN')),
        started_at_unix INTEGER NOT NULL,
        resolved_at_unix INTEGER,
        coinos_payment_id TEXT,
        coinos_payment_ref TEXT,
        error_class TEXT,
        UNIQUE (order_id, attempt_number),
        UNIQUE (payment_request),
        FOREIGN KEY (order_id) REFERENCES royalty_obligations(order_id) ON DELETE RESTRICT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS order_attempt_timeouts (
        order_id INTEGER NOT NULL,
        assignment_attempt INTEGER NOT NULL,
        reopened_generation INTEGER NOT NULL,
        seller_id INTEGER NOT NULL,
        creator_id INTEGER NOT NULL,
        reposted_at_unix INTEGER,
        seller_notified_at_unix INTEGER,
        creator_notified_at_unix INTEGER,
        created_at_unix INTEGER NOT NULL,
        PRIMARY KEY (order_id, assignment_attempt)
    )
    """)

    # Migrate databases created before the order details were added.
    existing_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(orders)")
    }

    missing_columns = {
        "buyer_id": "INTEGER",
        "price_type": "TEXT",
        "price_toman": "INTEGER",
        "market_offset_percent": "REAL",
        "market_reference_toman": "INTEGER",
        "price_locked_at_unix": "INTEGER",
        "description": "TEXT",
        "fee_percent": "INTEGER",
        "manager_invoice": "TEXT",
        "coinos_invoice_id": "TEXT",
        "coinos_payment_hash": "TEXT",
        "coinos_invoice_attempt": "INTEGER",
        "payout_status": "TEXT",
        "payout_started_at_unix": "INTEGER",
        "payout_coinos_id": "TEXT",
        "payout_coinos_ref": "TEXT",
        "payout_error": "TEXT",
        "buyer_invoice": "TEXT",
        "buyer_invoice_prompt_message_id": "INTEGER",
        "order_side": "TEXT NOT NULL DEFAULT 'SELL'",
        "channel_message_id": "INTEGER",
        "open_since_unix": "INTEGER",
        "open_generation": "INTEGER NOT NULL DEFAULT 0",
        "assignment_attempt": "INTEGER NOT NULL DEFAULT 0",
        "seller_invoice_attempt": "INTEGER",
        "seller_invoice_sent_at_unix": "INTEGER",
        "seller_invoice_deadline_unix": "INTEGER",
        "seller_payment_confirmed_at_unix": "INTEGER",
        "expired_at_unix": "INTEGER",
        "expiration_notification_sent_at_unix": "INTEGER",
        "expiration_delete_attempted_at_unix": "INTEGER",
        "repost_claimed_at_unix": "INTEGER",
        "completed_at_unix": "INTEGER",
        "refunded_at_unix": "INTEGER",
    }

    for column_name, column_type in missing_columns.items():
        if column_name not in existing_columns:
            cursor.execute(
                f"ALTER TABLE orders ADD COLUMN {column_name} {column_type}"
            )

    manager_transaction_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(manager_transactions)")
    }
    if "assignment_attempt" not in manager_transaction_columns:
        cursor.execute(
            "ALTER TABLE manager_transactions "
            "ADD COLUMN assignment_attempt INTEGER NOT NULL DEFAULT 0"
        )

    timeout_event_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(order_attempt_timeouts)")
    }
    if "reposted_at_unix" not in timeout_event_columns:
        cursor.execute(
            "ALTER TABLE order_attempt_timeouts ADD COLUMN reposted_at_unix INTEGER"
        )

    now_unix = int(time.time())
    cursor.execute(
        """
        INSERT OR IGNORE INTO bot_settings (setting_key, setting_value)
        VALUES (?, ?)
        """,
        ("royalty_accounting_started_at_unix", str(now_unix))
    )
    open_orders_without_timestamp = cursor.execute(
        """
        SELECT id, created_at
        FROM orders
        WHERE status = ? AND open_since_unix IS NULL
        """,
        ("OPEN",)
    ).fetchall()
    for order_id, created_at in open_orders_without_timestamp:
        try:
            open_since_unix = int(
                datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").timestamp()
            )
        except (TypeError, ValueError, OverflowError):
            open_since_unix = now_unix
        cursor.execute(
            "UPDATE orders SET open_since_unix = ? WHERE id = ?",
            (open_since_unix, order_id)
        )

    cursor.execute(
        """
        UPDATE manager_transactions
        SET assignment_attempt = COALESCE((
            SELECT assignment_attempt
            FROM orders
            WHERE orders.id = manager_transactions.order_id
        ), assignment_attempt)
        """
    )

    # Older databases did not record when an already-forwarded seller Invoice
    # was sent. Give only those legacy in-flight attempts a fresh persisted
    # window; all new forwards use the exact successful-send timestamp.
    cursor.execute(
        """
        UPDATE orders
        SET seller_invoice_attempt = assignment_attempt,
            seller_invoice_sent_at_unix = ?,
            seller_invoice_deadline_unix = ?
        WHERE status IN (?, ?)
          AND manager_invoice IS NOT NULL
          AND seller_invoice_deadline_unix IS NULL
          AND EXISTS (
              SELECT 1
              FROM manager_transactions mt
              WHERE mt.order_id = orders.id AND mt.status = ?
                AND mt.assignment_attempt = orders.assignment_attempt
          )
        """,
        (
            now_unix,
            now_unix + SELLER_INVOICE_PAYMENT_SECONDS,
            "BUYER_ASSIGNED",
            "SELLER_ASSIGNED",
            "FORWARDED"
        )
    )

    cursor.execute(
        """
        UPDATE orders
        SET manager_invoice = NULL
        WHERE status IN (?, ?)
          AND manager_invoice IS NOT NULL
          AND seller_invoice_sent_at_unix IS NULL
          AND EXISTS (
              SELECT 1
              FROM manager_transactions mt
              WHERE mt.order_id = orders.id AND mt.status = ?
                AND mt.assignment_attempt = orders.assignment_attempt
          )
        """,
        ("BUYER_ASSIGNED", "SELLER_ASSIGNED", "PENDING")
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_orders_open_expiration
        ON orders (status, open_since_unix)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_orders_seller_invoice_deadline
        ON orders (status, seller_invoice_deadline_unix)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_royalty_obligations_status_due
        ON royalty_obligations (status, settlement_due_at_unix, order_id)
        """
    )
    # Payment requests can contain sensitive Lightning metadata. Older staging
    # schemas had a raw column; retain it for compatibility but purge and never
    # populate it. The fingerprint is sufficient for duplicate detection.
    cursor.execute(
        "UPDATE royalty_payment_attempts SET payment_request = NULL "
        "WHERE payment_request IS NOT NULL"
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_royalty_payment_request_fingerprint
        ON royalty_payment_attempts (payment_request_fingerprint)
        WHERE payment_request_fingerprint IS NOT NULL
        """
    )

    conn.commit()
    conn.close()

