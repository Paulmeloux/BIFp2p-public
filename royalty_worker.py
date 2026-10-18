"""Durable per-trade royalty worker. UNKNOWN is reconciliation-only.

No SMTP, telemetry or direct notification to the original maintainer.
The operator receives a local admin notice and must report incidents.
"""
import asyncio
import hashlib
import time
import uuid

from database import db_connect
from observability import logger
import royalty
from royalty_wallet import BENEFICIARY, configured_wallet, resolve_invoice, verified_evidence
from settings import ADMIN_ID


def _claim(order_id, wallet_identity, now):
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT r.royalty_sats FROM royalty_obligations r JOIN orders o ON o.id=r.order_id "
            "WHERE r.order_id=? AND r.mode='PRODUCTION_ENFORCED' "
            "AND r.status IN ('DUE','FAILED') AND r.royalty_sats>0 "
            "AND o.status='COMPLETED' AND o.amount_sats=r.original_amount_sats "
            "AND r.royalty_sats=r.original_amount_sats/1000 "
            "AND r.destination_kind='LIGHTNING_ADDRESS' AND r.destination_snapshot=? "
            "AND NOT EXISTS (SELECT 1 FROM royalty_payment_attempts a "
            "WHERE a.order_id=r.order_id AND a.status IN ('PAYING','UNKNOWN','PAID'))",
            (order_id, BENEFICIARY),
        ).fetchone()
        if row is None:
            return None
        attempt = conn.execute(
            "SELECT COALESCE(MAX(attempt_number),0)+1 FROM royalty_payment_attempts WHERE order_id=?",
            (order_id,),
        ).fetchone()[0]
        conn.execute("UPDATE royalty_obligations SET status='PAYING',updated_at_unix=?, "
                     "failure_code=NULL WHERE order_id=?", (now, order_id))
        conn.execute(
            "INSERT INTO royalty_payment_attempts "
            "(order_id,attempt_number,payment_request_fingerprint,status,started_at_unix,"
            "wallet_identity,amount_sats,beneficiary) VALUES (?,?,?,'PAYING',?,?,?,?)",
            (order_id, attempt, hashlib.sha256(uuid.uuid4().bytes).hexdigest(), now,
             wallet_identity, row[0], BENEFICIARY),
        )
        conn.commit()
        return attempt, row[0]
    finally:
        conn.close()


def _prepare(order_id, attempt, invoice, now):
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        valid = conn.execute(
            "SELECT 1 FROM royalty_obligations r JOIN orders o ON o.id=r.order_id "
            "WHERE r.order_id=? AND r.status='PAYING' AND r.mode='PRODUCTION_ENFORCED' "
            "AND o.status='COMPLETED' AND o.amount_sats=r.original_amount_sats "
            "AND r.royalty_sats=? AND r.destination_snapshot=?",
            (order_id, invoice.amount_sats, BENEFICIARY),
        ).fetchone()
        if valid is None or invoice.expires_at <= now + 5:
            raise ValueError("Trade or invoice changed before submission")
        changed = conn.execute(
            "UPDATE royalty_payment_attempts SET invoice_payment_hash=?,payment_request_fingerprint=? "
            "WHERE order_id=? AND attempt_number=? AND status='PAYING' "
            "AND invoice_payment_hash IS NULL",
            (invoice.payment_hash, hashlib.sha256(invoice.bolt11.encode()).hexdigest(), order_id, attempt),
        ).rowcount
        if changed != 1:
            raise ValueError("Payment claim no longer valid")
        conn.commit()  # MUST precede the only wallet POST.
    finally:
        conn.close()


def _resolve(order_id, attempt, status, now, error=None, evidence=None):
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT invoice_payment_hash,amount_sats,status FROM royalty_payment_attempts "
            "WHERE order_id=? AND attempt_number=?", (order_id, attempt),
        ).fetchone()
        if row is None or row[2] not in ('PAYING', 'UNKNOWN'):
            return False
        if status == "PAID" and not verified_evidence(evidence, row[0], row[1]):
            raise ValueError("Settlement requires exact cryptographic payment evidence")
        # Only failures BEFORE hash persistence are safe to automatically retry.
        if status == "FAILED" and row[0] is not None:
            raise ValueError("Submitted payment is reconciliation-only")
        conn.execute(
            "UPDATE royalty_payment_attempts SET status=?,resolved_at_unix=?,error_class=?,"
            "coinos_payment_id=? WHERE order_id=? AND attempt_number=?",
            (status, now, error, row[0] if status == 'PAID' else None, order_id, attempt),
        )
        changed = conn.execute(
            "UPDATE royalty_obligations SET status=?,updated_at_unix=?,failure_code=?,"
            "confirmed_at_unix=CASE WHEN ?='PAID' THEN ? ELSE confirmed_at_unix END "
            "WHERE order_id=? AND status IN ('PAYING','UNKNOWN')",
            (status, now, error, status, now, order_id),
        ).rowcount
        if changed != 1:
            return False
        conn.commit()
        return True
    finally:
        conn.close()


def _pending(wallet_identity):
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT a.order_id,a.attempt_number,a.invoice_payment_hash,a.amount_sats,a.wallet_identity "
            "FROM royalty_payment_attempts a JOIN royalty_obligations r ON r.order_id=a.order_id "
            "WHERE a.status='UNKNOWN' AND r.status='UNKNOWN' AND r.mode='PRODUCTION_ENFORCED' "
            "AND a.beneficiary=? AND a.wallet_identity=? "
            "ORDER BY COALESCE(a.last_checked_at_unix,0),a.started_at_unix LIMIT 100",
            (BENEFICIARY, wallet_identity),
        ).fetchall()
    finally:
        conn.close()


async def _lookup(wallet, order_id, attempt, payment_hash, amount, now):
    try:
        evidence = await asyncio.to_thread(wallet.lookup_payment, payment_hash)
        if verified_evidence(evidence, payment_hash, amount):
            return _resolve(order_id, attempt, "PAID", now, evidence=evidence)
    except asyncio.CancelledError:
        raise
    except Exception:
        # No exception text: service responses may contain credentials/invoices.
        logger.warning("Royalty lookup unresolved order_id=%s", order_id)
    finally:
        conn = db_connect()
        try:
            conn.execute("UPDATE royalty_payment_attempts SET last_checked_at_unix=? "
                         "WHERE order_id=? AND attempt_number=?", (now, order_id, attempt))
            conn.commit()
        finally:
            conn.close()
    return False


async def process_royalties_once(wallet=None, now_unix=None):
    if royalty.ROYALTY_MODE != "PRODUCTION_ENFORCED" or not royalty.ROYALTY_PAYMENT_TRANSPORT_ENABLED:
        return
    now = int(time.time()) if now_unix is None else int(now_unix)
    wallet = configured_wallet() if wallet is None else wallet
    for order_id, attempt, payment_hash, amount, identity in _pending(wallet.identity):
        if identity != wallet.identity:
            continue  # Configuration changes must never redirect reconciliation.
        if payment_hash is None:
            # This new-style claim demonstrably crashed before the submission boundary.
            _resolve(order_id, attempt, "FAILED", now, "PREPARATION_INTERRUPTED")
        else:
            await _lookup(wallet, order_id, attempt, payment_hash, amount, now)
    conn = db_connect()
    try:
        # A completed trade that is subsequently fully refunded/cancelled no
        # longer owes an unpaid royalty. Never erase an uncertain submission.
        conn.execute(
            "UPDATE royalty_obligations SET status='ZERO',failure_code='TRADE_REVERSED',updated_at_unix=? "
            "WHERE mode='PRODUCTION_ENFORCED' AND status IN ('DUE','FAILED','CONFIG_ERROR') "
            "AND EXISTS (SELECT 1 FROM orders o WHERE o.id=order_id "
            "AND o.status IN ('REFUNDED','CANCELED','FAILED')) "
            "AND NOT EXISTS (SELECT 1 FROM royalty_payment_attempts a WHERE a.order_id=royalty_obligations.order_id "
            "AND a.status IN ('PAYING','UNKNOWN','PAID'))", (now,),
        )
        # Exclude invalid ledger snapshots from the payment queue; retain them
        # for operator review and deadline enforcement rather than starving it.
        conn.execute(
            "UPDATE royalty_obligations SET status='CONFIG_ERROR',failure_code='INVALID_TRADE_SNAPSHOT',updated_at_unix=? "
            "WHERE mode='PRODUCTION_ENFORCED' AND status IN ('DUE','FAILED') AND NOT EXISTS "
            "(SELECT 1 FROM orders o WHERE o.id=order_id AND o.status='COMPLETED' "
            "AND o.amount_sats=original_amount_sats AND royalty_sats=original_amount_sats/1000 "
            "AND destination_kind='LIGHTNING_ADDRESS' AND destination_snapshot=?)",
            (now, BENEFICIARY),
        )
        conn.commit()
        candidates = conn.execute(
            "SELECT order_id FROM royalty_obligations WHERE mode='PRODUCTION_ENFORCED' "
            "AND (status='DUE' OR (status='FAILED' AND updated_at_unix<=?)) "
            "ORDER BY created_at_unix,order_id LIMIT 20", (now - 60,),
        ).fetchall()
    finally:
        conn.close()
    for (order_id,) in candidates:
        claim = _claim(order_id, wallet.identity, now)
        if claim is None:
            continue
        attempt, amount = claim
        try:
            invoice = await asyncio.to_thread(resolve_invoice, amount)
            _prepare(order_id, attempt, invoice, now)
        except asyncio.CancelledError:
            raise  # startup recovery handles cancellation/crash
        except Exception:
            _resolve(order_id, attempt, "FAILED", now, "INVOICE_PREPARATION_FAILED")
            continue
        try:
            await asyncio.to_thread(wallet.pay_invoice, invoice)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Any error after the boundary may conceal successful payment.
        _resolve(order_id, attempt, "UNKNOWN", now, "AWAITING_PAYMENT_PROOF")
        await _lookup(wallet, order_id, attempt, invoice.payment_hash, amount, now)


async def notify_royalty_incidents(context, now_unix=None):
    """Notify the operator only, once per incident state; never email Paul."""
    now = int(time.time()) if now_unix is None else int(now_unix)
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT r.order_id,r.status,r.royalty_sats,r.created_at_unix FROM royalty_obligations r "
            "WHERE r.mode='PRODUCTION_ENFORCED' AND r.royalty_sats>0 AND r.status NOT IN ('PAID','ZERO') "
            "AND (r.status IN ('FAILED','UNKNOWN','CONFIG_ERROR') OR r.created_at_unix+259200<=?) "
            "AND NOT EXISTS (SELECT 1 FROM royalty_incident_notices n WHERE n.order_id=r.order_id "
            "AND n.incident_key=CASE WHEN r.created_at_unix+259200<=? THEN 'OVERDUE' ELSE r.status END) "
            "ORDER BY r.created_at_unix LIMIT 100", (now, now),
        ).fetchall()
    finally:
        conn.close()
    for order_id, status, amount, created in rows:
        key = "OVERDUE" if created + 259200 <= now else status
        conn = db_connect()
        try:
            sent = conn.execute("SELECT 1 FROM royalty_incident_notices WHERE order_id=? AND incident_key=?",
                                (order_id, key)).fetchone()
        finally:
            conn.close()
        if sent:
            continue
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=(
                f"Royalty payment incident: trade {order_id}, {amount} sats, {status}.\n"
                "Report payment faults/delays to paulmeloux@proton.me without sensitive user data.\n"
                "Resolve within 72 hours of trade completion. Overdue debt pauses new orders only.\n"
                "UNKNOWN may already be paid: reconcile; do not blindly resend."
            ))
        except Exception:
            logger.warning("Royalty incident admin notice failed order_id=%s", order_id)
            continue
        conn = db_connect()
        try:
            conn.execute("INSERT OR IGNORE INTO royalty_incident_notices VALUES (?,?,?)", (order_id, key, now))
            conn.commit()
        finally:
            conn.close()


async def royalty_worker(app):
    failure_notified = False
    while True:
        try:
            await process_royalties_once()
            failure_notified = False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Royalty worker could not complete this cycle; existing trades unaffected")
            if not failure_notified:
                try:
                    await app.bot.send_message(chat_id=ADMIN_ID, text=(
                        "Royalty worker configuration/service failure. Existing trade recovery remains active. "
                        "Check the local wallet configuration and /royalty_status. "
                        "Report payment faults to paulmeloux@proton.me without credentials or user data."
                    ))
                    failure_notified = True
                except Exception:
                    logger.warning("Royalty worker failure notice could not be delivered")
        try:
            await notify_royalty_incidents(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Royalty incident scan could not complete")
        await asyncio.sleep(30)


async def stop_royalty_worker(app):
    task = app.bot_data.get("royalty_worker")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
