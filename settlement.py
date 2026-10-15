"""Coinos escrow, payout reconciliation, and settlement notifications."""

import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from coinos_api import CoinosHTTPError, coinos_request
from database import db_connect
from escrow_invoices import get_or_create_coinos_invoice, reserve_coinos_invoice_id
from observability import audit_event, logger
from pricing import fiat_total_toman
from settings import ADMIN_ID, COINOS_TOKEN, SELLER_INVOICE_PAYMENT_SECONDS
from users import format_seller_fee_percent, seller_fee_amount_sats


async def send_coinos_escrow_invoice(context, order_id, order_side, seller_id, assignment_attempt):
    reserved = reserve_coinos_invoice_id(
        order_id, order_side, seller_id, assignment_attempt
    )
    if reserved is None:
        return False

    invoice_id, amount_sats, stored_invoice, sent_at, fee_percent = reserved
    if sent_at is not None:
        return True

    fee_amount = seller_fee_amount_sats(amount_sats, fee_percent)
    expected_amount = amount_sats + fee_amount
    fee_percent_text = format_seller_fee_percent(fee_percent)

    conn = db_connect()
    row = conn.execute(
        "SELECT order_code FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return False
    order_code = row[0]

    if stored_invoice is None:
        invoice_data, bolt11 = await get_or_create_coinos_invoice(
            invoice_id, expected_amount, order_code
        )
        payment_hash = invoice_data.get("paymentHash")

        assigned_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
        conn = db_connect(timeout=30)
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            UPDATE orders
            SET manager_invoice = ?, coinos_payment_hash = ?
            WHERE id = ? AND order_side = ? AND status = ?
              AND seller_id = ? AND assignment_attempt = ?
              AND coinos_invoice_id = ? AND coinos_invoice_attempt = ?
              AND manager_invoice IS NULL
            """,
            (
                bolt11,
                payment_hash,
                order_id,
                order_side,
                assigned_status,
                seller_id,
                assignment_attempt,
                invoice_id,
                assignment_attempt,
            ),
        )
        if cursor.rowcount != 1:
            current = cursor.execute(
                """
                SELECT manager_invoice
                FROM orders
                WHERE id = ? AND assignment_attempt = ? AND coinos_invoice_id = ?
                """,
                (order_id, assignment_attempt, invoice_id),
            ).fetchone()
            conn.commit()
            conn.close()
            if current is None or current[0] is None:
                return False
            bolt11 = current[0]
        else:
            conn.commit()
            conn.close()
    else:
        bolt11 = stored_invoice

    # Claim this exact delivery attempt in SQLite before exposing the payable
    # invoice to Telegram. This closes the send-before-state-write TOCTOU gap.
    deadline = mark_seller_invoice_sent(
        order_id, order_side, seller_id, assignment_attempt, bolt11
    )
    if deadline is None:
        return False

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "⚡ Invoice پرداخت بیتکوین\n\n"
                f"مبلغ سفارش: {amount_sats:,} ساتوشی\n"
                f"کارمزد {fee_percent_text}: {fee_amount:,} ساتوشی\n"
                f"مبلغ قابل پرداخت: {expected_amount:,} ساتوشی\n\n"
                "لطفاً Invoice زیر را حداکثر تا ۲۰ دقیقه پرداخت کنید."
            ),,
        )
        await context.bot.send_message(chat_id=seller_id, text=bolt11)
    except TelegramError:
        reset_seller_invoice_delivery_claim(
            order_id, order_side, seller_id, assignment_attempt, bolt11
        )
        logger.exception(
            "Failed to send Coinos escrow invoice order_id=%s attempt=%s",
            order_id,
            assignment_attempt,
        )
        return False

    logger.info(
        "Coinos escrow invoice delivered order_id=%s attempt=%s seller_id=%s deadline=%s",
        order_id, assignment_attempt, seller_id, deadline
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"Coinos escrow invoice sent for order {order_code}.\n"
                f"Order: {amount_sats:,} sats | escrow: {expected_amount:,} sats\n"
                "Payment detection is automatic; no manual confirmation is required."
            ),
        )
    except TelegramError:
        pass
    return True



async def notify_coinos_seller_payment_confirmed(
    context, order_id, order_side, seller_id, buyer_id, usernames
):
    conn = db_connect()
    price_row = conn.execute(
        "SELECT amount_sats, price_toman FROM orders WHERE id = ?",
        (order_id,)
    ).fetchone()
    conn.close()

    if price_row is None or price_row[1] is None:
        logger.error("Locked fiat price unavailable for order_id=%s", order_id)
        return

    amount_sats, locked_price_toman = price_row
    fiat_amount_toman = fiat_total_toman(amount_sats, locked_price_toman)
    payment_details = (
        "💳 پرداخت فیات\n\n"
        f"مقدار بیتکوین: {amount_sats:,} ساتوشی\n"
        f"قیمت نهایی بیتکوین: {locked_price_toman:,} تومان\n"
        f"مبلغ قابل پرداخت: {fiat_amount_toman:,} تومان"
    )

    seller_username = usernames.get(seller_id)
    buyer_username = usernames.get(buyer_id)
    seller_contact = f"@{seller_username}" if seller_username else str(seller_id)
    buyer_contact = f"@{buyer_username}" if buyer_username else str(buyer_id)
    privacy = (
        "🔒 لطفاً فرآیند مبادله را با سرعت و دقت تا پایان پیش ببرید و پس از تکمیل "
        "موفقیت آمیز معامله، نسبت به حفظ حریم خصوصی خود و دیگران کوشا باشید."
    )

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "بیتکوین شما توسط بات دریافت و تأیید شد.\n\n"
                f"{payment_details}\n\n"
                "اکنون می‌توانید مرحله پرداخت فیات را با خریدار ادامه دهید.\n\n"
                f"آیدی خریدار: {buyer_contact}\n\n{privacy}"
            ),
        )
    except TelegramError:
        pass

    buyer_markup = None
    if order_side == "BUY":
        buyer_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "💸 فیات ارسال شد",
                callback_data=f"buy_buyer_fiat_sent:{order_id}",
            )
        ]])
    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                "بیتکوین فروشنده دریافت و آماده انتقال نهایی است.\n\n"
                f"{payment_details}\n\n"
                f"آیدی فروشنده: {seller_contact}\n\n"
                "لطفاً مبلغ بالا را برای فروشنده ارسال کنید.\n\n"
                f"{privacy}"
            ),
            reply_markup=buyer_markup,
        )
    except TelegramError:
        pass

    if order_side == "SELL":
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"{payment_details}\n\n"
                    "لطفاً مبلغ بالا را برای فروشنده ارسال کنید.\n\n"
                    "پس از ارسال فیات، وضعیت را تأیید کنید."
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "💸 فیات ارسال شد",
                        callback_data=f"buyer_fiat_sent:{order_id}",
                    )
                ]]),
            )
        except TelegramError:
            pass
        try:
            await context.bot.send_message(
                chat_id=seller_id,
                text="پس از دریافت فیات، وضعیت را تأیید کنید.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "فیات دریافت شد",
                        callback_data=f"seller_fiat_received:{order_id}",
                    )
                ]]),
            )
        except TelegramError:
            pass



async def confirm_coinos_seller_payment(
    context, order_id, assignment_attempt, settled_unix
):
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    order = cursor.execute(
        """
        SELECT order_side, seller_id, buyer_id, status, assignment_attempt,
               seller_invoice_attempt, seller_invoice_deadline_unix,
               manager_invoice, coinos_invoice_attempt
        FROM orders
        WHERE id = ?
        """,
        (order_id,),
    ).fetchone()
    if order is None:
        conn.close()
        return False

    (
        order_side,
        seller_id,
        buyer_id,
        status,
        current_attempt,
        seller_invoice_attempt,
        deadline_unix,
        manager_invoice,
        coinos_invoice_attempt,
    ) = order
    expected_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
    if (
        status != expected_status
        or current_attempt != assignment_attempt
        or seller_invoice_attempt != assignment_attempt
        or coinos_invoice_attempt != assignment_attempt
        or deadline_unix is None
        or settled_unix > deadline_unix
        or manager_invoice is None
    ):
        conn.close()
        return False

    cursor.execute(
        """
        UPDATE orders
        SET status = ?, seller_payment_confirmed_at_unix = ?,
            seller_invoice_deadline_unix = NULL
        WHERE id = ? AND status = ? AND assignment_attempt = ?
          AND seller_invoice_attempt = ? AND coinos_invoice_attempt = ?
          AND seller_invoice_deadline_unix IS NOT NULL
          AND seller_invoice_deadline_unix >= ?
          AND seller_payment_confirmed_at_unix IS NULL
          AND manager_invoice IS NOT NULL
        """,
        (
            "SELLER_SATS_CONFIRMED",
            settled_unix,
            order_id,
            expected_status,
            assignment_attempt,
            assignment_attempt,
            assignment_attempt,
            settled_unix,
        ),
    )
    if cursor.rowcount != 1:
        conn.close()
        return False

    usernames = {
        telegram_id: username
        for telegram_id, username in cursor.execute(
            "SELECT telegram_id, username FROM users WHERE telegram_id IN (?, ?)",
            (seller_id, buyer_id),
        ).fetchall()
        if username
    }
    conn.commit()
    conn.close()

    await notify_coinos_seller_payment_confirmed(
        context, order_id, order_side, seller_id, buyer_id, usernames
    )
    audit_event("escrow_payment_confirmed", order_id=order_id, side=order_side, seller_id=seller_id, buyer_id=buyer_id, attempt=assignment_attempt)
    return True



async def process_coinos_escrow_invoices(context):
    if not COINOS_TOKEN:
        return

    conn = db_connect()
    pending_send = conn.execute(
        """
        SELECT id, order_side, seller_id, assignment_attempt
        FROM orders
        WHERE (
                (order_side = 'SELL' AND status = 'BUYER_ASSIGNED')
                OR (order_side = 'BUY' AND status = 'SELLER_ASSIGNED')
              )
          AND coinos_invoice_id IS NOT NULL
          AND coinos_invoice_attempt = assignment_attempt
          AND seller_payment_confirmed_at_unix IS NULL
          AND seller_invoice_sent_at_unix IS NULL
        ORDER BY id
        """
    ).fetchall()
    conn.close()

    for order_id, order_side, seller_id, assignment_attempt in pending_send:
        try:
            await send_coinos_escrow_invoice(
                context, order_id, order_side, seller_id, assignment_attempt
            )
        except Exception:
            logger.exception(
                "Coinos escrow invoice delivery failed order_id=%s attempt=%s",
                order_id,
                assignment_attempt,
            )

    conn = db_connect()
    watching = conn.execute(
        """
        SELECT id, assignment_attempt, amount_sats, coinos_invoice_id,
               seller_invoice_deadline_unix, fee_percent
        FROM orders
        WHERE (
                (order_side = 'SELL' AND status = 'BUYER_ASSIGNED')
                OR (order_side = 'BUY' AND status = 'SELLER_ASSIGNED')
              )
          AND coinos_invoice_id IS NOT NULL
          AND coinos_invoice_attempt = assignment_attempt
          AND manager_invoice IS NOT NULL
          AND seller_invoice_attempt = assignment_attempt
          AND seller_invoice_deadline_unix IS NOT NULL
          AND seller_payment_confirmed_at_unix IS NULL
        ORDER BY id
        """
    ).fetchall()
    conn.close()

    for order_id, assignment_attempt, amount_sats, invoice_id, deadline_unix, fee_percent in watching:
        try:
            invoice = await coinos_request("GET", f"/invoice/{invoice_id}")
            expected_amount = amount_sats + seller_fee_amount_sats(amount_sats, fee_percent)
            received = int(invoice.get("received") or 0)
            settled = invoice.get("settled")
            if received < expected_amount or settled is None:
                continue
            settled_unix = int(settled) // 1000
            if settled_unix > deadline_unix:
                continue
            await confirm_coinos_seller_payment(
                context, order_id, assignment_attempt, settled_unix
            )
        except Exception:
            logger.exception(
                "Coinos escrow status check failed order_id=%s attempt=%s",
                order_id,
                assignment_attempt,
            )



def claim_buyer_payout(order_id, buyer_id, invoice):
    """Claim a payout exactly once locally. A claimed payout is never auto-retried."""
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        """
        SELECT order_code, seller_id, buyer_id, amount_sats, order_side,
               status, buyer_invoice, payout_status
        FROM orders
        WHERE id = ?
        """,
        (order_id,),
    ).fetchone()
    if (
        row is None
        or row[2] != buyer_id
        or row[5] != "BUYER_INVOICE_RECEIVED"
        or row[6] != invoice
        or row[7] is not None
    ):
        conn.close()
        return None

    started_at = int(time.time())
    cursor.execute(
        """
        UPDATE orders
        SET payout_status = ?, payout_started_at_unix = ?, payout_error = NULL
        WHERE id = ? AND buyer_id = ? AND status = ? AND buyer_invoice = ?
          AND payout_status IS NULL
        """,
        (
            "PROCESSING",
            started_at,
            order_id,
            buyer_id,
            "BUYER_INVOICE_RECEIVED",
            invoice,
        ),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return None
    conn.commit()
    conn.close()
    return row[:5]



def mark_buyer_payout_unknown(order_id, error_text):
    conn = db_connect(timeout=30)
    conn.execute(
        """
        UPDATE orders
        SET payout_status = ?, payout_error = ?
        WHERE id = ? AND status = ? AND payout_status = ?
        """,
        (
            "UNKNOWN",
            str(error_text)[:500],
            order_id,
            "BUYER_INVOICE_RECEIVED",
            "PROCESSING",
        ),
    )
    conn.commit()
    conn.close()



def _is_definitive_coinos_routing_failure(error_text):
    '''Recognize only explicit terminal Coinos Lightning routing failures.'''
    if isinstance(error_text, CoinosHTTPError):
        text = f"{error_text} {error_text.response_body}".lower()
    else:
        text = str(error_text).lower()
    ambiguous_markers = (
        'timeout',
        'timed out',
        'network',
        'connection',
        'socket',
        'dns',
        'urlerror',
        'urlopen',
        'broken pipe',
        'remote end closed',
        'bad gateway',
        'service unavailable',
        'gateway timeout',
        'malformed',
        'unexpected',
        'unconfirmed',
        'mismatched payout',
    )
    if (
        any(marker in text for marker in ambiguous_markers)
        or re.search(r'\b(?:502|503|504)\b', text)
    ):
        return False

    attempts_exhausted = re.search(r'\bfailed after\s+\d+\s+attempts?\b', text)
    terminal_routing_failure = (
        'we could not find a usable set of paths' in text
        or 'no route to destination' in text
        or 'unable to find a route to destination' in text
        or 'channels to the destination are disabled' in text
    )
    return attempts_exhausted is not None and terminal_routing_failure



def reset_definitive_buyer_routing_failure(order_id, buyer_id, invoice, error_text):
    '''Atomically make only the failed invoice replaceable by the buyer.'''
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute('BEGIN IMMEDIATE')
    cursor.execute(
        '''
        UPDATE orders
        SET status = ?, buyer_invoice = NULL,
            buyer_invoice_prompt_message_id = NULL,
            payout_status = NULL, payout_started_at_unix = NULL,
            payout_coinos_id = NULL, payout_coinos_ref = NULL,
            payout_error = ?
        WHERE id = ? AND buyer_id = ? AND status = ?
          AND buyer_invoice = ? AND payout_status = ?
        ''',
        (
            'FIAT_RECEIVED',
            str(error_text)[:500],
            order_id,
            buyer_id,
            'BUYER_INVOICE_RECEIVED',
            invoice,
            'PROCESSING',
        ),
    )
    updated = cursor.rowcount == 1
    if updated:
        conn.commit()
    else:
        conn.rollback()
    conn.close()
    return updated



def store_buyer_invoice_prompt(order_id, buyer_id, message_id):
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute(
        '''
        UPDATE orders
        SET buyer_invoice_prompt_message_id = ?
        WHERE id = ? AND buyer_id = ? AND status = ?
          AND buyer_invoice IS NULL
        ''',
        (message_id, order_id, buyer_id, 'FIAT_RECEIVED'),
    )
    conn.commit()
    updated = cursor.rowcount == 1
    conn.close()
    return updated



def _normalized_lightning_invoice(invoice):
    invoice = (invoice or "").strip().lower()
    if invoice.startswith("lightning:"):
        invoice = invoice[len("lightning:"):]
    return invoice



def _find_confirmed_coinos_payout(payments_response, invoice, amount_sats, started_at_unix):
    """Find the exact already-paid Lightning invoice in Coinos payment history."""
    expected_invoice = _normalized_lightning_invoice(invoice)
    earliest_created_ms = max(0, int(started_at_unix or 0) * 1000 - 60_000)
    matches = []
    for payment in (payments_response or {}).get("payments", []):
        try:
            api_amount = int(payment.get("amount") or 0)
            created_ms = int(payment.get("created") or 0)
        except (TypeError, ValueError):
            continue
        if (
            payment.get("confirmed") is True
            and payment.get("type") in ("lightning", "internal")
            and api_amount == -int(amount_sats)
            and _normalized_lightning_invoice(payment.get("hash")) == expected_invoice
            and created_ms >= earliest_created_ms
        ):
            matches.append(payment)
    return matches[0] if len(matches) == 1 else None



def complete_reconciled_buyer_payout(order_id, buyer_id, invoice, payment):
    payment_id = payment.get("id")
    payment_ref = payment.get("ref")
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        """
        SELECT order_code, seller_id, buyer_id, amount_sats, order_side
        FROM orders
        WHERE id = ? AND buyer_id = ?
          AND status IN (?, ?) AND buyer_invoice = ?
          AND payout_status = ?
        """,
        (order_id, buyer_id, "BUYER_INVOICE_RECEIVED", "CANCELED_REFUND_PENDING", invoice, "UNKNOWN"),
    ).fetchone()
    if row is None:
        conn.rollback()
        conn.close()
        return None
    cursor.execute(
        """
        UPDATE orders
        SET payout_status = ?, payout_coinos_id = ?, payout_coinos_ref = ?,
            payout_error = NULL, status = ?
        WHERE id = ? AND buyer_id = ?
          AND status IN (?, ?) AND buyer_invoice = ?
          AND payout_status = ?
        """,
        (
            "CONFIRMED", payment_id, payment_ref, "COMPLETED", order_id, buyer_id,
            "BUYER_INVOICE_RECEIVED", "CANCELED_REFUND_PENDING", invoice, "UNKNOWN",
        ),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return None
    conn.commit()
    conn.close()
    return row



async def reconcile_unknown_buyer_payouts(context):
    """Read-only Coinos reconciliation. Never sends or retries a payment."""
    if not COINOS_TOKEN:
        return
    conn = db_connect()
    pending = conn.execute(
        """
        SELECT id, buyer_id, buyer_invoice, amount_sats, payout_started_at_unix
        FROM orders
        WHERE status IN (?, ?) AND payout_status = ? AND buyer_invoice IS NOT NULL
        ORDER BY id
        """,
        ("BUYER_INVOICE_RECEIVED", "CANCELED_REFUND_PENDING", "UNKNOWN"),
    ).fetchall()
    conn.close()
    if not pending:
        return
    try:
        payments_response = await coinos_request("GET", "/payments")
    except Exception:
        logger.exception("Coinos payout reconciliation history check failed")
        return

    for order_id, buyer_id, invoice, amount_sats, started_at_unix in pending:
        payment = _find_confirmed_coinos_payout(
            payments_response, invoice, amount_sats, started_at_unix
        )
        if payment is None:
            continue
        completed = complete_reconciled_buyer_payout(
            order_id, buyer_id, invoice, payment
        )
        if completed is None:
            continue
        order_code, seller_id, buyer_id, amount_sats, order_side = completed
        logger.info(
            "Coinos buyer payout reconciled order_id=%s payment_id=%s amount=%s",
            order_id, payment.get("id"), amount_sats,
        )
        audit_event(
            "buyer_payout_reconciled", order_id=order_id, order_code=order_code,
            side=order_side, seller_id=seller_id, buyer_id=buyer_id, amount_sats=amount_sats
        )
        await notify_completed_trade(
            context, order_code, order_id, seller_id, buyer_id
        )



async def notify_completed_trade(context, order_code, order_id, seller_id, buyer_id):
    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text="بیتکوین برای شما ارسال شد.\nمعامله با موفقیت تکمیل شد."
        )
    except TelegramError:
        pass
    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text="معامله با موفقیت تکمیل شد."
        )
    except TelegramError:
        pass
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"معامله {order_code} با شناسه {order_id} تکمیل شد. پرداخت Coinos تأیید شد."
        )
    except TelegramError:
        pass



async def auto_pay_buyer_invoice(context, order_id, buyer_id, invoice):
    """Pay a buyer invoice once. Ambiguous API outcomes are NEVER retried automatically."""
    claimed = claim_buyer_payout(order_id, buyer_id, invoice)
    if claimed is None:
        return False

    order_code, seller_id, _, amount_sats, order_side = claimed
    try:
        payment = await coinos_request("POST", "/payments", {"payreq": invoice})
        confirmed = payment.get("confirmed") is True
        api_amount = int(payment.get("amount") or 0)
        payment_type = payment.get("type")
        if (
            not confirmed
            or api_amount != -amount_sats
            or payment_type not in ("lightning", "internal")
            or _normalized_lightning_invoice(payment.get("hash"))
            != _normalized_lightning_invoice(invoice)
        ):
            raise RuntimeError("Coinos returned an unconfirmed or mismatched payout")
    except Exception as exc:
        if _is_definitive_coinos_routing_failure(exc):
            reset = reset_definitive_buyer_routing_failure(
                order_id, buyer_id, invoice, exc
            )
            if not reset:
                logger.warning(
                    'Coinos definitive routing failure reset skipped because state changed '
                    'order_id=%s order_code=%s',
                    order_id,
                    order_code,
                )
                return False

            logger.warning(
                'Coinos definitive routing failure reset for new buyer invoice '
                'order_id=%s order_code=%s',
                order_id,
                order_code,
            )
            audit_event(
                'buyer_payout_routing_failure_reset',
                order_id=order_id,
                order_code=order_code,
                side=order_side,
                seller_id=seller_id,
                buyer_id=buyer_id,
                amount_sats=amount_sats,
            )
            buyer_prompt_sent = False
            try:
                buyer_invoice_prompt = await context.bot.send_message(
                    chat_id=buyer_id,
                    text=(
                        'پرداخت لایتنینگ به Invoice قبلی به دلیل خطای مسیریابی '
                        'ناموفق بود و انجام نشد.\n\n'
                        f'لطفاً یک Invoice جدید دقیقاً به مبلغ {amount_sats:,} ساتوشی '
                        'ایجاد و برای بات ارسال کنید.'
                    ),,
                )
                buyer_prompt_sent = True
                prompt_stored = store_buyer_invoice_prompt(
                    order_id, buyer_id, buyer_invoice_prompt.message_id
                )
                if not prompt_stored:
                    logger.warning(
                        'Replacement buyer invoice prompt message_id was not stored '
                        'order_id=%s order_code=%s',
                        order_id,
                        order_code,
                    )
            except TelegramError:
                logger.exception(
                    'Failed to send replacement buyer invoice prompt order_id=%s',
                    order_id,
                )
            if buyer_prompt_sent:
                admin_delivery_text = (
                    'برای Invoice قبلی هیچ تلاش خودکار مجددی انجام نشد و از '
                    'خریدار Invoice جدید خواسته شد.'
                )
            else:
                admin_delivery_text = (
                    'برای Invoice قبلی هیچ تلاش خودکار مجددی انجام نشد. سفارش با '
                    'ایمنی به وضعیت انتظار برای Invoice جدید بازگردانده شد، اما بات '
                    'نتوانست پیام درخواست Invoice جدید را به خریدار برساند؛ ممکن است '
                    'تماس دستی لازم باشد.'
                )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        '⚠️ پرداخت خودکار به‌دلیل خطای قطعی مسیریابی لایتنینگ '
                        'ناموفق بود.\n'
                        f'{admin_delivery_text}\n\n'
                        f'کد سفارش: {order_code}\n'
                        f'شناسه سفارش: {order_id}\n'
                        f'مبلغ: {amount_sats:,} ساتوشی'
                    ),
                )
            except TelegramError:
                pass
            return False

        # Critical safety rule: a timeout/error may occur after Coinos actually paid.
        # Record UNKNOWN and require human reconciliation; never auto-retry this invoice.
        mark_buyer_payout_unknown(order_id, exc)
        logger.exception("Coinos buyer payout outcome unknown order_id=%s", order_id)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ وضعیت پرداخت Coinos نامشخص است. پرداخت خودکار دوباره تلاش نخواهد شد.\n\n"
                    f"کد سفارش: {order_code}\n"
                    f"شناسه سفارش: {order_id}\n"
                    f"مبلغ: {amount_sats:,} ساتوشی\n\n"
                    "ابتدا Coinos را بررسی کنید. فقط اگر پرداخت واقعاً انجام شده است، "
                    "دکمه تأیید دستی را بزنید."
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "تأیید دستی پرداخت پس از بررسی",
                        callback_data=(
                            f"buy_buyer_sats_sent:{order_id}"
                            if order_side == "BUY"
                            else f"buyer_sats_sent:{order_id}"
                        ),
                    )
                ]]),
            )
        except TelegramError:
            pass
        return False

    payment_id = payment.get("id")
    payment_ref = payment.get("ref")
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        """
        UPDATE orders
        SET payout_status = ?, payout_coinos_id = ?, payout_coinos_ref = ?,
            payout_error = NULL, status = ?
        WHERE id = ? AND buyer_id = ? AND status = ? AND buyer_invoice = ?
          AND payout_status = ?
        """,
        (
            "CONFIRMED",
            payment_id,
            payment_ref,
            "COMPLETED",
            order_id,
            buyer_id,
            "BUYER_INVOICE_RECEIVED",
            invoice,
            "PROCESSING",
        ),
    )
    updated = cursor.rowcount == 1
    if updated:
        conn.commit()
    else:
        conn.rollback()
    conn.close()
    if not updated:
        logger.critical(
            "Coinos payout confirmed remotely but local completion failed order_id=%s payment_id=%s",
            order_id,
            payment_id,
        )
        return False

    logger.info(
        "Coinos buyer payout confirmed order_id=%s payment_id=%s amount=%s",
        order_id,
        payment_id,
        amount_sats,
    )
    audit_event("buyer_payout_completed", order_id=order_id, order_code=order_code, side=order_side, seller_id=seller_id, buyer_id=buyer_id, amount_sats=amount_sats)
    await notify_completed_trade(context, order_code, order_id, seller_id, buyer_id)
    return True



def mark_seller_invoice_sent(
    order_id,
    order_side,
    seller_id,
    assignment_attempt,
    invoice
):
    sent_at_unix = int(time.time())
    deadline_unix = sent_at_unix + SELLER_INVOICE_PAYMENT_SECONDS
    assigned_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        """
        UPDATE orders
        SET seller_invoice_attempt = ?, seller_invoice_sent_at_unix = ?,
            seller_invoice_deadline_unix = ?,
            seller_payment_confirmed_at_unix = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND assignment_attempt = ?
          AND manager_invoice = ? AND seller_invoice_attempt IS NULL
        """,
        (
            assignment_attempt,
            sent_at_unix,
            deadline_unix,
            order_id,
            order_side,
            assigned_status,
            seller_id,
            assignment_attempt,
            invoice
        )
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return None

    cursor.execute(
        """
        UPDATE manager_transactions
        SET status = ?
        WHERE order_id = ? AND manager_id = ? AND status = ?
          AND assignment_attempt = ?
        """,
        ("FORWARDED", order_id, ADMIN_ID, "PENDING", assignment_attempt)
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return None

    conn.commit()
    conn.close()
    return deadline_unix



def reset_seller_invoice_delivery_claim(
    order_id, order_side, seller_id, assignment_attempt, invoice
):
    """Release a pre-send delivery claim only if this exact attempt is still unpaid."""
    assigned_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        """
        UPDATE orders
        SET seller_invoice_attempt = NULL, seller_invoice_sent_at_unix = NULL,
            seller_invoice_deadline_unix = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND assignment_attempt = ?
          AND manager_invoice = ? AND seller_invoice_attempt = ?
          AND seller_payment_confirmed_at_unix IS NULL
        """,
        (
            order_id, order_side, assigned_status, seller_id, assignment_attempt,
            invoice, assignment_attempt
        )
    )
    released = cursor.rowcount == 1
    if released:
        cursor.execute(
            """
            UPDATE manager_transactions
            SET status = ?
            WHERE order_id = ? AND manager_id = ? AND status = ?
              AND assignment_attempt = ?
            """,
            ("PENDING", order_id, ADMIN_ID, "FORWARDED", assignment_attempt)
        )
    conn.commit()
    conn.close()
    return released



def reset_unsent_manager_invoice(
    order_id,
    order_side,
    seller_id,
    assignment_attempt,
    invoice
):
    assigned_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
    conn = db_connect(timeout=30)
    conn.execute(
        """
        UPDATE orders
        SET manager_invoice = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND assignment_attempt = ?
          AND manager_invoice = ? AND seller_invoice_sent_at_unix IS NULL
        """,
        (
            order_id,
            order_side,
            assigned_status,
            seller_id,
            assignment_attempt,
            invoice
        )
    )
    conn.commit()
    conn.close()
