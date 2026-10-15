"""User order cancellation and channel cleanup."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from callbacks import SafeCallbackQuery
from database import db_connect
from observability import audit_event, logger
from recovery import notify_escrow_cancellation, notify_related_users_of_cancellation, publish_reopening_order
from settings import ACTIVE_ORDER_STATUSES, ADMIN_ID, CHANNEL_ID, ESCROWED_ORDER_STATUSES


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()
    user = update.effective_user

    conn = db_connect()
    cursor = conn.cursor()
    if user.id == ADMIN_ID:
        active_orders = cursor.execute(
            """
            SELECT id, order_code
            FROM orders
            WHERE status IN (?, ?, ?, ?, ?, ?, ?, ?)
            ORDER BY id
            """,
            ACTIVE_ORDER_STATUSES
        ).fetchall()
    else:
        active_orders = cursor.execute(
            """
            SELECT id, order_code
            FROM orders
            WHERE (seller_id = ? OR buyer_id = ?)
              AND status IN (?, ?, ?, ?, ?, ?, ?, ?)
            ORDER BY id
            """,
            (user.id, user.id, *ACTIVE_ORDER_STATUSES)
        ).fetchall()
    conn.close()

    if not active_orders:
        context.user_data.clear()
        await update.message.reply_text(
            "ℹ️ شما هیچ سفارش فعالی ندارید."
        )
        return

    keyboard = [
        [InlineKeyboardButton(order_code, callback_data=f"cancel_order:{order_id}")]
        for order_id, order_code in active_orders
    ]

    await update.message.reply_text(
        "کد سفارشی را که میخواهید حذف کنید انتخاب کنید.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


    return ConversationHandler.END



async def cancel_selected_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)
    await query.answer()

    user = update.effective_user
    order_id = int(query.data.split(":", 1)[1])

    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        """
        SELECT order_code, seller_id, buyer_id, order_side, status,
               channel_message_id, amount_sats, price_type, price_toman,
               description, assignment_attempt, payout_status
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if order is None:
        conn.close()
        await query.edit_message_text(
            "ℹ️ این سفارش فعال نیست یا متعلق به شما نیست."
        )
        return

    (
        order_code,
        seller_id,
        buyer_id,
        order_side,
        status,
        channel_message_id,
        amount,
        price_type,
        price,
        description,
        assignment_attempt,
        payout_status
    ) = order

    if payout_status in ("PROCESSING", "UNKNOWN"):
        conn.close()
        await query.edit_message_text(
            "⚠️ وضعیت پرداخت Coinos در حال بررسی است. تا تعیین نتیجه، این معامله قابل لغو نیست."
        )
        context.user_data.clear()
        return

    if user.id == ADMIN_ID and status in ACTIVE_ORDER_STATUSES:
        target_status = (
            "CANCELED_REFUND_PENDING"
            if status in ESCROWED_ORDER_STATUSES
            else "CANCELED"
        )
        cursor.execute(
            "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
            (target_status, order_id, status)
        )
        if cursor.rowcount != 1:
            conn.close()
            await query.edit_message_text(
                "ℹ️ وضعیت سفارش تغییر کرده است؛ لغو انجام نشد."
            )
            return

        conn.commit()
        conn.close()

        if channel_message_id is not None:
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=channel_message_id
                )
            except TelegramError:
                pass

        if target_status == "CANCELED_REFUND_PENDING":
            await notify_escrow_cancellation(
                context, order_id, order_code, amount, seller_id, buyer_id
            )
        else:
            await notify_related_users_of_cancellation(
                context,
                order_code,
                seller_id,
                buyer_id,
                canceled_by_admin=True,
                exclude_user_id=ADMIN_ID
            )
        logger.warning(
            "Admin canceled order order_id=%s order_code=%s previous_status=%s new_status=%s",
            order_id, order_code, status, target_status
        )
        await query.edit_message_text(
            f"✅ سفارش {order_code} توسط مدیر لغو شد.\nوضعیت: {target_status}"
        )
        context.user_data.clear()
        return

    if (
        status in ESCROWED_ORDER_STATUSES
        and user.id in (seller_id, buyer_id)
    ):
        cursor.execute(
            """
            UPDATE orders
            SET status = ?
            WHERE id = ? AND status = ?
              AND (seller_id = ? OR buyer_id = ?)
            """,
            (
                "CANCELED_REFUND_PENDING",
                order_id,
                status,
                user.id,
                user.id
            )
        )
        if cursor.rowcount != 1:
            conn.close()
            await query.edit_message_text(
                "ℹ️ این معامله دیگر قابل لغو نیست."
            )
            return

        conn.commit()
        conn.close()

        audit_event(
            "order_canceled",
            order_id=order_id,
            order_code=order_code,
            side=order_side,
            actor_id=user.id,
            previous_status=status,
            new_status="CANCELED_REFUND_PENDING",
        )
        await notify_escrow_cancellation(
            context,
            order_id,
            order_code,
            amount,
            seller_id,
            buyer_id
        )
        await query.edit_message_text(
            "✅ معامله لغو و برای بررسی و بازپرداخت به پشتیبانی ارجاع شد."
        )
        context.user_data.clear()
        return

    creator_id = seller_id if order_side == "SELL" else buyer_id
    assigned_status = (
        "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
    )

    if creator_id == user.id and status in (
        "OPEN",
        "REOPENING",
        assigned_status
    ):
        cursor.execute(
            """
            UPDATE orders
            SET status = ?
            WHERE id = ? AND status = ?
              AND (seller_id = ? OR buyer_id = ?)
            """,
            ("CANCELED", order_id, status, user.id, user.id)
        )
        if cursor.rowcount != 1:
            conn.close()
            await query.edit_message_text(
                "ℹ️ وضعیت سفارش تغییر کرده است؛ لغو عادی انجام نشد."
            )
            return

        conn.commit()
        conn.close()

        if channel_message_id is not None:
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=channel_message_id
                )
            except TelegramError:
                pass

        if status == assigned_status:
            await notify_related_users_of_cancellation(
                context,
                order_code,
                seller_id,
                buyer_id,
                exclude_user_id=user.id
            )
        audit_event(
            "order_canceled",
            order_id=order_id,
            order_code=order_code,
            side=order_side,
            actor_id=user.id,
            previous_status=status,
            new_status="CANCELED",
        )
        await query.edit_message_text("✅ سفارش انتخاب‌شده شما لغو شد.")
        context.user_data.clear()
        return

    if buyer_id == user.id and status == "BUYER_ASSIGNED":
        cursor.execute(
            """
            UPDATE orders
            SET buyer_id = NULL, status = ?, channel_message_id = NULL,
                open_since_unix = NULL,
                open_generation = open_generation + 1,
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
            WHERE id = ? AND buyer_id = ? AND status = ?
            """,
            ("REOPENING", order_id, user.id, "BUYER_ASSIGNED")
        )
        if cursor.rowcount != 1:
            conn.close()
            await query.edit_message_text("ℹ️ این سفارش دیگر در دسترس نیست.")
            return

        cursor.execute(
            """
            UPDATE manager_transactions
            SET status = ?
            WHERE order_id = ? AND assignment_attempt = ?
              AND status IN (?, ?)
            """,
            ("RELEASED", order_id, assignment_attempt, "PENDING", "FORWARDED")
        )
        conn.commit()
        conn.close()

        audit_event(
            "order_reservation_released",
            order_id=order_id,
            order_code=order_code,
            side=order_side,
            actor_id=user.id,
            previous_status=status,
            new_status="REOPENING",
        )
        await notify_related_users_of_cancellation(
            context,
            order_code,
            seller_id,
            buyer_id,
            reservation_released=True,
            exclude_user_id=user.id
        )

        republished = await publish_reopening_order(context, order_id)

        await query.edit_message_text(
            (
                "✅ رزرو سفارش لغو شد و سفارش دوباره در کانال منتشر شد."
                if republished
                else "✅ رزرو سفارش لغو شد و انتشار مجدد آن در حال بازیابی است."
            )
        )
        context.user_data.clear()
        return

    if seller_id == user.id and status == "SELLER_ASSIGNED":
        cursor.execute(
            """
            UPDATE orders
            SET seller_id = NULL, status = ?, channel_message_id = NULL,
                open_since_unix = NULL,
                open_generation = open_generation + 1,
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
            WHERE id = ? AND seller_id = ? AND status = ?
            """,
            ("REOPENING", order_id, user.id, "SELLER_ASSIGNED")
        )
        if cursor.rowcount != 1:
            conn.close()
            await query.edit_message_text(
                "ℹ️ این سفارش دیگر در دسترس نیست."
            )
            return

        cursor.execute(
            """
            UPDATE manager_transactions
            SET status = ?
            WHERE order_id = ? AND assignment_attempt = ?
              AND status IN (?, ?)
            """,
            ("RELEASED", order_id, assignment_attempt, "PENDING", "FORWARDED")
        )
        conn.commit()
        conn.close()

        audit_event(
            "order_reservation_released",
            order_id=order_id,
            order_code=order_code,
            side=order_side,
            actor_id=user.id,
            previous_status=status,
            new_status="REOPENING",
        )
        await notify_related_users_of_cancellation(
            context,
            order_code,
            seller_id,
            buyer_id,
            reservation_released=True,
            exclude_user_id=user.id
        )

        republished = await publish_reopening_order(context, order_id)

        await query.edit_message_text(
            (
                "✅ رزرو سفارش لغو شد و سفارش دوباره در کانال منتشر شد."
                if republished
                else "✅ رزرو سفارش لغو شد و انتشار مجدد آن در حال بازیابی است."
            )
        )
        context.user_data.clear()
        return

    conn.close()
    await query.edit_message_text(
        "ℹ️ این سفارش فعال نیست یا متعلق به شما نیست."
    )
    return



async def cancel_all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()
    user = update.effective_user

    conn = db_connect()
    active_orders = conn.execute(
        """
        SELECT id, order_code, seller_id, buyer_id, order_side,
               channel_message_id, status, amount_sats, assignment_attempt, payout_status
        FROM orders
        WHERE (seller_id = ? OR buyer_id = ?)
          AND status IN (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user.id, user.id, *ACTIVE_ORDER_STATUSES)
    ).fetchall()
    conn.close()

    changed_orders = []

    for (
        order_id,
        order_code,
        seller_id,
        buyer_id,
        order_side,
        channel_message_id,
        status,
        amount,
        assignment_attempt,
        payout_status
    ) in active_orders:
        if payout_status in ("PROCESSING", "UNKNOWN"):
            continue
        creator_id = seller_id if order_side == "SELL" else buyer_id
        assigned_status = (
            "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
        )

        conn = db_connect(timeout=30)
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        if status in ESCROWED_ORDER_STATUSES:
            cursor.execute(
                """
                UPDATE orders
                SET status = ?
                WHERE id = ? AND status = ?
                  AND (seller_id = ? OR buyer_id = ?)
                """,
                (
                    "CANCELED_REFUND_PENDING",
                    order_id,
                    status,
                    user.id,
                    user.id
                )
            )
            if cursor.rowcount == 1:
                conn.commit()
                changed_orders.append((
                    "REFUND_PENDING", order_id, order_code, seller_id, buyer_id,
                    order_side, channel_message_id, status, amount
                ))
            else:
                conn.rollback()
            conn.close()
            continue

        if creator_id == user.id and status in (
            "OPEN",
            "REOPENING",
            assigned_status
        ):
            cursor.execute(
                """
                UPDATE orders
                SET status = ?
                WHERE id = ? AND status = ?
                  AND (seller_id = ? OR buyer_id = ?)
                """,
                ("CANCELED", order_id, status, user.id, user.id)
            )
            if cursor.rowcount == 1:
                conn.commit()
                changed_orders.append((
                    "CANCELED", order_id, order_code, seller_id, buyer_id,
                    order_side, channel_message_id, status, amount
                ))
            else:
                conn.rollback()
            conn.close()
            continue

        is_buyer_taker = (
            order_side == "SELL"
            and buyer_id == user.id
            and status == "BUYER_ASSIGNED"
        )
        is_seller_taker = (
            order_side == "BUY"
            and seller_id == user.id
            and status == "SELLER_ASSIGNED"
        )

        if is_buyer_taker or is_seller_taker:
            participant_column = "buyer_id" if is_buyer_taker else "seller_id"
            cursor.execute(
                f"""
                UPDATE orders
                SET {participant_column} = NULL, status = ?,
                    channel_message_id = NULL, open_since_unix = NULL,
                    open_generation = open_generation + 1,
                    manager_invoice = NULL, coinos_invoice_id = NULL,
                    coinos_payment_hash = NULL, coinos_invoice_attempt = NULL,
                    seller_invoice_attempt = NULL,
                    seller_invoice_sent_at_unix = NULL,
                    seller_invoice_deadline_unix = NULL,
                    seller_payment_confirmed_at_unix = NULL,
                    repost_claimed_at_unix = NULL
                WHERE id = ? AND {participant_column} = ? AND status = ?
                """,
                ("REOPENING", order_id, user.id, status)
            )
            if cursor.rowcount == 1:
                cursor.execute(
                    """
                    UPDATE manager_transactions
                    SET status = ?
                    WHERE order_id = ? AND assignment_attempt = ?
                      AND status IN (?, ?)
                    """,
                    (
                        "RELEASED", order_id, assignment_attempt,
                        "PENDING", "FORWARDED"
                    )
                )
                conn.commit()
                changed_orders.append((
                    "REOPENING", order_id, order_code, seller_id, buyer_id,
                    order_side, channel_message_id, status, amount
                ))
            else:
                conn.rollback()
            conn.close()
            continue

        conn.rollback()
        conn.close()

    for (
        action, order_id, order_code, seller_id, buyer_id, order_side,
        channel_message_id, previous_status, amount
    ) in changed_orders:
        if action == "REFUND_PENDING":
            audit_event(
                "order_canceled",
                order_id=order_id,
                order_code=order_code,
                side=order_side,
                actor_id=user.id,
                previous_status=previous_status,
                new_status="CANCELED_REFUND_PENDING",
            )
            await notify_escrow_cancellation(
                context, order_id, order_code, amount, seller_id, buyer_id
            )
            continue

        if action == "REOPENING":
            audit_event(
                "order_reservation_released",
                order_id=order_id,
                order_code=order_code,
                side=order_side,
                actor_id=user.id,
                previous_status=previous_status,
                new_status="REOPENING",
            )
            await notify_related_users_of_cancellation(
                context,
                order_code,
                seller_id,
                buyer_id,
                reservation_released=True,
                exclude_user_id=user.id
            )
            await publish_reopening_order(context, order_id)
            continue

        if channel_message_id is not None:
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=channel_message_id
                )
            except TelegramError:
                pass

        audit_event(
            "order_canceled",
            order_id=order_id,
            order_code=order_code,
            side=order_side,
            actor_id=user.id,
            previous_status=previous_status,
            new_status="CANCELED",
        )
        if previous_status in ("BUYER_ASSIGNED", "SELLER_ASSIGNED"):
            await notify_related_users_of_cancellation(
                context,
                order_code,
                seller_id,
                buyer_id,
                exclude_user_id=user.id
            )

    context.user_data.clear()

    if changed_orders:
        await update.message.reply_text(
            f"✅ همه سفارش‌های فعال شما رسیدگی شدند. تعداد: {len(changed_orders)}"
        )
    else:
        await update.message.reply_text(
            "ℹ️ شما هیچ سفارش فعالی ندارید."
        )

    return ConversationHandler.END

