"""Administrator commands, moderation, and order intervention."""

import re
import sqlite3
from datetime import datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from database import db_connect
from observability import audit_event, logger
from recovery import notify_escrow_cancellation, notify_related_users_of_cancellation
from settings import ACTIVE_ORDER_STATUSES, ADMIN_ID, CHANNEL_ID, ESCROWED_ORDER_STATUSES
from users import set_maintenance_mode


async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "فرمت صحیح: /ban_user <telegram_id> <reason>"
        )
        return

    try:
        telegram_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "شناسه تلگرام باید یک عدد صحیح باشد."
        )
        return

    reason = " ".join(context.args[1:]).strip()
    if not reason:
        await update.message.reply_text(
            "فرمت صحیح: /ban_user <telegram_id> <reason>"
        )
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db_connect()
    conn.execute(
        """
        INSERT INTO blacklist (telegram_id, reason, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            reason = excluded.reason,
            created_at = excluded.created_at
        """,
        (telegram_id, reason, created_at)
    )
    conn.commit()
    conn.close()

    audit_event("user_banned", actor_id=ADMIN_ID, target_id=telegram_id)
    notification_status = "اطلاع‌رسانی به کاربر انجام شد."
    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=" دسترسی شما به BIFp2p Bot توسط پشتیبانی محدود شده است."
        )
    except TelegramError:
        notification_status = "ارسال پیام به کاربر ممکن نبود."

    await update.message.reply_text(
        f"کاربر {telegram_id} در فهرست سیاه ثبت شد.\n"
        f"دلیل: {reason}\n"
        f"{notification_status}"
    )



async def unban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "فرمت صحیح: /unban_user <telegram_id>"
        )
        return

    try:
        telegram_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "شناسه تلگرام باید یک عدد صحیح باشد."
        )
        return

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM blacklist WHERE telegram_id = ?",
        (telegram_id,)
    )
    removed = cursor.rowcount == 1
    conn.commit()
    conn.close()

    if removed:
        audit_event("user_unbanned", actor_id=ADMIN_ID, target_id=telegram_id)

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"کاربر {telegram_id} از فهرست سیاه حذف شد."
        )

        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "✅ محدودیت حساب شما در BIFp2p برداشته شد.\n\n"
                    "اکنون می‌توانید دوباره از خدمات بات استفاده کنید."
                )
            )
        except Exception:
            logger.warning(
                "Could not notify unbanned user telegram_id=%s",
                telegram_id
            )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"کاربر {telegram_id} در فهرست سیاه نبود."
        )



async def maintenance_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این دستور فقط برای مدیر قابل استفاده است.")
        return

    set_maintenance_mode(True)
    audit_event("maintenance_changed", actor_id=ADMIN_ID, enabled=1)
    await update.message.reply_text(
        "\U0001F6E0 \u062d\u0627\u0644\u062a \u062a\u0639\u0645\u06cc\u0631 \u0648 \u0646\u06af\u0647\u062f\u0627\u0631\u06cc \u0641\u0639\u0627\u0644 \u0634\u062f. \u062b\u0628\u062a \u0633\u0641\u0627\u0631\u0634 \u062c\u062f\u06cc\u062f \u0645\u062a\u0648\u0642\u0641 \u0627\u0633\u062a."
    )



async def maintenance_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این دستور فقط برای مدیر قابل استفاده است.")
        return

    set_maintenance_mode(False)
    audit_event("maintenance_changed", actor_id=ADMIN_ID, enabled=0)
    await update.message.reply_text(
        "\u2705 \u062d\u0627\u0644\u062a \u062a\u0639\u0645\u06cc\u0631 \u0648 \u0646\u06af\u0647\u062f\u0627\u0631\u06cc \u063a\u06cc\u0631\u0641\u0639\u0627\u0644 \u0634\u062f. \u062b\u0628\u062a \u0633\u0641\u0627\u0631\u0634 \u062c\u062f\u06cc\u062f \u062f\u0648\u0628\u0627\u0631\u0647 \u0627\u0645\u06a9\u0627\u0646\u200c\u067e\u0630\u06cc\u0631 \u0627\u0633\u062a."
    )



async def score_adjust_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این دستور فقط برای مدیر قابل استفاده است.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "فرمت صحیح: /score_adjust <user_id یا @username> <amount> <reason>"
        )
        return

    target = context.args[0].strip()
    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("مقدار امتیاز باید عددی باشد.")
        return

    if re.fullmatch(r"\d+", target):
        user_id = int(target)
    else:
        username = target.lstrip("@").strip()
        if not username:
            await update.message.reply_text("شناسه کاربر یا Username نامعتبر است.")
            return
        conn = db_connect()
        row = conn.execute(
            "SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)",
            (username,)
        ).fetchone()
        conn.close()
        if row is None:
            await update.message.reply_text(
                "کاربری با این Username در دیتابیس بات پیدا نشد. "
                "کاربر باید حداقل یک‌بار بات را Start کرده باشد."
            )
            return
        user_id = int(row[0])

    if amount == 0:
        await update.message.reply_text("مقدار تعدیل نمی‌تواند صفر باشد.")
        return

    reason = " ".join(context.args[2:]).strip()
    adjustment_code = (
        f"MANUAL-{user_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    )
    conn = db_connect()
    conn.execute(
        """
        INSERT INTO reputation_adjustments
        (user_id, order_code, points, reason, applied_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            adjustment_code,
            amount,
            reason,
            ADMIN_ID,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    conn.commit()
    conn.close()
    audit_event("reputation_adjusted", actor_id=ADMIN_ID, target_id=user_id, points=amount)

    try:
        if amount > 0:
            user_message = (
                f"🎁 {amount:,} امتیاز به اعتبار شما اضافه شد.\n"
                f"دلیل: {reason}"
            )
        else:
            user_message = (
                f"⚠️ {abs(amount):,} امتیاز از اعتبار شما کسر شد.\n"
                f"دلیل: {reason}"
            )
        await context.bot.send_message(chat_id=user_id, text=user_message)
        user_notified = True
    except TelegramError:
        user_notified = False
        logger.exception(
            "Failed to notify user about reputation adjustment user_id=%s",
            user_id
        )

    await update.message.reply_text(
        f"✅ تعدیل {amount:+,} امتیاز برای کاربر {user_id} ثبت شد.\n"
        f"دلیل: {reason}\n"
        f"اطلاع‌رسانی به کاربر: {'انجام شد' if user_notified else 'ناموفق'}"
    )



def find_order_for_admin(cursor, identifier):

    normalized = identifier.strip().upper()
    if re.fullmatch(r"#ZI\d{6,}", normalized):
        condition = "order_code = ?"
        value = normalized
    elif normalized.isdigit():
        condition = "id = ?"
        value = int(normalized)
    else:
        return None

    return cursor.execute(
        f"""
        SELECT id, order_code, seller_id, buyer_id, amount_sats,
               status, channel_message_id
        FROM orders
        WHERE {condition}
        """,
        (value,)
    ).fetchone()



async def admin_cancel_order_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این دستور فقط برای مدیر قابل استفاده است.")
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "فرمت صحیح: /admin_cancel <order_code_or_id>"
        )
        return

    conn = db_connect()
    cursor = conn.cursor()
    order = find_order_for_admin(cursor, context.args[0])
    if order is None:
        conn.close()
        await update.message.reply_text("سفارش پیدا نشد.")
        return

    (
        order_id,
        order_code,
        seller_id,
        buyer_id,
        amount_sats,
        status,
        channel_message_id
    ) = order
    payout_row = cursor.execute(
        "SELECT payout_status FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    payout_status = payout_row[0] if payout_row else None
    if payout_status in ("PROCESSING", "UNKNOWN"):
        conn.close()
        await update.message.reply_text(
            "⚠️ وضعیت پرداخت Coinos در حال بررسی است. تا تعیین نتیجه، این معامله قابل لغو نیست."
        )
        return
    if status not in ACTIVE_ORDER_STATUSES:
        conn.close()
        await update.message.reply_text(
            f"سفارش {order_code} در وضعیت نهایی {status} است و قابل لغو نیست."
        )
        return

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
        await update.message.reply_text("وضعیت سفارش تغییر کرده است؛ لغو انجام نشد.")
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
            context, order_id, order_code, amount_sats, seller_id, buyer_id
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
    await update.message.reply_text(
        f"✅ سفارش {order_code} توسط مدیر لغو شد.\n"
        f"وضعیت: {target_status}"
    )



async def admin_remove_order_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این دستور فقط برای مدیر قابل استفاده است.")
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "فرمت صحیح: /admin_remove <order_code_or_id>"
        )
        return

    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    order = find_order_for_admin(cursor, context.args[0])
    if order is None:
        conn.close()
        await update.message.reply_text("سفارش پیدا نشد.")
        return

    order_id, order_code, seller_id, buyer_id, amount_sats, status, channel_message_id = order
    payout_row = cursor.execute(
        "SELECT payout_status FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    payout_status = payout_row[0] if payout_row else None
    if payout_status in ("PROCESSING", "UNKNOWN"):
        conn.rollback()
        conn.close()
        await update.message.reply_text(
            "⚠️ وضعیت پرداخت Coinos در حال بررسی است. تا تعیین نتیجه، این معامله قابل حذف نیست."
        )
        return
    if status not in ACTIVE_ORDER_STATUSES:
        conn.close()
        await update.message.reply_text(
            f"سفارش {order_code} در وضعیت نهایی {status} است و قابل حذف نیست."
        )
        return

    target_status = (
        "CANCELED_REFUND_PENDING"
        if status in ESCROWED_ORDER_STATUSES
        else "CANCELED"
    )
    cursor.execute(
        """
        UPDATE orders
        SET status = ?, channel_message_id = NULL
        WHERE id = ? AND status = ?
        """,
        (target_status, order_id, status)
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        await update.message.reply_text("وضعیت سفارش تغییر کرده است؛ حذف انجام نشد.")
        return
    conn.commit()
    conn.close()

    if channel_message_id is not None:
        try:
            await context.bot.delete_message(
                chat_id=CHANNEL_ID, message_id=channel_message_id
            )
        except TelegramError:
            logger.warning(
                "Admin remove could not delete channel message order_id=%s message_id=%s",
                order_id, channel_message_id
            )

    if target_status == "CANCELED_REFUND_PENDING":
        await notify_escrow_cancellation(
            context, order_id, order_code, amount_sats, seller_id, buyer_id
        )
    else:
        await notify_related_users_of_cancellation(
            context, order_code, seller_id, buyer_id,
            canceled_by_admin=True, exclude_user_id=ADMIN_ID
        )

    logger.warning(
        "Admin removed order order_id=%s order_code=%s previous_status=%s new_status=%s",
        order_id, order_code, status, target_status
    )
    await update.message.reply_text(
        f"✅ سفارش {order_code} توسط مدیر حذف شد.\nوضعیت: {target_status}"
    )



async def penalty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این دستور فقط برای پشتیبانی قابل استفاده است.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("فرمت صحیح: /penalty <order_code> <user_id>")
        return

    order_code = context.args[0].upper()
    if not re.fullmatch(r"#ZI\d{6,}", order_code):
        await update.message.reply_text("کد سفارش نامعتبر است.")
        return

    try:
        user_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("شناسه کاربر باید عددی باشد.")
        return

    conn = db_connect()
    cursor = conn.cursor()
    order = cursor.execute(
        """
        SELECT seller_id, buyer_id, status
        FROM orders
        WHERE order_code = ?
        """,
        (order_code,)
    ).fetchone()

    if order is None:
        conn.close()
        await update.message.reply_text("سفارشی با این کد پیدا نشد.")
        return

    seller_id, buyer_id, status = order
    if status != "CANCELED_REFUND_PENDING":
        conn.close()
        await update.message.reply_text(
            "جریمه فقط برای سفارش در وضعیت CANCELED_REFUND_PENDING قابل ثبت است."
        )
        return

    if user_id not in (seller_id, buyer_id):
        conn.close()
        await update.message.reply_text("این کاربر یکی از طرفین سفارش نیست.")
        return

    try:
        cursor.execute(
            """
            INSERT INTO reputation_adjustments
            (user_id, order_code, points, reason, applied_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                order_code,
                -1000,
                "UNJUSTIFIED_ESCROW_CANCELLATION",
                ADMIN_ID,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        await update.message.reply_text(
            "این جریمه قبلاً برای همین کاربر و سفارش ثبت شده است."
        )
        return

    conn.close()
    audit_event("penalty_applied", actor_id=ADMIN_ID, target_id=user_id, order_code=order_code, points=-1000)
    await update.message.reply_text(
        f"جریمه 1,000- امتیازی برای کاربر {user_id} در سفارش {order_code} ثبت شد."
    )

