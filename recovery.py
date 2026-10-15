"""Order expiration, timeout recovery, reopening, and cancellation notices."""

import asyncio
import time

from telegram.error import BadRequest, TelegramError

from database import db_connect
from observability import logger
from order_publishing import publish_buy_order, publish_sell_order
from settings import (
    ADMIN_ID, CHANNEL_ID, OPEN_ORDER_EXPIRATION_MESSAGE,
    OPEN_ORDER_LIFETIME_SECONDS, ORDER_TIMEOUT_POLL_SECONDS, REPOST_CLAIM_STALE_SECONDS,
)
from settlement import process_coinos_escrow_invoices, reconcile_unknown_buyer_payouts


def claim_due_seller_payment_timeouts():
    now_unix = int(time.time())
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    due_orders = cursor.execute(
        """
        SELECT id, order_side, seller_id, buyer_id, assignment_attempt,
               open_generation
        FROM orders
        WHERE (
                (order_side = ? AND status = ?)
                OR (order_side = ? AND status = ?)
              )
          AND seller_invoice_deadline_unix IS NOT NULL
          AND seller_invoice_deadline_unix <= ?
          AND seller_invoice_attempt = assignment_attempt
          AND seller_payment_confirmed_at_unix IS NULL
        ORDER BY id
        """,
        ("SELL", "BUYER_ASSIGNED", "BUY", "SELLER_ASSIGNED", now_unix)
    ).fetchall()

    claimed_count = 0
    for (
        order_id,
        order_side,
        seller_id,
        buyer_id,
        assignment_attempt,
        open_generation
    ) in due_orders:
        creator_id = seller_id if order_side == "SELL" else buyer_id
        reopened_generation = open_generation + 1
        assignment_column = "buyer_id" if order_side == "SELL" else "seller_id"
        expected_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
        cursor.execute(
            f"""
            UPDATE orders
            SET {assignment_column} = NULL,
                status = ?, channel_message_id = NULL,
                open_since_unix = NULL, open_generation = ?,
                manager_invoice = NULL,
                coinos_invoice_id = NULL,
                coinos_payment_hash = NULL,
                coinos_invoice_attempt = NULL,
                seller_invoice_attempt = NULL,
                seller_invoice_sent_at_unix = NULL,
                seller_invoice_deadline_unix = NULL,
                seller_payment_confirmed_at_unix = NULL,
                price_toman = CASE WHEN price_type = 'fixed' THEN price_toman ELSE NULL END,
                market_reference_toman = NULL,
                price_locked_at_unix = NULL,
                repost_claimed_at_unix = NULL
            WHERE id = ? AND order_side = ? AND status = ?
              AND seller_id = ? AND assignment_attempt = ?
              AND seller_invoice_attempt = assignment_attempt
              AND seller_invoice_deadline_unix IS NOT NULL
              AND seller_invoice_deadline_unix <= ?
              AND seller_payment_confirmed_at_unix IS NULL
            """,
            (
                "REOPENING",
                reopened_generation,
                order_id,
                order_side,
                expected_status,
                seller_id,
                assignment_attempt,
                now_unix
            )
        )
        if cursor.rowcount != 1:
            continue

        cursor.execute(
            """
            INSERT OR IGNORE INTO order_attempt_timeouts
                (order_id, assignment_attempt, reopened_generation,
                 seller_id, creator_id, created_at_unix)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                assignment_attempt,
                reopened_generation,
                seller_id,
                creator_id,
                now_unix
            )
        )
        cursor.execute(
            """
            UPDATE manager_transactions
            SET status = ?
            WHERE order_id = ? AND assignment_attempt = ?
              AND status IN (?, ?)
            """,
            ("TIMED_OUT", order_id, assignment_attempt, "PENDING", "FORWARDED")
        )
        claimed_count += 1

    conn.commit()
    conn.close()
    return claimed_count



async def publish_reopening_order(context, order_id):
    now_unix = int(time.time())
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        """
        UPDATE orders
        SET repost_claimed_at_unix = ?
        WHERE id = ? AND status = ? AND channel_message_id IS NULL
          AND (
              repost_claimed_at_unix IS NULL
              OR repost_claimed_at_unix <= ?
          )
        """,
        (
            now_unix,
            order_id,
            "REOPENING",
            now_unix - REPOST_CLAIM_STALE_SECONDS
        )
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return False

    order = cursor.execute(
        """
        SELECT order_side, seller_id, buyer_id, amount_sats, price_type,
               price_toman, description, open_generation, market_offset_percent
        FROM orders
        WHERE id = ? AND status = ?
        """,
        (order_id, "REOPENING")
    ).fetchone()
    conn.commit()
    conn.close()
    if order is None:
        return False

    (
        order_side,
        seller_id,
        buyer_id,
        amount_sats,
        price_type,
        price_toman,
        description,
        open_generation,
        market_offset_percent
    ) = order

    try:
        if order_side == "SELL":
            channel_message_id = await publish_sell_order(
                context,
                order_id,
                seller_id,
                amount_sats,
                price_type,
                price_toman,
                description,
                open_generation,
                market_offset_percent
            )
        else:
            channel_message_id = await publish_buy_order(
                context,
                order_id,
                buyer_id,
                amount_sats,
                price_type,
                price_toman,
                description,
                open_generation,
                market_offset_percent
            )
    except TelegramError:
        conn = db_connect(timeout=30)
        conn.execute(
            """
            UPDATE orders
            SET repost_claimed_at_unix = NULL
            WHERE id = ? AND status = ? AND open_generation = ?
              AND repost_claimed_at_unix = ?
            """,
            (order_id, "REOPENING", open_generation, now_unix)
        )
        conn.commit()
        conn.close()
        return False

    posted_at_unix = int(time.time())
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET status = ?, channel_message_id = ?, open_since_unix = ?,
            repost_claimed_at_unix = NULL,
            expired_at_unix = NULL,
            expiration_notification_sent_at_unix = NULL,
            expiration_delete_attempted_at_unix = NULL
        WHERE id = ? AND status = ? AND open_generation = ?
          AND channel_message_id IS NULL AND repost_claimed_at_unix = ?
        """,
        (
            "OPEN",
            channel_message_id,
            posted_at_unix,
            order_id,
            "REOPENING",
            open_generation,
            now_unix
        )
    )
    published = cursor.rowcount == 1
    if published:
        cursor.execute(
            """
            UPDATE order_attempt_timeouts
            SET reposted_at_unix = ?
            WHERE order_id = ? AND reopened_generation = ?
              AND reposted_at_unix IS NULL
            """,
            (posted_at_unix, order_id, open_generation)
        )
    conn.commit()
    conn.close()

    if not published:
        try:
            await context.bot.delete_message(
                chat_id=CHANNEL_ID,
                message_id=channel_message_id
            )
        except TelegramError:
            pass
    return published



async def recover_reopening_orders(context):
    conn = db_connect()
    reopening_order_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM orders WHERE status = ? ORDER BY id",
            ("REOPENING",)
        ).fetchall()
    ]
    conn.close()
    for order_id in reopening_order_ids:
        await publish_reopening_order(context, order_id)



async def notify_seller_payment_timeouts(context):
    conn = db_connect()
    events = conn.execute(
        """
        SELECT e.order_id, e.assignment_attempt, e.seller_id, e.creator_id,
               e.seller_notified_at_unix, e.creator_notified_at_unix,
               e.reposted_at_unix, o.order_code
        FROM order_attempt_timeouts e
        JOIN orders o ON o.id = e.order_id
        WHERE (
              e.seller_notified_at_unix IS NULL
              OR (
                  e.reposted_at_unix IS NOT NULL
                  AND e.creator_notified_at_unix IS NULL
              )
          )
        ORDER BY e.order_id, e.assignment_attempt
        """,
        ()
    ).fetchall()
    conn.close()

    for (
        order_id,
        assignment_attempt,
        seller_id,
        creator_id,
        seller_notified_at_unix,
        creator_notified_at_unix,
        reposted_at_unix,
        order_code
    ) in events:
        if seller_notified_at_unix is None:
            try:
                await context.bot.send_message(
                    chat_id=seller_id,
                    text=(
                        f"مهلت ۲۰ دقیقه‌ای پرداخت Invoice سفارش {order_code} به پایان رسید "
                        "و سفارش از رزرو شما خارج شد."
                    )
                )
            except TelegramError:
                pass
            else:
                conn = db_connect()
                conn.execute(
                    """
                    UPDATE order_attempt_timeouts
                    SET seller_notified_at_unix = ?
                    WHERE order_id = ? AND assignment_attempt = ?
                      AND seller_notified_at_unix IS NULL
                    """,
                    (int(time.time()), order_id, assignment_attempt)
                )
                conn.commit()
                conn.close()

        if creator_notified_at_unix is not None:
            continue
        if creator_id == seller_id:
            conn = db_connect()
            conn.execute(
                """
                UPDATE order_attempt_timeouts
                SET creator_notified_at_unix = ?
                WHERE order_id = ? AND assignment_attempt = ?
                  AND creator_notified_at_unix IS NULL
                """,
                (int(time.time()), order_id, assignment_attempt)
            )
            conn.commit()
            conn.close()
            continue

        if reposted_at_unix is None:
            continue

        try:
            await context.bot.send_message(
                chat_id=creator_id,
                text=(
                    f"سفارش {order_code} به‌دلیل پایان مهلت ۲۰ دقیقه‌ای فروشنده "
                    "دوباره در کانال منتشر شد."
                )
            )
        except TelegramError:
            pass
        else:
            conn = db_connect()
            conn.execute(
                """
                UPDATE order_attempt_timeouts
                SET creator_notified_at_unix = ?
                WHERE order_id = ? AND assignment_attempt = ?
                  AND creator_notified_at_unix IS NULL
                """,
                (int(time.time()), order_id, assignment_attempt)
            )
            conn.commit()
            conn.close()



def expire_due_open_orders():
    now_unix = int(time.time())
    expiration_cutoff = now_unix - OPEN_ORDER_LIFETIME_SECONDS
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    due_orders = cursor.execute(
        """
        SELECT id, open_generation
        FROM orders
        WHERE status = ? AND channel_message_id IS NOT NULL
          AND open_since_unix IS NOT NULL AND open_since_unix <= ?
        ORDER BY id
        """,
        ("OPEN", expiration_cutoff)
    ).fetchall()
    expired_count = 0
    for order_id, open_generation in due_orders:
        cursor.execute(
            """
            UPDATE orders
            SET status = ?, expired_at_unix = ?
            WHERE id = ? AND status = ? AND open_generation = ?
              AND channel_message_id IS NOT NULL
              AND open_since_unix IS NOT NULL AND open_since_unix <= ?
            """,
            (
                "EXPIRED",
                now_unix,
                order_id,
                "OPEN",
                open_generation,
                expiration_cutoff
            )
        )
        if cursor.rowcount == 1:
            expired_count += 1
    conn.commit()
    conn.close()
    return expired_count



async def process_expired_order_side_effects(context):
    conn = db_connect()
    expired_orders = conn.execute(
        """
        SELECT id, order_side, seller_id, buyer_id, channel_message_id,
               expiration_notification_sent_at_unix
        FROM orders
        WHERE status = ?
          AND (
              channel_message_id IS NOT NULL
              OR expiration_notification_sent_at_unix IS NULL
          )
        ORDER BY id
        """,
        ("EXPIRED",)
    ).fetchall()
    conn.close()

    for (
        order_id,
        order_side,
        seller_id,
        buyer_id,
        channel_message_id,
        notification_sent_at_unix
    ) in expired_orders:
        if channel_message_id is not None:
            delete_complete = False
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=channel_message_id
                )
            except BadRequest:
                delete_complete = True
            except TelegramError:
                pass
            else:
                delete_complete = True

            if delete_complete:
                conn = db_connect()
                conn.execute(
                    """
                    UPDATE orders
                    SET channel_message_id = NULL,
                        expiration_delete_attempted_at_unix = ?
                    WHERE id = ? AND status = ? AND channel_message_id = ?
                    """,
                    (int(time.time()), order_id, "EXPIRED", channel_message_id)
                )
                conn.commit()
                conn.close()

        if notification_sent_at_unix is not None:
            continue
        creator_id = seller_id if order_side == "SELL" else buyer_id
        try:
            await context.bot.send_message(
                chat_id=creator_id,
                text=OPEN_ORDER_EXPIRATION_MESSAGE
            )
        except TelegramError:
            pass
        else:
            conn = db_connect()
            conn.execute(
                """
                UPDATE orders
                SET expiration_notification_sent_at_unix = ?
                WHERE id = ? AND status = ?
                  AND expiration_notification_sent_at_unix IS NULL
                """,
                (int(time.time()), order_id, "EXPIRED")
            )
            conn.commit()
            conn.close()



async def run_order_timeout_recovery_once(context):
    await process_coinos_escrow_invoices(context)
    await reconcile_unknown_buyer_payouts(context)
    claim_due_seller_payment_timeouts()
    await recover_reopening_orders(context)
    await notify_seller_payment_timeouts(context)
    expire_due_open_orders()
    await process_expired_order_side_effects(context)



async def order_timeout_worker(context):
    while True:
        try:
            await run_order_timeout_recovery_once(context)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Order timeout recovery failed")
        await asyncio.sleep(ORDER_TIMEOUT_POLL_SECONDS)



async def notify_escrow_cancellation(
    context,
    order_id,
    order_code,
    amount_sats,
    seller_id,
    buyer_id
):
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                " معامله پس از دریافت بیتکوین فروشنده لغو شد.\n\n"
                f"کد سفارش: {order_code}\n"
                f"شناسه سفارش: {order_id}\n"
                f"مبلغ اصلی: {amount_sats:,} ساتوشی\n"
                f"فروشنده: {seller_id}\n"
                f"خریدار: {buyer_id}\n\n"
                "لطفاً ابتدا بررسی کنید آیا پیام پشتیبانی یا اختلافی درباره این معامله وجود دارد.\n\n"
                "اگر اختلافی مطرح نشده است، Invoice بازپرداخت را از فروشنده دریافت و ساتوشیهای فروشنده را بازگردانید."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "معامله لغو شد و بیتکوین شما در انتظار بازپرداخت توسط پشتیبانی است.\n"
                "در صورت وجود اختلاف موضوع ابتدا بررسی خواهد شد."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                " معامله لغو شد و در انتظار بررسی پشتیبانی و رسیدگی به بازپرداخت است."
            )
        )
    except TelegramError:
        pass



async def notify_related_users_of_cancellation(
    context,
    order_code,
    seller_id,
    buyer_id,
    canceled_by_admin=False,
    reservation_released=False,
    exclude_user_id=None
):
    if canceled_by_admin:
        text = f"❌ سفارش {order_code} توسط مدیر لغو شد."
    elif reservation_released:
        text = f"ℹ️ طرف مقابل از سفارش {order_code} خارج شد و سفارش دوباره فعال شد."
    else:
        text = f"❌ سفارش {order_code} توسط طرف مقابل لغو شد."

    notified_user_ids = set()
    for user_id in (seller_id, buyer_id):
        if (
            user_id is None
            or user_id == exclude_user_id
            or user_id in notified_user_ids
        ):
            continue
        notified_user_ids.add(user_id)
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except TelegramError:
            pass

