"""User eligibility, maintenance mode, reputation, and seller fees."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from telegram.error import TelegramError

from callbacks import SafeCallbackQuery
from database import db_connect
from royalty import royalty_gate_message, royalty_new_trade_block_reason
from settings import ADMIN_ID, OFFICIAL_CHANNEL_ID, OFFICIAL_GROUP_ID


def save_user(user):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO users
        (telegram_id, username, created_at)
        VALUES (?, ?, ?)
        """,
        (
            user.id,
            user.username,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    conn.commit()
    conn.close()



def historical_reputation_points(user_id):

    conn = db_connect()
    points = historical_reputation_points_with_cursor(conn.cursor(), user_id)
    conn.close()
    return points


def historical_reputation_points_with_cursor(cursor, user_id):
    """Read database-derived reputation using the caller's transaction."""
    completed_count, completed_volume = cursor.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(amount_sats), 0)
        FROM orders
        WHERE status = ? AND (seller_id = ? OR buyer_id = ?)
        """,
        ("COMPLETED", user_id, user_id)
    ).fetchone()
    manual_adjustments = cursor.execute(
        """
        SELECT COALESCE(SUM(points), 0)
        FROM reputation_adjustments
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()[0]
    transaction_points = completed_count * 100
    volume_points = completed_volume
    return transaction_points + volume_points + manual_adjustments



def maintenance_mode_enabled(cursor=None):

    conn = None if cursor is not None else db_connect()
    db_cursor = cursor if cursor is not None else conn.cursor()
    row = db_cursor.execute(
        "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
        ("maintenance_mode",)
    ).fetchone()
    if conn is not None:
        conn.close()
    return row is not None and row[0] == "1"



def set_maintenance_mode(enabled):

    conn = db_connect()
    conn.execute(
        """
        INSERT INTO bot_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value
        """,
        ("maintenance_mode", "1" if enabled else "0")
    )
    conn.commit()
    conn.close()




async def is_official_channel_member(context, user_id):
    try:
        membership = await context.bot.get_chat_member(
            chat_id=OFFICIAL_CHANNEL_ID,
            user_id=user_id
        )
    except TelegramError:
        return False

    if membership.status in ("creator", "administrator", "member"):
        return True
    if membership.status == "restricted" and getattr(membership, "is_member", False):
        return True
    return False


async def is_official_group_member(context, user_id):

    try:
        membership = await context.bot.get_chat_member(
            chat_id=OFFICIAL_GROUP_ID,
            user_id=user_id
        )
    except TelegramError:
        return False

    if membership.status in ("creator", "administrator", "member"):
        return True
    if membership.status == "restricted" and getattr(membership, "is_member", False):
        return True
    return False



def is_user_blacklisted(user_id):

    if user_id == ADMIN_ID:
        return False

    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM blacklist WHERE telegram_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row is not None



async def send_eligibility_failure(update, context, message):

    query = SafeCallbackQuery(update.callback_query) if update.callback_query else None
    if query is None:
        await update.effective_message.reply_text(message)
        return

    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=message
        )
        await query.answer()
    except TelegramError:
        await query.answer(message, show_alert=True)



async def require_order_eligibility(update, context):

    user = update.effective_user
    if royalty_new_trade_block_reason() is not None:
        await send_eligibility_failure(
            update,
            context,
            royalty_gate_message()
        )
        return False

    if user.id == ADMIN_ID:
        return True

    if not user.username:
        await send_eligibility_failure(
            update,
            context,
            " برای استفاده از خدمات بات داشتن نام کاربری (Username) در تلگرام الزامی است.\n\n"
            "لطفا یک Username برای حساب تلگرام خود تنظیم کنید و سپس دوباره اقدام کنید."
        )
        return False


    if is_user_blacklisted(user.id):
        await send_eligibility_failure(
            update,
            context,
            " دسترسی شما به بات محدود شده است."
        )
        return False

    return True



async def require_not_blacklisted(update, context):

    user = update.effective_user
    if user.id == ADMIN_ID or not is_user_blacklisted(user.id):
        return True

    await send_eligibility_failure(
        update,
        context,
        " دسترسی شما به بات محدود شده است."
    )
    return False




async def channel_membership_points(context, user_id):
    if await is_official_channel_member(context, user_id):
        return 1000
    return 0


async def user_reputation_score(context, user_id):
    return historical_reputation_points(user_id) + await channel_membership_points(context, user_id)



def seller_fee_percent_from_score(score):
    """Return seller fee percent: 1.0%, minus 0.1% per 100k points, floor 0.5%."""
    safe_score = max(int(score or 0), 0)
    discount_steps = min(safe_score // 100_000, 5)
    return Decimal("1.0") - (Decimal(discount_steps) / Decimal("10"))



def normalize_seller_fee_percent(fee_percent):
    """Normalize persisted fee values and safely fall back to the legacy 1% rate."""
    try:
        value = Decimal(str(fee_percent))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("1.0")
    if value < Decimal("0.5") or value > Decimal("1.0"):
        return Decimal("1.0")
    return value



def seller_fee_amount_sats(amount_sats, fee_percent):
    """Calculate the fee in whole sats, preserving the old floor/truncation behavior."""
    value = normalize_seller_fee_percent(fee_percent)
    return int(Decimal(int(amount_sats)) * value / Decimal("100"))



def format_seller_fee_percent(fee_percent):
    value = normalize_seller_fee_percent(fee_percent)
    return f"{format(value.normalize(), 'f')}%"

