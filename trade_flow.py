"""BUY and SELL settlement conversations and invoice handlers."""

import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from callbacks import SafeCallbackQuery
from database import db_connect
from lightning import buyer_invoice_amount_sats
from observability import audit_event
from settings import ADMIN_ID
from settlement import auto_pay_buyer_invoice, mark_seller_invoice_sent, reset_unsent_manager_invoice
from users import format_seller_fee_percent, require_not_blacklisted, seller_fee_amount_sats


async def handle_buy_manager_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    invoice,
    manager_prompt_message_id
):
    conn = db_connect()
    cursor = conn.cursor()
    pending = cursor.execute(
        """
        SELECT o.id, o.order_code, o.seller_id, o.amount_sats,
               o.assignment_attempt, o.fee_percent
        FROM orders o
        JOIN manager_transactions mt ON mt.order_id = o.id
        WHERE o.order_side = ?
          AND o.status = ?
          AND o.manager_invoice IS NULL
          AND mt.manager_id = ?
          AND mt.status = ?
          AND mt.manager_prompt_message_id = ?
          AND mt.assignment_attempt = o.assignment_attempt
        """,
        (
            "BUY",
            "SELLER_ASSIGNED",
            ADMIN_ID,
            "PENDING",
            manager_prompt_message_id
        )
    ).fetchall()

    if not pending:
        conn.close()
        return False

    if len(pending) != 1:
        conn.close()
        await update.message.reply_text(
            "Invoice رد شد: سفارش خرید مرتبط با این پیام یکتا نیست."
        )
        return True

    order_id, order_code, seller_id, amount_sats, assignment_attempt, fee_percent = pending[0]
    fee_amount = seller_fee_amount_sats(amount_sats, fee_percent)
    expected_invoice_amount = amount_sats + fee_amount
    fee_percent_text = format_seller_fee_percent(fee_percent)
    invoice_amount = buyer_invoice_amount_sats(invoice)

    if invoice_amount is None:
        conn.close()
        await update.message.reply_text(
            "Invoice رد شد: لطفاً یک Invoice معتبر لایتنینگ ارسال کنید."
        )
        return True

    if invoice_amount != expected_invoice_amount:
        conn.close()
        await update.message.reply_text(
            f"Invoice رد شد: مبلغ Invoice باید دقیقاً {expected_invoice_amount:,} ساتوشی باشد."
        )
        return True

    cursor.execute(
        """
        UPDATE orders
        SET manager_invoice = ?
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND manager_invoice IS NULL
          AND assignment_attempt = ?
        """,
        (
            invoice,
            order_id,
            "BUY",
            "SELLER_ASSIGNED",
            seller_id,
            assignment_attempt
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await update.message.reply_text(
            "Invoice رد شد: این سفارش خرید دیگر در انتظار Invoice نیست."
        )
        return True

    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                " Invoice پرداخت بیتکوین\n\n"
                f"مبلغ سفارش: {amount_sats:,} ساتوشی\n"
                f"کارمزد {fee_percent_text}: {fee_amount:,} ساتوشی\n"
                f"مبلغ قابل پرداخت: {expected_invoice_amount:,} ساتوشی\n\n"
                "لطفاً بیتکوین خود را به Invoice زیر ارسال کنید."
            )
        )
        await context.bot.send_message(chat_id=seller_id, text=invoice)
    except TelegramError:
        reset_unsent_manager_invoice(
            order_id,
            "BUY",
            seller_id,
            assignment_attempt,
            invoice
        )
        await update.message.reply_text(
            f"ارسال Invoice سفارش {order_code} به فروشنده ناموفق بود؛ لطفاً دوباره ارسال کنید."
        )
        return True

    deadline_unix = mark_seller_invoice_sent(
        order_id,
        "BUY",
        seller_id,
        assignment_attempt,
        invoice
    )
    if deadline_unix is None:
        await update.message.reply_text(
            "Invoice ارسال شد، اما وضعیت سفارش هم‌زمان تغییر کرده است؛ تأیید این تلاش غیرفعال شد."
        )
        return True

    await update.message.reply_text(
        f"Invoice سفارش {order_code} برای فروشنده ارسال شد."
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"تأیید دریافت ساتوشی فروشنده برای سفارش {order_code}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                " ساتوشی فروشنده دریافت شد",
                callback_data=(
                    f"buy_seller_sats_received:{order_id}:{assignment_attempt}"
                )
            )
        ]])
    )
    return True



async def manager_invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the manager's Lightning invoice for the pending assigned order."""
    if update.effective_user.id != ADMIN_ID:
        return

    if update.message is None or update.message.text is None:
        return

    reply = update.message.reply_to_message
    if update.effective_chat.type == "private" and reply is not None:
        conn = db_connect()
        admin_buyer_order = conn.execute(
            """
            SELECT id
            FROM orders
            WHERE buyer_id = ? AND status = ? AND buyer_invoice IS NULL
              AND buyer_invoice_prompt_message_id = ?
            """,
            (ADMIN_ID, "FIAT_RECEIVED", reply.message_id)
        ).fetchone()
        conn.close()

        if admin_buyer_order is not None:
            await buyer_invoice_handler(update, context)
            return

    if (
        update.effective_chat.type == "private"
        and await handle_buy_buyer_invoice(update, context)
    ):
        return

    invoice = update.message.text.strip()
    conn = db_connect()
    cursor = conn.cursor()
    reply = update.message.reply_to_message
    if reply is None:
        conn.close()
        await update.message.reply_text(
            "لطفاً Invoice را در پاسخ (Reply) به پیام همان سفارش ارسال کنید."
        )
        return

    conn.close()
    if await handle_buy_manager_invoice(
        update,
        context,
        invoice,
        reply.message_id
    ):
        return

    conn = db_connect()
    cursor = conn.cursor()

    pending = cursor.execute(
        """
        SELECT o.id, o.order_code, o.seller_id, o.assignment_attempt
        FROM orders o
        JOIN manager_transactions mt ON mt.order_id = o.id
        WHERE mt.order_id = o.id
          AND mt.manager_id = ?
          AND mt.status = ?
          AND mt.manager_prompt_message_id = ?
          AND o.order_side = ?
          AND o.status = ?
          AND o.manager_invoice IS NULL
          AND mt.assignment_attempt = o.assignment_attempt
        """,
        (ADMIN_ID, "PENDING", reply.message_id, "SELL", "BUYER_ASSIGNED")
    ).fetchall()

    if len(pending) != 1:
        conn.close()
        await update.message.reply_text(
            "لطفاً Invoice را در پاسخ (Reply) به پیام مربوط به همان معامله ارسال کنید."
        )
        return

    order_id, order_code, seller_id, assignment_attempt = pending[0]
    current = cursor.execute(
        """
        SELECT status, manager_invoice, assignment_attempt
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()
    if (
        current is None
        or current[0] != "BUYER_ASSIGNED"
        or current[1] is not None
        or current[2] != assignment_attempt
    ):
        conn.close()
        await update.message.reply_text("Invoice رد شد: این سفارش دیگر در وضعیت BUYER_ASSIGNED نیست.")
        return

    cursor.execute(
        """
        UPDATE orders
        SET manager_invoice = ?
        WHERE id = ? AND status = ? AND manager_invoice IS NULL
          AND assignment_attempt = ?
        """,
        (invoice, order_id, "BUYER_ASSIGNED", assignment_attempt)
    )
    if cursor.rowcount != 1:
        conn.close()
        await update.message.reply_text("Invoice رد شد: این سفارش دیگر در انتظار Invoice نیست.")
        return
    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(chat_id=seller_id, text=invoice)
    except TelegramError:
        reset_unsent_manager_invoice(
            order_id,
            "SELL",
            seller_id,
            assignment_attempt,
            invoice
        )
        await update.message.reply_text(
            f"ارسال Invoice سفارش {order_code} به فروشنده ناموفق بود؛ لطفاً دوباره ارسال کنید."
        )
        return

    deadline_unix = mark_seller_invoice_sent(
        order_id,
        "SELL",
        seller_id,
        assignment_attempt,
        invoice
    )
    if deadline_unix is None:
        await update.message.reply_text(
            "Invoice ارسال شد، اما وضعیت سفارش هم‌زمان تغییر کرده است؛ تأیید این تلاش غیرفعال شد."
        )
        return

    await update.message.reply_text(
        f"Invoice سفارش {order_code} برای فروشنده ارسال شد."
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"تأیید دریافت ساتوشی فروشنده برای سفارش {order_code}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                " ساتوشی فروشنده دریافت شد",
                callback_data=f"seller_sats_received:{order_id}:{assignment_attempt}"
            )]
        ])
    )



async def confirm_buy_seller_sats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)

    if update.effective_user.id != ADMIN_ID:
        await query.answer(
            "این دکمه فقط برای پشتیبانی قابل استفاده است.",
            show_alert=True
        )
        return

    callback_match = re.fullmatch(
        r"buy_seller_sats_received:(\d+)(?::(\d+))?",
        query.data
    )
    if callback_match is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    await query.answer()
    order_id = int(callback_match.group(1))
    callback_attempt = (
        int(callback_match.group(2)) if callback_match.group(2) is not None else 0
    )
    confirmation_time_unix = int(time.time())
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    order = cursor.execute(
        """
        SELECT order_code, seller_id, buyer_id, order_side, status,
               manager_invoice, assignment_attempt, seller_invoice_attempt,
               seller_invoice_deadline_unix
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
        seller_id,
        buyer_id,
        order_side,
        status,
        manager_invoice,
        assignment_attempt,
        seller_invoice_attempt,
        seller_invoice_deadline_unix
    ) = order

    if order_side != "BUY":
        conn.close()
        await query.answer("این دکمه متعلق به سفارش خرید نیست.", show_alert=True)
        return

    if (
        status != "SELLER_ASSIGNED"
        or manager_invoice is None
        or assignment_attempt != callback_attempt
        or seller_invoice_attempt != callback_attempt
        or seller_invoice_deadline_unix is None
        or seller_invoice_deadline_unix <= confirmation_time_unix
    ):
        conn.close()
        await query.answer(
            "این سفارش دیگر در وضعیت SELLER_ASSIGNED نیست یا Invoice آن ثبت نشده است.",
            show_alert=True
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET status = ?, seller_payment_confirmed_at_unix = ?,
            seller_invoice_deadline_unix = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND buyer_id = ? AND manager_invoice IS NOT NULL
          AND assignment_attempt = ? AND seller_invoice_attempt = ?
          AND seller_invoice_deadline_unix IS NOT NULL
          AND seller_invoice_deadline_unix > ?
          AND seller_payment_confirmed_at_unix IS NULL
        """,
        (
            "SELLER_SATS_CONFIRMED",
            confirmation_time_unix,
            order_id,
            "BUY",
            "SELLER_ASSIGNED",
            seller_id,
            buyer_id,
            callback_attempt,
            callback_attempt,
            confirmation_time_unix
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این سفارش قبلاً تأیید شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return

    username_rows = cursor.execute(
        """
        SELECT telegram_id, username
        FROM users
        WHERE telegram_id IN (?, ?)
        """,
        (seller_id, buyer_id)
    ).fetchall()
    conn.commit()
    conn.close()

    usernames = {
        telegram_id: username
        for telegram_id, username in username_rows
        if username
    }
    seller_contact = (
        f"@{usernames[seller_id]}" if seller_id in usernames else str(seller_id)
    )
    buyer_contact = (
        f"@{usernames[buyer_id]}" if buyer_id in usernames else str(buyer_id)
    )

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "بیتکوین شما توسط بات دریافت و تأیید شد.\n\n"
                "اکنون میتوانید مرحله پرداخت فیات را با خریدار ادامه دهید.\n\n"
                f"آیدی خریدار: {buyer_contact}\n\n"
                "🔒 لطفاً فرآیند مبادله را با سرعت و دقت تا پایان پیش ببرید و پس از تکمیل "
                "موفقیت آمیز معامله، نسبت به حفظ حریم خصوصی خود و دیگران کوشا باشید."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                "بیتکوین فروشنده دریافت و آماده انتقال نهایی است.\n\n"
                "اکنون میتوانید مرحله پرداخت فیات را با فروشنده ادامه دهید.\n\n"
                f"آیدی فروشنده: {seller_contact}\n\n"
                "🔒 لطفاً فرآیند مبادله را با سرعت و دقت تا پایان پیش ببرید و پس از تکمیل "
                "موفقیت آمیز معامله، نسبت به حفظ حریم خصوصی خود و دیگران کوشا باشید."
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💸 فیات ارسال شد",
                    callback_data=f"buy_buyer_fiat_sent:{order_id}"
                )
            ]])
        )
    except TelegramError:
        pass



async def confirm_buy_buyer_fiat_sent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    if not await require_not_blacklisted(update, context):
        return

    callback_match = re.fullmatch(r"buy_buyer_fiat_sent:(\d+)", query.data)
    if callback_match is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    order_id = int(callback_match.group(1))
    buyer_id = update.effective_user.id
    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        """
        SELECT seller_id, buyer_id, order_side, status
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if order is None or order[1] != buyer_id:
        conn.close()
        await query.answer(
            "این دکمه فقط برای خریدار همین سفارش قابل استفاده است.",
            show_alert=True
        )
        return

    seller_id, _, order_side, status = order
    if order_side != "BUY" or status != "SELLER_SATS_CONFIRMED":
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا هنوز قابل تأیید نیست.",
            show_alert=True
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ? AND order_side = ? AND buyer_id = ? AND status = ?
        """,
        (
            "FIAT_SENT",
            order_id,
            "BUY",
            buyer_id,
            "SELLER_SATS_CONFIRMED"
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return

    conn.commit()
    conn.close()
    audit_event("fiat_sent", order_id=order_id, side="BUY", actor_id=update.effective_user.id)

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await context.bot.send_message(
        chat_id=buyer_id,
        text="ارسال فیات ثبت شد.\nمنتظر تأیید فروشنده بمانید."
    )

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "💸 خریدار اعلام کرد فیات را ارسال کرده است.\n\n"
                "لطفاً پس از دریافت وجه، دریافت فیات را تأیید کنید."
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    " فیات دریافت شد",
                    callback_data=f"buy_seller_fiat_received:{order_id}"
                )
            ]])
        )
    except TelegramError:
        pass



async def confirm_buy_seller_fiat_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    if not await require_not_blacklisted(update, context):
        return

    callback_match = re.fullmatch(
        r"buy_seller_fiat_received:(\d+)",
        query.data
    )
    if callback_match is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    order_id = int(callback_match.group(1))
    seller_id = update.effective_user.id
    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        """
        SELECT seller_id, buyer_id, amount_sats, order_side, status
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if order is None or order[0] != seller_id:
        conn.close()
        await query.answer(
            "این دکمه فقط برای فروشنده همین سفارش قابل استفاده است.",
            show_alert=True
        )
        return

    _, buyer_id, amount_sats, order_side, status = order
    if order_side != "BUY" or status != "FIAT_SENT":
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا هنوز قابل تأیید نیست.",
            show_alert=True
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ? AND order_side = ? AND seller_id = ? AND status = ?
        """,
        ("FIAT_RECEIVED", order_id, "BUY", seller_id, "FIAT_SENT")
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return

    conn.commit()
    conn.close()
    audit_event("fiat_received", order_id=order_id, side="BUY", actor_id=update.effective_user.id)

    await query.answer()
    await query.edit_message_text(" دریافت فیات ثبت شد.")

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=" فروشنده دریافت فیات را تأیید کرد."
        )
    except TelegramError:
        pass

    try:
        buyer_invoice_prompt = await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                f" لطفاً یک Invoice لایتنینگ به مبلغ {amount_sats:,} ساتوشی "
                "ایجاد و برای بات ارسال کنید."
            ),
        )
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE orders
            SET buyer_invoice_prompt_message_id = ?
            WHERE id = ? AND order_side = ? AND buyer_id = ? AND status = ?
            """,
            (
                buyer_invoice_prompt.message_id,
                order_id,
                "BUY",
                buyer_id,
                "FIAT_RECEIVED"
            )
        )
        conn.commit()
        conn.close()
    except TelegramError:
        pass



async def confirm_seller_sats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = SafeCallbackQuery(update.callback_query)

    if update.effective_user.id != ADMIN_ID:
        await query.answer("این دکمه فقط برای مدیر قابل استفاده است.", show_alert=True)
        return

    callback_match = re.fullmatch(
        r"seller_sats_received:(\d+)(?::(\d+))?",
        query.data
    )
    if callback_match is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    await query.answer()
    order_id = int(callback_match.group(1))
    callback_attempt = (
        int(callback_match.group(2)) if callback_match.group(2) is not None else 0
    )
    confirmation_time_unix = int(time.time())
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    order = cursor.execute(
        """
        SELECT seller_id, buyer_id, assignment_attempt,
               seller_invoice_attempt, seller_invoice_deadline_unix,
               manager_invoice
        FROM orders
        WHERE id = ? AND status = ?
        """,
        (order_id, "BUYER_ASSIGNED")
    ).fetchone()

    if order is None:
        conn.close()
        await query.answer(
            "این سفارش دیگر در وضعیت BUYER_ASSIGNED نیست.",
            show_alert=True
        )
        return

    (
        seller_id,
        buyer_id,
        assignment_attempt,
        seller_invoice_attempt,
        seller_invoice_deadline_unix,
        manager_invoice
    ) = order
    if (
        assignment_attempt != callback_attempt
        or seller_invoice_attempt != callback_attempt
        or seller_invoice_deadline_unix is None
        or seller_invoice_deadline_unix <= confirmation_time_unix
        or manager_invoice is None
    ):
        conn.close()
        await query.answer(
            "مهلت این تلاش پایان یافته یا این تأیید دیگر معتبر نیست.",
            show_alert=True
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET status = ?, seller_payment_confirmed_at_unix = ?,
            seller_invoice_deadline_unix = NULL
        WHERE id = ? AND status = ?
          AND assignment_attempt = ? AND seller_invoice_attempt = ?
          AND seller_invoice_deadline_unix IS NOT NULL
          AND seller_invoice_deadline_unix > ?
          AND seller_payment_confirmed_at_unix IS NULL
          AND manager_invoice IS NOT NULL
        """,
        (
            "SELLER_SATS_CONFIRMED",
            confirmation_time_unix,
            order_id,
            "BUYER_ASSIGNED",
            callback_attempt,
            callback_attempt,
            confirmation_time_unix
        )
    )

    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این سفارش دیگر در وضعیت BUYER_ASSIGNED نیست.",
            show_alert=True
        )
        return

    usernames = {
        telegram_id: username
        for telegram_id, username in cursor.execute(
            "SELECT telegram_id, username FROM users WHERE telegram_id IN (?, ?)",
            (seller_id, buyer_id)
        ).fetchall()
    }
    conn.commit()
    conn.close()

    buyer_username = usernames.get(buyer_id)
    seller_username = usernames.get(seller_id)
    buyer_contact = f"@{buyer_username}" if buyer_username else str(buyer_id)
    seller_contact = f"@{seller_username}" if seller_username else str(seller_id)

    await query.answer()
    await query.edit_message_text("دریافت ساتوشی فروشنده تأیید شد.")

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "بیتکوین شما توسط بات دریافت و تأیید شد.\n\n"
                "اکنون میتوانید مرحله پرداخت فیات را با خریدار ادامه دهید.\n\n"
                f"آیدی خریدار: {buyer_contact}\n\n"
                "🔒 لطفاً فرآیند مبادله را با سرعت و دقت تا پایان پیش ببرید و پس از تکمیل "
                "موفقیت آمیز معامله، نسبت به حفظ حریم خصوصی خود و دیگران کوشا باشید."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                "بیتکوین فروشنده دریافت و آماده انتقال نهایی است.\n\n"
                "اکنون میتوانید مرحله پرداخت فیات را با فروشنده ادامه دهید.\n\n"
                f"آیدی فروشنده: {seller_contact}\n\n"
                "🔒 لطفاً فرآیند مبادله را با سرعت و دقت تا پایان پیش ببرید و پس از تکمیل "
                "موفقیت آمیز معامله، نسبت به حفظ حریم خصوصی خود و دیگران کوشا باشید."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text="پس از ارسال فیات، وضعیت را تأیید کنید.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "💸 فیات ارسال شد",
                    callback_data=f"buyer_fiat_sent:{order_id}"
                )]
            ])
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text="پس از دریافت فیات، وضعیت را تأیید کنید.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    " فیات دریافت شد",
                    callback_data=f"seller_fiat_received:{order_id}"
                )]
            ])
        )
    except TelegramError:
        pass



async def confirm_buyer_fiat_sent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    if not await require_not_blacklisted(update, context):
        return

    order_id = int(query.data.split(":", 1)[1])
    buyer_id = update.effective_user.id

    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        "SELECT seller_id, buyer_id, status, order_side FROM orders WHERE id = ?",
        (order_id,)
    ).fetchone()

    if order is None or order[1] != buyer_id:
        conn.close()
        await query.answer(
            "این دکمه فقط برای خریدار همین سفارش قابل استفاده است.",
            show_alert=True
        )
        return

    seller_id, _, status, order_side = order
    if order_side != "SELL":
        conn.close()
        await query.answer("این دکمه مربوط به این نوع سفارش نیست.", show_alert=True)
        return
    if status != "SELLER_SATS_CONFIRMED":
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا هنوز قابل تأیید نیست.",
            show_alert=True
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ? AND buyer_id = ? AND status = ?
        """,
        ("FIAT_SENT", order_id, buyer_id, "SELLER_SATS_CONFIRMED")
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return


    conn.commit()
    conn.close()
    audit_event("fiat_sent", order_id=order_id, side="SELL", actor_id=update.effective_user.id)

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await context.bot.send_message(
        chat_id=buyer_id,
        text="ارسال فیات برای این سفارش ثبت شد."
    )

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "💸 خریدار اعلام کرد فیات را ارسال کرده است.\n"
                "لطفاً پس از دریافت وجه، «فیات دریافت شد» را تأیید کنید."
            )
        )
    except TelegramError:
        pass



async def confirm_seller_fiat_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    if not await require_not_blacklisted(update, context):
        return

    order_id = int(query.data.split(":", 1)[1])
    seller_id = update.effective_user.id

    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        "SELECT seller_id, buyer_id, amount_sats, status, order_side FROM orders WHERE id = ?",
        (order_id,)
    ).fetchone()

    if order is None or order[0] != seller_id:
        conn.close()
        await query.answer(
            "این دکمه فقط برای فروشنده همین سفارش قابل استفاده است.",
            show_alert=True
        )
        return

    _, buyer_id, amount_sats, status, order_side = order
    if order_side != "SELL":
        conn.close()
        await query.answer("این دکمه مربوط به این نوع سفارش نیست.", show_alert=True)
        return
    if status != "FIAT_SENT":
        conn.close()
        await query.answer(
            "تا پیش از تأیید ارسال فیات توسط خریدار، این مرحله قابل ثبت نیست.",
            show_alert=True
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ? AND seller_id = ? AND status = ?
        """,
        ("FIAT_RECEIVED", order_id, seller_id, "FIAT_SENT")
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این مرحله قبلاً ثبت شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return


    conn.commit()
    conn.close()
    audit_event("fiat_received", order_id=order_id, side="SELL", actor_id=update.effective_user.id)

    await query.answer()
    await query.edit_message_text("دریافت فیات برای این سفارش ثبت شد.")

    try:
        buyer_invoice_prompt = await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                " فروشنده دریافت فیات را تأیید کرد.\n\n"
                f"لطفاً یک Invoice لایتنینگ به مبلغ {amount_sats:,} ساتوشی "
                "ایجاد و برای بات ارسال کنید."
            ),
        )
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE orders
            SET buyer_invoice_prompt_message_id = ?
            WHERE id = ? AND buyer_id = ? AND status = ?
            """,
            (buyer_invoice_prompt.message_id, order_id, buyer_id, "FIAT_RECEIVED")
        )
        conn.commit()
        conn.close()
    except TelegramError:
        pass



async def handle_buy_buyer_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    buyer_id = update.effective_user.id
    message_text = update.message.text.strip()
    conn = db_connect()
    cursor = conn.cursor()

    order = None
    invoice = message_text
    reply = update.message.reply_to_message
    if reply is not None:
        reply_order = cursor.execute(
            """
            SELECT id, order_code, amount_sats, order_side
            FROM orders
            WHERE buyer_id = ? AND status = ? AND buyer_invoice IS NULL
              AND buyer_invoice_prompt_message_id = ?
            """,
            (buyer_id, "FIAT_RECEIVED", reply.message_id)
        ).fetchone()
        if reply_order is not None:
            if reply_order[3] != "BUY":
                conn.close()
                return False
            order = reply_order[:3]
        elif buyer_id == ADMIN_ID:
            manager_reply_order = cursor.execute(
                """
                SELECT o.id
                FROM orders o
                JOIN manager_transactions mt ON mt.order_id = o.id
                WHERE mt.manager_id = ? AND mt.status = ?
                  AND mt.manager_prompt_message_id = ?
                  AND o.manager_invoice IS NULL
                  AND mt.assignment_attempt = o.assignment_attempt
                  AND (
                      (o.order_side = ? AND o.status = ?)
                      OR (o.order_side = ? AND o.status = ?)
                  )
                """,
                (
                    ADMIN_ID,
                    "PENDING",
                    reply.message_id,
                    "BUY",
                    "SELLER_ASSIGNED",
                    "SELL",
                    "BUYER_ASSIGNED"
                )
            ).fetchone()
            if manager_reply_order is not None:
                conn.close()
                return False

    explicit_order = re.fullmatch(
        r"\s*(#ZI\d{6})\s+(.+?)\s*",
        message_text,
        re.DOTALL
    )
    if order is None and explicit_order is not None:
        order_code, invoice = explicit_order.groups()
        coded_order = cursor.execute(
            """
            SELECT id, order_code, amount_sats, order_side
            FROM orders
            WHERE order_code = ? AND buyer_id = ? AND status = ?
              AND buyer_invoice IS NULL
            """,
            (order_code, buyer_id, "FIAT_RECEIVED")
        ).fetchone()
        if coded_order is not None:
            if coded_order[3] != "BUY":
                conn.close()
                return False
            order = coded_order[:3]
        else:
            pending_buy_count = cursor.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE buyer_id = ? AND order_side = ? AND status = ?
                  AND buyer_invoice IS NULL
                """,
                (buyer_id, "BUY", "FIAT_RECEIVED")
            ).fetchone()[0]
            if pending_buy_count:
                conn.close()
                await update.message.reply_text(
                    "کد سفارش با هیچ سفارش خرید در انتظار Invoice مطابقت ندارد."
                )
                return True
            conn.close()
            return False

    if order is None:
        pending_orders = cursor.execute(
            """
            SELECT id, order_code, amount_sats
            FROM orders
            WHERE buyer_id = ? AND order_side = ? AND status = ?
              AND buyer_invoice IS NULL
            ORDER BY id
            """,
            (buyer_id, "BUY", "FIAT_RECEIVED")
        ).fetchall()
        if len(pending_orders) == 1:
            order = pending_orders[0]
        elif len(pending_orders) > 1:
            order_codes = "، ".join(row[1] for row in pending_orders)
            conn.close()
            await update.message.reply_text(
                "چند سفارش خرید در انتظار Invoice دارید. برای انتخاب دقیق، Invoice را "
                "در پاسخ به پیام درخواست همان سفارش بفرستید یا کد سفارش و Invoice را "
                f"در دو خط ارسال کنید.\n\nسفارش‌ها: {order_codes}"
            )
            return True
        else:
            conn.close()
            return False

    order_id, order_code, amount_sats = order
    invoice = invoice.strip()
    invoice_amount = buyer_invoice_amount_sats(invoice)
    if invoice_amount is None:
        conn.close()
        await update.message.reply_text(
            "Invoice نامعتبر است. لطفاً یک Invoice معتبر لایتنینگ ارسال کنید."
        )
        return True
    if invoice_amount != amount_sats:
        conn.close()
        await update.message.reply_text(
            f"مبلغ Invoice باید دقیقاً {amount_sats:,} ساتوشی باشد."
        )
        return True

    cursor.execute(
        """
        UPDATE orders
        SET buyer_invoice = ?, status = ?
        WHERE id = ? AND order_side = ? AND buyer_id = ? AND status = ?
          AND buyer_invoice IS NULL
        """,
        (
            invoice,
            "BUYER_INVOICE_RECEIVED",
            order_id,
            "BUY",
            buyer_id,
            "FIAT_RECEIVED"
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await update.message.reply_text(
            "Invoice این سفارش قبلاً ثبت شده یا سفارش دیگر در انتظار Invoice نیست."
        )
        return True

    conn.commit()
    conn.close()

    await auto_pay_buyer_invoice(context, order_id, buyer_id, invoice)
    return True



async def buyer_invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store an assigned buyer's invoice against one exact FIAT_RECEIVED order."""
    if (
        update.effective_chat is None
        or update.effective_chat.type != "private"
        or update.message is None
        or update.message.text is None
    ):
        return

    if not await require_not_blacklisted(update, context):
        return

    if await handle_buy_buyer_invoice(update, context):
        return

    buyer_id = update.effective_user.id
    message_text = update.message.text.strip()
    conn = db_connect()
    cursor = conn.cursor()

    order = None
    invoice = message_text
    reply = update.message.reply_to_message
    if reply is not None:
        order = cursor.execute(
            """
            SELECT id, order_code, amount_sats
            FROM orders
            WHERE buyer_id = ? AND status = ? AND buyer_invoice IS NULL
              AND buyer_invoice_prompt_message_id = ?
            """,
            (buyer_id, "FIAT_RECEIVED", reply.message_id)
        ).fetchone()

    if order is None:
        explicit_order = re.fullmatch(r"\s*(#ZI\d{6})\s+(.+?)\s*", message_text, re.DOTALL)
        if explicit_order is not None:
            order_code, invoice = explicit_order.groups()
            order = cursor.execute(
                """
                SELECT id, order_code, amount_sats
                FROM orders
                WHERE order_code = ? AND buyer_id = ? AND status = ?
                  AND buyer_invoice IS NULL
                """,
                (order_code, buyer_id, "FIAT_RECEIVED")
            ).fetchone()

    if order is None:
        pending_orders = cursor.execute(
            """
            SELECT id, order_code, amount_sats
            FROM orders
            WHERE buyer_id = ? AND status = ? AND buyer_invoice IS NULL
            ORDER BY id
            """,
            (buyer_id, "FIAT_RECEIVED")
        ).fetchall()
        if len(pending_orders) == 1:
            order = pending_orders[0]
        elif len(pending_orders) > 1:
            order_codes = "، ".join(row[1] for row in pending_orders)
            conn.close()
            await update.message.reply_text(
                "چند سفارش در انتظار Invoice دارید. برای انتخاب دقیق، Invoice را در پاسخ "
                "به پیام درخواست همان سفارش بفرستید یا کد سفارش و Invoice را در دو خط "
                f"ارسال کنید.\n\nسفارش‌ها: {order_codes}"
            )
            return
        else:
            conn.close()
            await other_messages(update, context)
            return

    order_id, order_code, amount_sats = order
    invoice = invoice.strip()
    invoice_amount = buyer_invoice_amount_sats(invoice)
    if invoice_amount is None:
        conn.close()
        await update.message.reply_text("Invoice نامعتبر است. لطفاً یک Invoice معتبر لایتنینگ ارسال کنید.")
        return
    if invoice_amount != amount_sats:
        conn.close()
        await update.message.reply_text(
            f"مبلغ Invoice باید دقیقاً {amount_sats:,} ساتوشی باشد."
        )
        return

    cursor.execute(
        """
        UPDATE orders
        SET buyer_invoice = ?, status = ?
        WHERE id = ? AND buyer_id = ? AND status = ? AND buyer_invoice IS NULL
        """,
        (
            invoice,
            "BUYER_INVOICE_RECEIVED",
            order_id,
            buyer_id,
            "FIAT_RECEIVED"
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await update.message.reply_text(
            "Invoice این سفارش قبلاً ثبت شده یا سفارش دیگر در انتظار Invoice نیست."
        )
        return

    conn.commit()
    conn.close()

    await auto_pay_buyer_invoice(context, order_id, buyer_id, invoice)



async def confirm_buy_buyer_sats_sent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = SafeCallbackQuery(update.callback_query)
    if update.effective_user.id != ADMIN_ID:
        await query.answer(
            "این دکمه فقط برای پشتیبانی قابل استفاده است.",
            show_alert=True
        )
        return

    callback_match = re.fullmatch(r"buy_buyer_sats_sent:(\d+)", query.data)
    if callback_match is None:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    await query.answer()
    order_id = int(callback_match.group(1))
    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        """
        SELECT order_code, seller_id, buyer_id, order_side, status,
               buyer_invoice, payout_status
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if (
        order is None
        or order[3] != "BUY"
        or order[4] != "BUYER_INVOICE_RECEIVED"
        or order[5] is None
        or order[6] != "UNKNOWN"
    ):
        conn.close()
        await query.answer(
            "این سفارش قبلاً تکمیل شده یا هنوز قابل تأیید نیست.",
            show_alert=True
        )
        return

    order_code, seller_id, buyer_id = order[:3]
    cursor.execute(
        """
        UPDATE orders
        SET status = ?, payout_status = ?
        WHERE id = ? AND order_side = ? AND status = ?
          AND buyer_invoice IS NOT NULL AND payout_status = ?
        """,
        (
            "COMPLETED",
            "MANUALLY_CONFIRMED",
            order_id,
            "BUY",
            "BUYER_INVOICE_RECEIVED",
            "UNKNOWN"
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این سفارش قبلاً تکمیل شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return

    conn.commit()
    conn.close()

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                " بیتکوین برای شما ارسال شد.\n"
                "معامله با موفقیت تکمیل شد."
            )
        )
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=" معامله با موفقیت تکمیل شد."
        )
    except TelegramError:
        pass

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"معامله {order_code} با شناسه {order_id} تکمیل شد."
    )



async def confirm_buyer_sats_sent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Complete one exact payout after the manager manually pays its invoice."""
    query = SafeCallbackQuery(update.callback_query)
    if update.effective_user.id != ADMIN_ID:
        await query.answer(
            "این دکمه فقط برای مدیر قابل استفاده است.",
            show_alert=True
        )
        return

    await query.answer()
    order_id = int(query.data.split(":", 1)[1])
    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        """
        SELECT order_code, seller_id, buyer_id, status, buyer_invoice, payout_status, order_side
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    if (
        order is None
        or order[3] != "BUYER_INVOICE_RECEIVED"
        or order[4] is None
        or order[5] != "UNKNOWN"
        or order[6] != "SELL"
    ):
        conn.close()
        await query.answer(
            "این سفارش قبلاً تکمیل شده یا در وضعیت BUYER_INVOICE_RECEIVED نیست.",
            show_alert=True
        )
        return

    order_code, seller_id, buyer_id = order[:3]
    cursor.execute(
        """
        UPDATE orders
        SET status = ?, payout_status = ?
        WHERE id = ? AND status = ? AND buyer_invoice IS NOT NULL
          AND payout_status = ?
        """,
        (
            "COMPLETED",
            "MANUALLY_CONFIRMED",
            order_id,
            "BUYER_INVOICE_RECEIVED",
            "UNKNOWN",
        )
    )
    if cursor.rowcount != 1:
        conn.close()
        await query.answer(
            "این سفارش قبلاً تکمیل شده یا دیگر قابل تأیید نیست.",
            show_alert=True
        )
        return

    conn.commit()
    conn.close()

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

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

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"معامله {order_code} تکمیل شد."
    )



async def other_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message
    if (
        message is None
        or update.effective_chat is None
        or update.effective_chat.type != "private"
    ):
        return

    await message.reply_text(
        "این بخش هنوز فعال نشده است."
    )

