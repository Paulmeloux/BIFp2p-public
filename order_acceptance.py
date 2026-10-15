"""Order selection, acceptance, and counterparty assignment."""

import time
from datetime import datetime
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from callbacks import SafeCallbackQuery
from database import db_connect
from observability import audit_event, logger
from pricing import current_order_price_details, format_percent
from settings import ADMIN_ID, CHANNEL_ID
from settlement import send_coinos_escrow_invoice
from telegram_ui import (
    buy_callback_data, confirm_buy_channel_order_callback_data,
    confirm_channel_order_callback_data, decline_buy_channel_order_callback_data,
    decline_channel_order_callback_data, main_menu, parse_open_order_callback,
    parse_order_deep_link, sell_buy_order_callback_data,
)
from users import require_order_eligibility, save_user, seller_fee_percent_from_score, user_reputation_score


async def acceptance_price_snapshot(order_id, expected_generation):
    conn = db_connect()
    row = conn.execute(
        """
        SELECT amount_sats, price_type, price_toman, market_offset_percent,
               status, open_generation
        FROM orders WHERE id = ?
        """,
        (order_id,)
    ).fetchone()
    conn.close()
    if row is None or row[4] != "OPEN" or row[5] != expected_generation:
        return None
    amount_sats, price_type, price_toman, offset, _, _ = row
    try:
        market_price, final_price, fiat_total = await current_order_price_details(
            price_type, price_toman, offset, amount_sats
        )
    except RuntimeError:
        return "unavailable"
    return {
        "amount_sats": amount_sats,
        "price_type": price_type,
        "offset": offset,
        "market_price": market_price,
        "final_price": final_price,
        "fiat_total": fiat_total,
    }



def acceptance_price_text(snapshot):
    if snapshot["price_type"] == "fixed":
        return (
            "روش قیمت‌گذاری: قیمت دلخواه\n"
            f"مبلغ معامله: {snapshot['fiat_total']:,} تومان"
        )

    lines = [
        f"قیمت مرجع بازار: {snapshot['market_price']:,} تومان / BTC"
    ]
    offset = Decimal(str(snapshot["offset"] or 0))
    if offset == 0:
        lines.append("روش قیمت‌گذاری: قیمت بازار")
    else:
        lines.append(f"روش قیمت‌گذاری: {format_percent(offset)}٪ بالاتر از بازار")
    lines.append(f"قیمت فعلی سفارش: {snapshot['final_price']:,} تومان / BTC")
    lines.append(f"مبلغ تقریبی معامله: {snapshot['fiat_total']:,} تومان")
    lines.append("قیمت نهایی در لحظه تأیید پذیرش دوباره محاسبه و سپس ثابت می‌شود.")
    return "\n".join(lines)



async def send_deep_link_order_prompt(update, context, action, order_id, callback_generation):
    if not await require_order_eligibility(update, context):
        return

    user = update.effective_user
    conn = db_connect()
    order = conn.execute(
        """
        SELECT seller_id, buyer_id, order_side, status, open_generation
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()
    conn.close()

    if order is None:
        await update.message.reply_text("این سفارش یافت نشد.")
        return

    seller_id, buyer_id, order_side, status, open_generation = order
    if status != "OPEN" or open_generation != callback_generation:
        await update.message.reply_text("این سفارش دیگر در دسترس نیست.")
        return

    if action == "buy":
        if order_side != "SELL" or buyer_id is not None:
            await update.message.reply_text("این سفارش دیگر در دسترس نیست.")
            return
        if seller_id == user.id:
            await update.message.reply_text("شما نمی‌توانید سفارش خودتان را خریداری کنید.")
            return
        confirm_data = confirm_channel_order_callback_data(order_id, callback_generation)
        decline_data = decline_channel_order_callback_data(order_id, callback_generation)
    elif action == "sell":
        if order_side != "BUY" or seller_id is not None:
            await update.message.reply_text("این سفارش دیگر در دسترس نیست.")
            return
        if buyer_id == user.id:
            await update.message.reply_text("شما نمی‌توانید سفارش خرید خودتان را بپذیرید.")
            return
        confirm_data = confirm_buy_channel_order_callback_data(order_id, callback_generation)
        decline_data = decline_buy_channel_order_callback_data(order_id, callback_generation)
    else:
        await update.message.reply_text("درخواست نامعتبر است.")
        return

    snapshot = await acceptance_price_snapshot(order_id, callback_generation)
    if snapshot == "unavailable":
        await update.message.reply_text(
            "⚠️ دریافت قیمت بازار موقتاً ممکن نیست. سفارش همچنان فعال است؛ کمی بعد دوباره تلاش کنید."
        )
        return
    if snapshot is None:
        await update.message.reply_text("این سفارش دیگر در دسترس نیست.")
        return

    await update.message.reply_text(
        acceptance_price_text(snapshot) +
        "\n\nآیا از پذیرش این سفارش مطمئن هستید؟\n\n"
        "برداشت بی‌مورد سفارش باعث اختلال در روند معاملات و اتلاف وقت کاربران می‌شود "
        "و می‌تواند موجب محدود شدن یا ممنوعیت دائمی دسترسی شما به بات شود.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("تأیید پذیرش سفارش", callback_data=confirm_data)],
            [InlineKeyboardButton("انصراف", callback_data=decline_data)]
        ])
    )



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    user = update.effective_user

    await update.message.reply_text(
        f"Telegram ID شما: {user.id}"
    )

    if not user.username:

        await update.message.reply_text(
            "⚠️ لطفاً ابتدا Username تلگرام خود را فعال کنید."
        )

        return ConversationHandler.END


    save_user(user)

    if context.args:
        deep_link = parse_order_deep_link(context.args[0])
        if deep_link is not None:
            action, order_id, callback_generation = deep_link
            await send_deep_link_order_prompt(
                update,
                context,
                action,
                order_id,
                callback_generation
            )
            return ConversationHandler.END

    await update.message.reply_text(
        "خرید و فروش همتا به همتای بیتکوین با تسویه Lightning",
        reply_markup=main_menu()
    )



    return ConversationHandler.END



async def select_sell_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)
    callback = parse_open_order_callback(
        query.data,
        "buy_order",
        buy_callback_data
    )
    if callback is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return ConversationHandler.END
    order_id, callback_generation = callback

    await query.answer()

    if not await require_order_eligibility(update, context):
        return ConversationHandler.END

    buyer = update.effective_user
    conn = db_connect()
    order = conn.execute(
        """
        SELECT seller_id, order_side, status, buyer_id, open_generation
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()
    conn.close()

    if order is None:
        await query.answer("این سفارش یافت نشد.", show_alert=True)
        return ConversationHandler.END

    seller_id, order_side, status, assigned_buyer_id, open_generation = order
    if seller_id == buyer.id:
        await query.answer(
            "شما نمی‌توانید سفارش خودتان را خریداری کنید.",
            show_alert=True
        )
        return ConversationHandler.END

    if (
        order_side != "SELL"
        or status != "OPEN"
        or assigned_buyer_id is not None
        or open_generation != callback_generation
    ):
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return ConversationHandler.END

    snapshot = await acceptance_price_snapshot(order_id, callback_generation)
    if snapshot == "unavailable":
        await query.answer("دریافت قیمت بازار موقتاً ممکن نیست. کمی بعد دوباره تلاش کنید.", show_alert=True)
        return ConversationHandler.END
    if snapshot is None:
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return ConversationHandler.END

    try:
        await context.bot.send_message(
            chat_id=buyer.id,
            text=(
                acceptance_price_text(snapshot) +
                "\n\nآیا از پذیرش این سفارش مطمئن هستید؟\n\n"
                "برداشت بی‌مورد سفارش باعث اختلال در روند معاملات و اتلاف وقت کاربران می‌شود "
                "و می‌تواند موجب محدود شدن یا ممنوعیت دائمی دسترسی شما به بات شود."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    " تأیید پذیرش سفارش",
                    callback_data=confirm_channel_order_callback_data(
                        order_id,
                        callback_generation
                    )
                )],
                [InlineKeyboardButton(
                    " انصراف",
                    callback_data=decline_channel_order_callback_data(
                        order_id,
                        callback_generation
                    )
                )]
            ])
        )
    except TelegramError:
        await query.answer(
            "ارسال پیام خصوصی ممکن نیست. ابتدا بات را در گفتگوی خصوصی فعال کنید.",
            show_alert=True
        )
        return ConversationHandler.END

    await query.answer("پیام تأیید برای شما ارسال شد.")
    return ConversationHandler.END



async def decline_channel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)
    user = update.effective_user
    callback = parse_open_order_callback(
        query.data,
        "decline_channel_order",
        decline_channel_order_callback_data
    )
    if callback is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return ConversationHandler.END

    if query.message is None or query.message.chat_id != user.id:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    await query.edit_message_text("پذیرش سفارش لغو شد.")
    return ConversationHandler.END



async def confirm_sell_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)
    buyer = update.effective_user

    callback = parse_open_order_callback(
        query.data,
        "confirm_channel_order",
        confirm_channel_order_callback_data
    )
    if callback is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return ConversationHandler.END
    order_id, callback_generation = callback

    if query.message is None or query.message.chat_id != buyer.id:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return ConversationHandler.END

    await query.answer()

    if not await require_order_eligibility(update, context):
        return ConversationHandler.END

    snapshot = await acceptance_price_snapshot(order_id, callback_generation)
    if snapshot == "unavailable":
        await query.answer("دریافت قیمت بازار موقتاً ممکن نیست. سفارش رزرو نشد.", show_alert=True)
        return ConversationHandler.END
    if snapshot is None:
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return ConversationHandler.END

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    order = cursor.execute(
        """
        SELECT seller_id, order_code, amount_sats, channel_message_id,
               order_side, status, buyer_id, open_generation,
               assignment_attempt
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if order is None:
        conn.close()
        await query.answer("این سفارش یافت نشد.", show_alert=True)
        return ConversationHandler.END

    (
        seller_id,
        order_code,
        amount_sats,
        channel_message_id,
        order_side,
        status,
        assigned_buyer_id,
        open_generation,
        assignment_attempt
    ) = order
    if seller_id == buyer.id:
        conn.close()
        await query.answer("شما نمی‌توانید سفارش خودتان را خریداری کنید.", show_alert=True)
        return

    if (
        order_side != "SELL"
        or status != "OPEN"
        or assigned_buyer_id is not None
        or open_generation != callback_generation
    ):
        conn.close()
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return ConversationHandler.END

    seller_score = await user_reputation_score(context, seller_id)
    locked_fee_percent = seller_fee_percent_from_score(seller_score)

    cursor.execute(
        """
        UPDATE orders
        SET buyer_id = ?, status = ?, channel_message_id = NULL,
            price_toman = ?, market_reference_toman = ?, price_locked_at_unix = ?,
            fee_percent = ?,
            open_since_unix = NULL,
            assignment_attempt = assignment_attempt + 1,
            manager_invoice = NULL,
            coinos_invoice_id = NULL,
            coinos_payment_hash = NULL,
            coinos_invoice_attempt = NULL,
            seller_invoice_attempt = NULL,
            seller_invoice_sent_at_unix = NULL,
            seller_invoice_deadline_unix = NULL,
            seller_payment_confirmed_at_unix = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND buyer_id IS NULL AND seller_id != ? AND open_generation = ?
        """,
        (
            buyer.id,
            "BUYER_ASSIGNED",
            (snapshot["fiat_total"] if snapshot["price_type"] == "fixed" else snapshot["final_price"]),
            snapshot["market_price"],
            int(time.time()),
            float(locked_fee_percent),
            order_id,
            "SELL",
            "OPEN",
            buyer.id,
            callback_generation
        )
    )

    if cursor.rowcount != 1:
        conn.close()
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return

    conn.commit()
    conn.close()

    assignment_attempt += 1
    audit_event("order_assigned", order_id=order_id, order_code=order_code, side="SELL", seller_id=seller_id, buyer_id=buyer.id, attempt=assignment_attempt)

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if channel_message_id is not None:
        try:
            await context.bot.delete_message(
                chat_id=CHANNEL_ID,
                message_id=channel_message_id
            )
        except TelegramError:
            pass

    try:
        await context.bot.send_message(
            chat_id=buyer.id,
            text=(
                f"✅ سفارش {order_code} برای شما رزرو شد.\n\n"
                + (
                    f"مبلغ معامله: {snapshot['fiat_total']:,} تومان\n\n"
                    if snapshot["price_type"] == "fixed"
                    else (
                        f"قیمت نهایی: {snapshot['final_price']:,} تومان / BTC\n"
                        f"مبلغ معامله: {snapshot['fiat_total']:,} تومان\n\n"
                    )
                )
                +
                "ادامه فرایند معامله از طریق بات اطلاع‌رسانی خواهد شد."
            )
        )
    except TelegramError:
        pass

    if ADMIN_ID is not None:
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO manager_transactions
                (order_id, manager_id, status, manager_prompt_message_id,
                 assignment_attempt, created_at)
            VALUES (?, ?, ?, NULL, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                manager_id = excluded.manager_id,
                status = excluded.status,
                manager_prompt_message_id = NULL,
                assignment_attempt = excluded.assignment_attempt,
                created_at = excluded.created_at
            """,
            (
                order_id,
                ADMIN_ID,
                "PENDING",
                assignment_attempt,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        conn.commit()
        conn.close()
        try:
            await send_coinos_escrow_invoice(
                context, order_id, "SELL", seller_id, assignment_attempt
            )
        except Exception:
            logger.exception(
                "Coinos escrow start failed order_id=%s attempt=%s",
                order_id,
                assignment_attempt,
            )



async def select_buy_order_as_seller(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)
    callback = parse_open_order_callback(
        query.data,
        "sell_buy_order",
        sell_buy_order_callback_data
    )
    if callback is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return
    order_id, callback_generation = callback

    await query.answer()

    if not await require_order_eligibility(update, context):
        return

    seller = update.effective_user
    conn = db_connect()
    order = conn.execute(
        """
        SELECT buyer_id, order_side, status, seller_id, open_generation
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()
    conn.close()

    if order is None:
        await query.answer("این سفارش یافت نشد.", show_alert=True)
        return

    buyer_id, order_side, status, assigned_seller_id, open_generation = order

    if order_side != "BUY":
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    if buyer_id == seller.id:
        await query.answer(
            "شما نمی‌توانید سفارش خرید خودتان را بپذیرید.",
            show_alert=True
        )
        return

    if (
        status != "OPEN"
        or assigned_seller_id is not None
        or open_generation != callback_generation
    ):
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return

    snapshot = await acceptance_price_snapshot(order_id, callback_generation)
    if snapshot == "unavailable":
        await query.answer("دریافت قیمت بازار موقتاً ممکن نیست. کمی بعد دوباره تلاش کنید.", show_alert=True)
        return
    if snapshot is None:
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return

    try:
        await context.bot.send_message(
            chat_id=seller.id,
            text=(
                acceptance_price_text(snapshot) +
                "\n\nآیا از پذیرش این سفارش مطمئن هستید؟\n\n"
                "برداشت بی‌مورد سفارش باعث اختلال در روند معاملات و اتلاف وقت کاربران می‌شود "
                "و می‌تواند موجب محدود شدن یا ممنوعیت دائمی دسترسی شما به بات شود."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    " تأیید پذیرش سفارش",
                    callback_data=confirm_buy_channel_order_callback_data(
                        order_id,
                        callback_generation
                    )
                )],
                [InlineKeyboardButton(
                    " انصراف",
                    callback_data=decline_buy_channel_order_callback_data(
                        order_id,
                        callback_generation
                    )
                )]
            ])
        )
    except TelegramError:
        await query.answer(
            "ارسال پیام خصوصی ممکن نیست. ابتدا بات را در گفتگوی خصوصی فعال کنید.",
            show_alert=True
        )
        return

    await query.answer("پیام تأیید برای شما ارسال شد.")



async def decline_buy_channel_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)
    user = update.effective_user
    callback = parse_open_order_callback(
        query.data,
        "decline_buy_channel_order",
        decline_buy_channel_order_callback_data
    )
    if callback is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    if query.message is None or query.message.chat_id != user.id:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text("پذیرش سفارش لغو شد.")



async def confirm_buy_order_as_seller(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)
    callback = parse_open_order_callback(
        query.data,
        "confirm_buy_channel_order",
        confirm_buy_channel_order_callback_data
    )
    if callback is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return
    order_id, callback_generation = callback

    seller = update.effective_user
    if query.message is None or query.message.chat_id != seller.id:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    await query.answer()

    if not await require_order_eligibility(update, context):
        return

    snapshot = await acceptance_price_snapshot(order_id, callback_generation)
    if snapshot == "unavailable":
        await query.answer("دریافت قیمت بازار موقتاً ممکن نیست. سفارش رزرو نشد.", show_alert=True)
        return
    if snapshot is None:
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    order = cursor.execute(
        """
        SELECT order_code, buyer_id, amount_sats, order_side, status,
               seller_id, channel_message_id, open_generation,
               assignment_attempt
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if order is None:
        conn.close()
        await query.answer("این سفارش یافت نشد.", show_alert=True)
        return

    (
        order_code,
        buyer_id,
        amount_sats,
        order_side,
        status,
        assigned_seller_id,
        channel_message_id,
        open_generation,
        assignment_attempt
    ) = order

    if order_side != "BUY":
        conn.close()
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    if buyer_id == seller.id:
        conn.close()
        await query.answer(
            "شما نمی‌توانید سفارش خرید خودتان را بپذیرید.",
            show_alert=True
        )
        return

    if (
        status != "OPEN"
        or assigned_seller_id is not None
        or open_generation != callback_generation
    ):
        conn.close()
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return

    seller_score = await user_reputation_score(context, seller.id)
    locked_fee_percent = seller_fee_percent_from_score(seller_score)

    cursor.execute(
        """
        UPDATE orders
        SET seller_id = ?, status = ?, channel_message_id = NULL,
            price_toman = ?, market_reference_toman = ?, price_locked_at_unix = ?,
            fee_percent = ?,
            open_since_unix = NULL,
            assignment_attempt = assignment_attempt + 1,
            manager_invoice = NULL,
            coinos_invoice_id = NULL,
            coinos_payment_hash = NULL,
            coinos_invoice_attempt = NULL,
            seller_invoice_attempt = NULL,
            seller_invoice_sent_at_unix = NULL,
            seller_invoice_deadline_unix = NULL,
            seller_payment_confirmed_at_unix = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id IS NULL AND buyer_id = ? AND buyer_id != ?
          AND open_generation = ?
        """,
        (
            seller.id,
            "SELLER_ASSIGNED",
            (snapshot["fiat_total"] if snapshot["price_type"] == "fixed" else snapshot["final_price"]),
            snapshot["market_price"],
            int(time.time()),
            float(locked_fee_percent),
            order_id,
            "BUY",
            "OPEN",
            buyer_id,
            seller.id,
            callback_generation
        )
    )

    if cursor.rowcount != 1:
        conn.close()
        await query.answer("این سفارش دیگر در دسترس نیست.", show_alert=True)
        return

    conn.commit()
    conn.close()
    assignment_attempt += 1
    audit_event("order_assigned", order_id=order_id, order_code=order_code, side="BUY", seller_id=seller.id, buyer_id=buyer_id, attempt=assignment_attempt)

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if channel_message_id is not None:
        try:
            await context.bot.delete_message(
                chat_id=CHANNEL_ID,
                message_id=channel_message_id
            )
        except TelegramError:
            pass

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO manager_transactions
            (order_id, manager_id, status, manager_prompt_message_id,
             assignment_attempt, created_at)
        VALUES (?, ?, ?, NULL, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            manager_id = excluded.manager_id,
            status = excluded.status,
            manager_prompt_message_id = NULL,
            assignment_attempt = excluded.assignment_attempt,
            created_at = excluded.created_at
        """,
        (
            order_id,
            ADMIN_ID,
            "PENDING",
            assignment_attempt,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    conn.commit()
    conn.close()
    try:
        await send_coinos_escrow_invoice(
            context, order_id, "BUY", seller.id, assignment_attempt
        )
    except Exception:
        logger.exception(
            "Coinos escrow start failed order_id=%s attempt=%s",
            order_id,
            assignment_attempt,
        )

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                "🟠 یک فروشنده سفارش خرید شما را پذیرفت.\n"
                "معامله وارد مرحله بعد شد."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=seller.id,
            text=(
                " سفارش فروش برای شما ثبت شد.\n\n"
                + (
                    f"مبلغ معامله: {snapshot['fiat_total']:,} تومان\n\n"
                    if snapshot["price_type"] == "fixed"
                    else (
                        f"قیمت نهایی: {snapshot['final_price']:,} تومان / BTC\n"
                        f"مبلغ معامله: {snapshot['fiat_total']:,} تومان\n\n"
                    )
                )
                +
                "برای ادامه معامله، Invoice لایتنینگ مربوط به این سفارش برای شما ارسال میشود."
            )
        )
    except TelegramError:
        pass

