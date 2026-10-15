"""Publish channel orders and construct their deep links."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from pricing import price_rule_text
from settings import CHANNEL_ID
from telegram_ui import order_deep_link_url
from users import user_reputation_score


async def publish_sell_order(
    context,
    order_id,
    creator_id,
    amount,
    price_type,
    price,
    description,
    open_generation,
    market_offset_percent=None
):

    score = await user_reputation_score(context, creator_id)

    channel_message = await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=(
            "🔵 سفارش فروش بیتکوین\n\n"
            f"مقدار: {amount} ساتوشی\n"
            f"قیمت: {price:,} تومان\n"
            f"توضیحات: {description}\n"
            f"امتیاز کاربر: {score}\n"
            "وضعیت: در انتظار خریدار"
        ) if price_type == "fixed" else (
            "🔵 سفارش فروش بیتکوین\n\n"
            f"مقدار: {amount} ساتوشی\n"
            f"قیمت: {price_rule_text(price_type, price, market_offset_percent)}\n"
            f"توضیحات: {description}\n"
            f"امتیاز کاربر: {score}\n"
            "وضعیت: در انتظار خریدار"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔵 خرید بیتکوین",
                url=order_deep_link_url("buy", order_id, open_generation)
            )]
        ])
    )

    return channel_message.message_id



async def publish_buy_order(
    context,
    order_id,
    creator_id,
    amount,
    price_type,
    price,
    description,
    open_generation,
    market_offset_percent=None
):

    score = await user_reputation_score(context, creator_id)
    price_text = price_rule_text(price_type, price, market_offset_percent)

    channel_message = await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=(
            "🟠 سفارش خرید بیتکوین\n\n"
            f"مقدار: {amount} ساتوشی\n"
            f"قیمت: {price_text}\n"
            f"توضیحات: {description}\n"
            f"امتیاز کاربر: {score}\n"
            "وضعیت: در انتظار فروشنده"
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🟠 فروش بیتکوین",
                url=order_deep_link_url("sell", order_id, open_generation)
            )
        ]])
    )

    return channel_message.message_id

