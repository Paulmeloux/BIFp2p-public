"""SELL and BUY order creation conversations."""

import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from callbacks import SafeCallbackQuery
from database import db_connect
from observability import audit_event
from order_publishing import publish_buy_order, publish_sell_order
from pricing import market_price_method_keyboard, price_rule_text
from royalty import royalty_gate_message, royalty_new_trade_block_reason
from settings import (
    AMOUNT, BUY_AMOUNT, BUY_CONFIRM, BUY_DESCRIPTION, BUY_PRICE, BUY_PRICE_METHOD,
    BUY_PRICE_PERCENT, CONFIRM, DESCRIPTION, MAX_MARKET_OFFSET_PERCENT,
    MAX_ORDER_SATS, MIN_ORDER_SATS, PRICE, PRICE_METHOD, PRICE_PERCENT,
)
from telegram_ui import confirm_keyboard
from users import (
    format_seller_fee_percent, maintenance_mode_enabled, require_order_eligibility,
    seller_fee_percent_from_score, user_reputation_score,
)


async def sell_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    if maintenance_mode_enabled():
        await update.message.reply_text(
            "🛠 بات در حال تعمیر و نگهداری است و ثبت سفارش جدید موقتاً امکان‌پذیر نیست."
        )
        return ConversationHandler.END

    if not await require_order_eligibility(update, context):
        return ConversationHandler.END

    await update.message.reply_text(
        "🟠 فروش بیتکوین\n\n"
        "مقدار ساتوشی مورد فروش را وارد کنید.\n\n"
        "حداقل سفارش: 10,000 sats\n"
        "حداکثر سفارش: 100,000 sats"
    )

    return AMOUNT



async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
    except (TypeError, ValueError):
        await update.message.reply_text("⚠️ فقط عدد وارد کنید.")
        return AMOUNT

    if amount < MIN_ORDER_SATS or amount > MAX_ORDER_SATS:
        await update.message.reply_text("⚠️ مقدار باید بین 10,000 تا 100,000 sats باشد.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text(
        "روش تعیین قیمت را انتخاب کنید:",
        reply_markup=market_price_method_keyboard("sell")
    )
    return PRICE_METHOD



async def choose_sell_price_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    method = query.data.split(":", 1)[1]
    if method == "market":
        context.user_data["price_type"] = "market"
        context.user_data["market_offset_percent"] = 0.0
        context.user_data.pop("price", None)
        await query.edit_message_text("📊 قیمت سفارش برابر قیمت بازار در لحظه پذیرش خواهد بود.")
        await context.bot.send_message(chat_id=update.effective_user.id, text="اگر توضیحی برای خریدار دارید وارد کنید.\nدر غیر این صورت بنویسید: ندارد")
        return DESCRIPTION
    if method == "percent":
        context.user_data["price_type"] = "market"
        context.user_data.pop("price", None)
        await query.edit_message_text("درصد بالاتر از قیمت بازار را وارد کنید.\nعدد باید بیشتر از ۰ و حداکثر ۵ باشد.\nمثال: 3 یا 4.5")
        return PRICE_PERCENT
    if method == "fixed":
        context.user_data["price_type"] = "fixed"
        context.user_data["market_offset_percent"] = None
        await query.edit_message_text(f"مبلغ پیشنهادی خود را برای {context.user_data['amount']:,} ساتوشی به تومان وارد کنید.")
        return PRICE
    return PRICE_METHOD



async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.replace(",", ""))
    except (TypeError, ValueError):
        await update.message.reply_text("⚠️ مبلغ را فقط به صورت عدد وارد کنید.")
        return PRICE
    if price <= 0:
        await update.message.reply_text("⚠️ مبلغ باید یک عدد صحیحِ مثبت باشد.")
        return PRICE
    context.user_data["price"] = price
    await update.message.reply_text("اگر توضیحی برای خریدار دارید وارد کنید.\nدر غیر این صورت بنویسید: ندارد")
    return DESCRIPTION



async def get_sell_price_percent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        percent = Decimal(update.message.text.strip().replace("٫", "."))
    except (InvalidOperation, AttributeError):
        await update.message.reply_text("⚠️ درصد را فقط به صورت عدد وارد کنید. مثال: 3 یا 4.5")
        return PRICE_PERCENT
    if percent <= 0 or percent > MAX_MARKET_OFFSET_PERCENT:
        await update.message.reply_text("⚠️ درصد باید بیشتر از ۰ و حداکثر ۵ باشد.")
        return PRICE_PERCENT
    context.user_data["market_offset_percent"] = float(percent)
    await update.message.reply_text("اگر توضیحی برای خریدار دارید وارد کنید.\nدر غیر این صورت بنویسید: ندارد")
    return DESCRIPTION



async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text
    if description.strip() == "":
        description = "ندارد"
    context.user_data["description"] = description
    amount = context.user_data["amount"]
    if context.user_data["price_type"] == "fixed":
        price = f"{context.user_data['price']:,} تومان (مبلغ کل معامله)"
    else:
        price = price_rule_text(
            context.user_data["price_type"],
            context.user_data.get("price"),
            context.user_data.get("market_offset_percent")
        )
    score = await user_reputation_score(context, update.effective_user.id)
    current_fee_percent = seller_fee_percent_from_score(score)
    await update.message.reply_text(
        f"""\n🟠 خلاصه سفارش فروش\n\nمقدار:\n{amount} sats\n\nقیمت:\n{price}\n\nکارمزد فعلی شما:\n{format_seller_fee_percent(current_fee_percent)}\n\nنرخ نهایی هنگام پذیرش سفارش بر اساس امتیاز شما قفل میشود.\n\nتوضیحات:\n{description}\n\nآیا ثبت شود؟\n""",
        reply_markup=confirm_keyboard()
    )
    return CONFIRM



async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)


    if query.data == "cancel_order":

        await query.answer()

        await query.edit_message_text(
            "❌ سفارش لغو شد."
        )

        context.user_data.clear()

        return ConversationHandler.END



    if query.data == "confirm_order":

        await query.answer()

        if maintenance_mode_enabled():
            await query.answer(
                "🛠 ثبت سفارش جدید در حالت تعمیر و نگهداری ممنوع است.",
                show_alert=True
            )
            return CONFIRM

        if not await require_order_eligibility(update, context):
            return CONFIRM

        await query.answer()

        user = update.effective_user


        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        if maintenance_mode_enabled(cursor):
            conn.rollback()
            conn.close()
            await query.answer(
                "🛠 ثبت سفارش جدید در حالت تعمیر و نگهداری ممنوع است.",
                show_alert=True
            )
            return CONFIRM
        if royalty_new_trade_block_reason(cursor) is not None:
            conn.rollback()
            conn.close()
            await query.answer(royalty_gate_message(), show_alert=True)
            return CONFIRM


        cursor.execute(
            """
            INSERT INTO orders
            (
            order_code,
            seller_id,
            amount_sats,
            price_type,
            price_toman,
            market_offset_percent,
            description,
            fee_percent,
            open_generation,
            status,
            created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                user.id,
                context.user_data["amount"],
                context.user_data["price_type"],
                context.user_data.get("price"),
                context.user_data.get("market_offset_percent"),
                context.user_data["description"],
                1,
                1,
                "OPEN",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )


        order_id = cursor.lastrowid
        order_code = f"#ZI{order_id:06d}"

        cursor.execute(
            "UPDATE orders SET order_code = ? WHERE id = ?",
            (order_code, order_id)
        )


        conn.commit()
        conn.close()
        audit_event("order_created", order_id=order_id, order_code=order_code, side="SELL", actor_id=user.id, amount_sats=context.user_data["amount"])


        price = price_rule_text(
            context.user_data["price_type"],
            context.user_data.get("price"),
            context.user_data.get("market_offset_percent")
        )


        channel_message_id = await publish_sell_order(
            context,
            order_id,
            user.id,
            context.user_data["amount"],
            context.user_data["price_type"],
            context.user_data.get("price"),
            context.user_data["description"],
            1,
            context.user_data.get("market_offset_percent")
        )

        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE orders
            SET channel_message_id = ?, open_since_unix = ?
            WHERE id = ? AND status = ? AND open_generation = ?
            """,
            (channel_message_id, int(time.time()), order_id, "OPEN", 1)
        )
        conn.commit()
        conn.close()

        await query.edit_message_text(
            f"""
✅ سفارش شما ثبت شد.

کد سفارش:
{order_code}

مقدار:
{context.user_data['amount']} sats

قیمت:
{price}

کارمزد:
بر اساس امتیاز فروشنده در زمان پذیرش سفارش (1% تا 0.5%)

توضیحات:
{context.user_data['description']}

وضعیت:
در انتظار خریدار

پس از پیدا شدن خریدار،
از طریق همین بات اطلاع‌رسانی خواهد شد.
"""
        )


        context.user_data.clear()


        return ConversationHandler.END



async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    if maintenance_mode_enabled():
        await update.message.reply_text(
            "🛠 بات در حال تعمیر و نگهداری است و ثبت سفارش جدید موقتاً امکان‌پذیر نیست."
        )
        return ConversationHandler.END

    if not await require_order_eligibility(update, context):
        return ConversationHandler.END

    await update.message.reply_text(
        "🔵 خرید بیتکوین\n\n"
        "مقدار ساتوشی مورد خرید را وارد کنید.\n\n"
        "حداقل سفارش: 10,000 sats\n"
        "حداکثر سفارش: 100,000 sats"
    )

    return BUY_AMOUNT



async def get_buy_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
    except (TypeError, ValueError):
        await update.message.reply_text("⚠️ فقط عدد وارد کنید.")
        return BUY_AMOUNT
    if amount < MIN_ORDER_SATS or amount > MAX_ORDER_SATS:
        await update.message.reply_text("⚠️ مقدار باید بین 10,000 تا 100,000 sats باشد.")
        return BUY_AMOUNT
    context.user_data["buy_amount"] = amount
    await update.message.reply_text(
        "روش تعیین قیمت را انتخاب کنید:",
        reply_markup=market_price_method_keyboard("buy")
    )
    return BUY_PRICE_METHOD



async def choose_buy_price_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = SafeCallbackQuery(update.callback_query)
    await query.answer()
    method = query.data.split(":", 1)[1]
    if method == "market":
        context.user_data["buy_price_type"] = "market"
        context.user_data["buy_market_offset_percent"] = 0.0
        context.user_data.pop("buy_price", None)
        await query.edit_message_text("📊 قیمت سفارش برابر قیمت بازار در لحظه پذیرش خواهد بود.")
        await context.bot.send_message(chat_id=update.effective_user.id, text="اگر توضیحی برای فروشنده دارید وارد کنید.\nدر غیر این صورت بنویسید: ندارد")
        return BUY_DESCRIPTION
    if method == "percent":
        context.user_data["buy_price_type"] = "market"
        context.user_data.pop("buy_price", None)
        await query.edit_message_text("درصد بالاتر از قیمت بازار را وارد کنید.\nعدد باید بیشتر از ۰ و حداکثر ۵ باشد.\nمثال: 3 یا 4.5")
        return BUY_PRICE_PERCENT
    if method == "fixed":
        context.user_data["buy_price_type"] = "fixed"
        context.user_data["buy_market_offset_percent"] = None
        await query.edit_message_text(f"مبلغ پیشنهادی خود را برای {context.user_data['buy_amount']:,} ساتوشی به تومان وارد کنید.")
        return BUY_PRICE
    return BUY_PRICE_METHOD



async def get_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.replace(",", ""))
    except (TypeError, ValueError):
        await update.message.reply_text("⚠️ مبلغ را فقط به صورت عدد وارد کنید.")
        return BUY_PRICE
    if price <= 0:
        await update.message.reply_text("⚠️ مبلغ باید یک عدد صحیحِ مثبت باشد.")
        return BUY_PRICE
    context.user_data["buy_price"] = price
    await update.message.reply_text("اگر توضیحی برای فروشنده دارید وارد کنید.\nدر غیر این صورت بنویسید: ندارد")
    return BUY_DESCRIPTION



async def get_buy_price_percent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        percent = Decimal(update.message.text.strip().replace("٫", "."))
    except (InvalidOperation, AttributeError):
        await update.message.reply_text("⚠️ درصد را فقط به صورت عدد وارد کنید. مثال: 3 یا 4.5")
        return BUY_PRICE_PERCENT
    if percent <= 0 or percent > MAX_MARKET_OFFSET_PERCENT:
        await update.message.reply_text("⚠️ درصد باید بیشتر از ۰ و حداکثر ۵ باشد.")
        return BUY_PRICE_PERCENT
    context.user_data["buy_market_offset_percent"] = float(percent)
    await update.message.reply_text("اگر توضیحی برای فروشنده دارید وارد کنید.\nدر غیر این صورت بنویسید: ندارد")
    return BUY_DESCRIPTION



async def get_buy_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text
    if description.strip() == "":
        description = "ندارد"
    context.user_data["buy_description"] = description
    if context.user_data["buy_price_type"] == "fixed":
        price = f"{context.user_data['buy_price']:,} تومان (مبلغ کل معامله)"
    else:
        price = price_rule_text(
            context.user_data["buy_price_type"],
            context.user_data.get("buy_price"),
            context.user_data.get("buy_market_offset_percent")
        )
    await update.message.reply_text(
        f"""\n🔵 خلاصه سفارش خرید\n\nمقدار:\n{context.user_data['buy_amount']} sats\n\nقیمت:\n{price}\n\nتوضیحات:\n{description}\n\nآیا ثبت شود؟\n""",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ثبت سفارش", callback_data="confirm_buy_order"), InlineKeyboardButton("❌ لغو", callback_data="cancel_buy_order")]])
    )
    return BUY_CONFIRM



async def confirm_buy_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = SafeCallbackQuery(update.callback_query)

    if query.data == "cancel_buy_order":
        await query.answer()
        await query.edit_message_text("❌ سفارش لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data != "confirm_buy_order":
        return BUY_CONFIRM

    await query.answer()

    if maintenance_mode_enabled():
        await query.answer(
            "🛠 ثبت سفارش جدید در حالت تعمیر و نگهداری ممنوع است.",
            show_alert=True
        )
        return BUY_CONFIRM

    if not await require_order_eligibility(update, context):
        return BUY_CONFIRM

    await query.answer()

    user = update.effective_user
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    if maintenance_mode_enabled(cursor):
        conn.rollback()
        conn.close()
        await query.answer(
            "🛠 ثبت سفارش جدید در حالت تعمیر و نگهداری ممنوع است.",
            show_alert=True
        )
        return BUY_CONFIRM
    if royalty_new_trade_block_reason(cursor) is not None:
        conn.rollback()
        conn.close()
        await query.answer(royalty_gate_message(), show_alert=True)
        return BUY_CONFIRM
    cursor.execute(
        """
        INSERT INTO orders
        (
        order_code,
        buyer_id,
        amount_sats,
        price_type,
        price_toman,
        market_offset_percent,
        description,
        fee_percent,
        order_side,
        open_generation,
        status,
        created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            None,
            user.id,
            context.user_data["buy_amount"],
            context.user_data["buy_price_type"],
            context.user_data.get("buy_price"),
            context.user_data.get("buy_market_offset_percent"),
            context.user_data["buy_description"],
            1,
            "BUY",
            1,
            "OPEN",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    order_id = cursor.lastrowid
    order_code = f"#ZI{order_id:06d}"
    cursor.execute(
        "UPDATE orders SET order_code = ? WHERE id = ?",
        (order_code, order_id)
    )
    conn.commit()
    conn.close()
    audit_event("order_created", order_id=order_id, order_code=order_code, side="BUY", actor_id=user.id, amount_sats=context.user_data["buy_amount"])

    channel_message_id = await publish_buy_order(
        context,
        order_id,
        user.id,
        context.user_data["buy_amount"],
        context.user_data["buy_price_type"],
        context.user_data.get("buy_price"),
        context.user_data["buy_description"],
        1,
        context.user_data.get("buy_market_offset_percent")
    )

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET channel_message_id = ?, open_since_unix = ?
        WHERE id = ? AND status = ? AND open_generation = ?
        """,
        (channel_message_id, int(time.time()), order_id, "OPEN", 1)
    )
    conn.commit()
    conn.close()

    await query.edit_message_text(
        f"""
✅ سفارش خرید شما ثبت شد.

کد سفارش:
{order_code}

مقدار:
{context.user_data['buy_amount']} sats

قیمت:
{(f"{context.user_data['buy_price']:,} تومان (مبلغ کل معامله)" if context.user_data['buy_price_type'] == 'fixed' else price_rule_text(context.user_data['buy_price_type'], context.user_data.get('buy_price'), context.user_data.get('buy_market_offset_percent')))}

توضیحات:
{context.user_data['buy_description']}

وضعیت:
در انتظار فروشنده
"""
    )

    context.user_data.clear()
    return ConversationHandler.END

