"""User support conversation and administrator replies."""

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from settings import (
    ADMIN_ID,
    SUPPORT_NOTIFICATION_PREFIX,
    SUPPORT_ACK_PREFIX,
    SUPPORT_REPLY_PREFIX,
    SUPPORT_MESSAGE,
)
from callbacks import SafeCallbackQuery
from database import db_connect









async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🛟 پشتیبانی\n\n"
        "اگر درباره یک معامله مشکل اختلاف یا درخواست بازپرداخت دارید لطفا پیام خود را همراه با کد سفارش ارسال کنید.\n\n"
        "نمونه:\n"
        "#ZI000123\n"
        "شرح مشکل...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "❌ انصراف از پشتیبانی",
                callback_data="support_cancel"
            )]
        ])
    )

    return SUPPORT_MESSAGE



async def cancel_support(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    await query.edit_message_text("✅ درخواست پشتیبانی لغو شد.")

    return ConversationHandler.END



async def submit_support_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user
    message = update.message
    support_message = (message.text or message.caption or "").strip()
    submitted_at = message.date.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %z"
    )
    order_code_match = re.search(
        r"(?<!\w)#ZI\d{6,}(?!\d)",
        support_message,
        flags=re.IGNORECASE
    )
    order_code = order_code_match.group(0).upper() if order_code_match else None
    order = None

    if order_code:
        conn = db_connect()
        cursor = conn.cursor()
        order = cursor.execute(
            """
            SELECT id, status, buyer_id, seller_id, amount_sats, order_side
            FROM orders
            WHERE order_code = ? AND (buyer_id = ? OR seller_id = ?)
            """,
            (order_code, user.id, user.id)
        ).fetchone()
        conn.close()

    username_or_id = f"@{user.username}" if user.username else str(user.id)
    support_details = [
        SUPPORT_NOTIFICATION_PREFIX,
        "",
        f"کاربر: {username_or_id}",
        f"شناسه تلگرام: {user.id}",
        f"کد سفارش: {order_code or 'بدون کد سفارش'}",
        f"زمان ثبت: {submitted_at}",
    ]

    if order:
        order_id, status, buyer_id, seller_id, amount_sats, order_side = order
        support_details.extend([
            "",
            f"شناسه سفارش: {order_id}",
            f"وضعیت سفارش: {status}",
            f"نوع سفارش: {order_side}",
            f"مبلغ: {amount_sats:,} ساتوشی",
            f"خریدار: {buyer_id}",
            f"فروشنده: {seller_id}",
        ])

        if status == "CANCELED_REFUND_PENDING":
            support_details.extend([
                "",
                " این معامله در انتظار بررسی و بازپرداخت است."
            ])

    support_details.extend([
        "",
        "پیام کاربر:",
        support_message or "(تصویر بدون توضیح)"
    ])
    admin_message = "\n".join(support_details)

    if len(admin_message) <= 4096:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message)
    else:
        for start_index in range(0, len(admin_message), 4096):
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message[start_index:start_index + 4096]
            )

    if message.photo:
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

    context.user_data["support_order_code"] = order_code

    confirmation_lines = [
        SUPPORT_ACK_PREFIX,
        "پشتیبانی پس از بررسی از طریق همین بات با شما در ارتباط خواهد بود.",
    ]
    if order_code:
        confirmation_lines.append(f"کد سفارش: {order_code}")
    confirmation_lines.extend([
        "",
        "برای ارسال Invoice یا اطلاعات تکمیلی، پیام جدید را در پاسخ (Reply) به همین پیام بفرستید."
    ])
    await update.message.reply_text("\n".join(confirmation_lines))

    return ConversationHandler.END



async def submit_support_followup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user
    message = update.message
    support_message = (message.text or message.caption or "").strip()
    if not support_message and not message.photo:
        await message.reply_text(
            "لطفاً Invoice، توضیحات تکمیلی یا تصویر ارسال کنید."
        )
        return

    order_code_match = re.search(
        r"(?<!\w)#ZI\d{6,}(?!\d)",
        support_message,
        flags=re.IGNORECASE
    )
    order_code = order_code_match.group(0).upper() if order_code_match else None

    if not order_code:
        replied_text = (message.reply_to_message.text or "") if message.reply_to_message else ""
        replied_order_match = re.search(
            r"(?<!\w)#ZI\d{6,}(?!\d)",
            replied_text,
            flags=re.IGNORECASE
        )
        if replied_order_match:
            order_code = replied_order_match.group(0).upper()

    if not order_code:
        order_code = context.user_data.get("support_order_code")

    submitted_at = message.date.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    username_or_id = f"@{user.username}" if user.username else str(user.id)
    support_details = [
        SUPPORT_NOTIFICATION_PREFIX,
        "",
        "نوع پیام: اطلاعات تکمیلی",
        f"کاربر: {username_or_id}",
        f"شناسه تلگرام: {user.id}",
        f"کد سفارش: {order_code or 'بدون کد سفارش'}",
        f"زمان ثبت: {submitted_at}",
        "",
        "پیام تکمیلی کاربر:",
        support_message or "(تصویر بدون توضیح)",
    ]
    admin_message = "\n".join(support_details)

    if len(admin_message) <= 4096:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message)
    else:
        for start_index in range(0, len(admin_message), 4096):
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message[start_index:start_index + 4096]
            )

    if message.photo:
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

    if order_code:
        context.user_data["support_order_code"] = order_code

    await message.reply_text(
        "✅ اطلاعات تکمیلی برای پشتیبانی ارسال شد.\n"
        "برای ارسال مورد دیگری، دوباره به یکی از پیام‌های پشتیبانی Reply کنید."
    )



async def reply_to_support_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    replied_message = update.message.reply_to_message

    if (
        replied_message.from_user.id != context.bot.id
        or not replied_message.text.startswith(SUPPORT_NOTIFICATION_PREFIX)
    ):
        return

    user_id_match = re.search(
        r"^شناسه تلگرام: (\d+)$",
        replied_message.text,
        flags=re.MULTILINE
    )
    if not user_id_match:
        return

    support_user_id = int(user_id_match.group(1))
    await context.bot.send_message(
        chat_id=support_user_id,
        text=(
            f"{SUPPORT_REPLY_PREFIX}\n\n"
            f"{update.message.text}"
        )
    )
    await update.message.reply_text(" پاسخ برای کاربر ارسال شد.")




class SupportNotificationReplyFilter(filters.MessageFilter):

    def filter(self, message):
        replied_message = message.reply_to_message

        return bool(
            message.chat_id == ADMIN_ID
            and replied_message
            and replied_message.from_user
            and replied_message.from_user.is_bot
            and replied_message.text
            and replied_message.text.startswith(SUPPORT_NOTIFICATION_PREFIX)
            and re.search(
                r"^شناسه تلگرام: \d+$",
                replied_message.text,
                flags=re.MULTILINE
            )
        )



class SupportUserFollowupReplyFilter(filters.MessageFilter):

    def filter(self, message):
        replied_message = message.reply_to_message
        replied_text = (replied_message.text or "") if replied_message else ""

        return bool(
            message.chat_id != ADMIN_ID
            and replied_message
            and replied_message.from_user
            and replied_message.from_user.is_bot
            and replied_text
            and (
                replied_text.startswith(SUPPORT_ACK_PREFIX)
                or replied_text.startswith(SUPPORT_REPLY_PREFIX)
                or "پیام شما برای پشتیبانی ارسال شد." in replied_text
            )
        )

