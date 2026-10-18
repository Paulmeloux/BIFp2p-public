import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
import warnings
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from telegram import BotCommand, BotCommandScopeAllPrivateChats, Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, TelegramError
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from settings import (
    validate_runtime_config,
    TOKEN,
    COINOS_TOKEN,
    COINOS_API_BASE,
    CHANNEL_ID,
    OFFICIAL_CHANNEL_ID,
    OFFICIAL_CHANNEL_URL,
    OFFICIAL_GROUP_ID,
    BOT_USERNAME,
    MIN_ORDER_SATS,
    MAX_ORDER_SATS,
    OPEN_ORDER_LIFETIME_SECONDS,
    ORDER_TIMEOUT_POLL_SECONDS,
    MARKET_PRICE_API_URL,
    MARKET_PRICE_TIMEOUT_SECONDS,
    MAX_MARKET_OFFSET_PERCENT,
    REPOST_CLAIM_STALE_SECONDS,
    OPEN_ORDER_EXPIRATION_MESSAGE,
    SUPPORT_BUTTON_TEXT,
    GUIDE_BUTTON_TEXT,
    GUIDE_TEXT,
    AMOUNT,
    PRICE_METHOD,
    PRICE,
    PRICE_PERCENT,
    DESCRIPTION,
    CONFIRM,
    BUY_AMOUNT,
    BUY_PRICE_METHOD,
    BUY_PRICE,
    BUY_PRICE_PERCENT,
    BUY_DESCRIPTION,
    BUY_CONFIRM,
    ESCROWED_ORDER_STATUSES,
    ACTIVE_ORDER_STATUSES,
)
from escrow_invoices import get_or_create_coinos_invoice, reserve_coinos_invoice_id
from observability import audit_event, logger
from coinos_api import CoinosNotFound, coinos_request
from lightning import buyer_invoice_amount_sats
from telegram_ui import (
    project_about_text,
    main_menu,
    confirm_keyboard,
    buy_callback_data,
    confirm_channel_order_callback_data,
    decline_channel_order_callback_data,
    sell_buy_order_callback_data,
    confirm_buy_channel_order_callback_data,
    decline_buy_channel_order_callback_data,
    order_deep_link_payload,
    order_deep_link_url,
    parse_order_deep_link,
    parse_open_order_callback,
)
from users import (
    save_user,
    historical_reputation_points,
    maintenance_mode_enabled,
    set_maintenance_mode,
    is_official_group_member,
    is_user_blacklisted,
    send_eligibility_failure,
    require_order_eligibility,
    require_not_blacklisted,
    user_reputation_score,
    seller_fee_percent_from_score,
    normalize_seller_fee_percent,
    seller_fee_amount_sats,
    format_seller_fee_percent,
)
from trade_flow import (
    handle_buy_manager_invoice,
    manager_invoice_handler,
    confirm_buy_seller_sats,
    confirm_buy_buyer_fiat_sent,
    confirm_buy_seller_fiat_received,
    confirm_seller_sats,
    confirm_buyer_fiat_sent,
    confirm_seller_fiat_received,
    handle_buy_buyer_invoice,
    buyer_invoice_handler,
    confirm_buy_buyer_sats_sent,
    confirm_buyer_sats_sent,
    other_messages,
)
from order_acceptance import (
    acceptance_price_snapshot,
    acceptance_price_text,
    send_deep_link_order_prompt,
    start,
    select_sell_order,
    decline_channel_order,
    confirm_sell_order,
    select_buy_order_as_seller,
    decline_buy_channel_order,
    confirm_buy_order_as_seller,
)
from order_creation import (
    sell_start,
    get_amount,
    choose_sell_price_method,
    get_price,
    get_sell_price_percent,
    get_description,
    confirm_order,
    buy_start,
    get_buy_amount,
    choose_buy_price_method,
    get_buy_price,
    get_buy_price_percent,
    get_buy_description,
    confirm_buy_order,
)
from cancellation import cancel_order, cancel_selected_order, cancel_all_orders
from admin import (
    ban_user_command,
    unban_user_command,
    maintenance_on_command,
    maintenance_off_command,
    score_adjust_command,
    find_order_for_admin,
    admin_cancel_order_command,
    admin_remove_order_command,
    penalty_command,
    royalty_status_command,
)
from recovery import (
    claim_due_seller_payment_timeouts,
    publish_reopening_order,
    recover_reopening_orders,
    notify_seller_payment_timeouts,
    expire_due_open_orders,
    process_expired_order_side_effects,
    run_order_timeout_recovery_once,
    order_timeout_worker,
    notify_escrow_cancellation,
    notify_related_users_of_cancellation,
)
from order_publishing import publish_sell_order, publish_buy_order
from settlement import (
    send_coinos_escrow_invoice,
    notify_coinos_seller_payment_confirmed,
    confirm_coinos_seller_payment,
    process_coinos_escrow_invoices,
    claim_buyer_payout,
    mark_buyer_payout_unknown,
    _normalized_lightning_invoice,
    _find_confirmed_coinos_payout,
    complete_reconciled_buyer_payout,
    reconcile_unknown_buyer_payouts,
    notify_completed_trade,
    auto_pay_buyer_invoice,
    mark_seller_invoice_sent,
    reset_seller_invoice_delivery_claim,
    reset_unsent_manager_invoice,
)
from callbacks import SafeCallbackQuery, answer_callback_query_safely
from database import (
    DB_PATH,
    SELLER_INVOICE_PAYMENT_SECONDS,
    db_connect,
    init_database,
    verify_database_integrity,
)
from royalty import recover_royalty_state_on_startup, royalty_status_snapshot
from royalty_worker import royalty_worker, stop_royalty_worker
from messaging import help_guide, publish_periodic_market_price, market_price_worker, stop_market_price_worker
from support import (
    SupportNotificationReplyFilter,
    SupportUserFollowupReplyFilter,
    ADMIN_ID,
    SUPPORT_ACK_PREFIX,
    SUPPORT_MESSAGE,
    SUPPORT_NOTIFICATION_PREFIX,
    SUPPORT_REPLY_PREFIX,
    cancel_support,
    reply_to_support_user,
    submit_support_followup,
    submit_support_message,
    support_start,
)
from pricing import (
    market_price_method_keyboard,
    fetch_market_price_toman,
    current_order_price_details,
    fetch_market_price_usd,
    dynamic_order_price_toman,
    fiat_total_toman,
    format_percent,
    price_rule_text,
)


PRIVATE_CHAT_ONLY = filters.ChatType.PRIVATE


def conversation_handler_without_per_message_warning(*args, **kwargs):
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                r"If 'per_message=False', 'CallbackQueryHandler' will not be "
                r"tracked for every message\."
            ),
            category=PTBUserWarning
        )
        return ConversationHandler(*args, **kwargs)


from pricing import (
    dynamic_order_price_toman,
    fiat_total_toman,
    format_percent,
    price_rule_text,
)


async def about_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the original project's identity independently of the operator."""
    if update.effective_message is not None:
        await update.effective_message.reply_text(project_about_text())


async def set_bot_commands(app: Application) -> None:

    await app.bot.delete_my_commands()
    await app.bot.set_my_commands([
        BotCommand("start", "شروع"),
        BotCommand("help", "راهنما"),
        BotCommand("cancel", "لغو یک سفارش"),
        BotCommand("cancel_all_order", "لغو همه سفارشات"),
        BotCommand("support", "پشتیبانی"),
        BotCommand("about", "About BIFp2p and original project")
    ], scope=BotCommandScopeAllPrivateChats())
    app.bot_data["royalty_worker"] = asyncio.create_task(royalty_worker(app))
    await run_order_timeout_recovery_once(app)
    app.bot_data["order_timeout_worker"] = asyncio.create_task(
        order_timeout_worker(app)
    )
    app.bot_data["market_price_worker"] = asyncio.create_task(
        market_price_worker(app)
    )


async def stop_order_timeout_worker(app: Application) -> None:
    worker = app.bot_data.get("order_timeout_worker")
    if worker is None:
        return
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


async def stop_background_workers(app):
    await stop_royalty_worker(app)
    await stop_order_timeout_worker(app)
    await stop_market_price_worker(app)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    effective_user = getattr(update, "effective_user", None)
    effective_chat = getattr(update, "effective_chat", None)
    update_id = getattr(update, "update_id", None)
    logger.error(
        "Unhandled bot exception update_id=%s user_id=%s chat_id=%s error_type=%s",
        update_id,
        getattr(effective_user, "id", None),
        getattr(effective_chat, "id", None),
        type(error).__name__ if error is not None else "Unknown",
        exc_info=(type(error), error, error.__traceback__) if error is not None else None,
    )


def main():

    validate_runtime_config()
    init_database()
    verify_database_integrity()
    recovered_royalties = recover_royalty_state_on_startup()
    royalty_health = royalty_status_snapshot()
    if recovered_royalties:
        logger.warning(
            "Marked %s in-flight royalty obligation(s) UNKNOWN after restart",
            recovered_royalties,
        )
    if royalty_health["mode"] == "ACCOUNTING_TEST":
        logger.warning(
            "Royalty mode ACCOUNTING_TEST records obligations only and is not "
            "commercially compliant production operation"
        )
    if not royalty_health["production_compliant"]:
        logger.warning(
            "Royalty production enforcement is not operational; new-trade gate=%s",
            royalty_health["gate_reason"],
        )


    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(set_bot_commands)
        .post_shutdown(stop_background_workers)
        .build()
    )


    support_conversation = conversation_handler_without_per_message_warning(
        entry_points=[
            CommandHandler(
                "support",
                support_start,
                filters=PRIVATE_CHAT_ONLY
            ),
            MessageHandler(
                PRIVATE_CHAT_ONLY
                & filters.Regex(f"^{re.escape(SUPPORT_BUTTON_TEXT)}$"),
                support_start
            ),
        ],
        states={
            SUPPORT_MESSAGE: [
                CallbackQueryHandler(
                    cancel_support,
                    pattern=r"^support_cancel$"
                ),
                MessageHandler(
                    PRIVATE_CHAT_ONLY & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
                    submit_support_message
                )
            ]
        },
        fallbacks=[
            CommandHandler("support", support_start, filters=PRIVATE_CHAT_ONLY),
            CommandHandler("start", start, filters=PRIVATE_CHAT_ONLY),
            CommandHandler("help", help_guide, filters=PRIVATE_CHAT_ONLY),
            CommandHandler("cancel", cancel_order, filters=PRIVATE_CHAT_ONLY),
            CommandHandler(
                "cancel_all_order",
                cancel_all_orders,
                filters=PRIVATE_CHAT_ONLY
            ),
        ],
        allow_reentry=True
    )


    sell_conversation = conversation_handler_without_per_message_warning(

        entry_points=[
            MessageHandler(
                PRIVATE_CHAT_ONLY
                & filters.Regex("^🟠 فروش بیتکوین$"),
                sell_start
            )
        ],


        states={

            AMOUNT: [
                MessageHandler(
                    PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND,
                    get_amount
                )
            ],
            PRICE_METHOD: [
                CallbackQueryHandler(choose_sell_price_method, pattern=r"^sell_price_method:(market|percent|fixed)$")
            ],
            PRICE: [
                MessageHandler(PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND, get_price)
            ],
            PRICE_PERCENT: [
                MessageHandler(PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND, get_sell_price_percent)
            ],


            DESCRIPTION: [
                MessageHandler(
                    PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND,
                    get_description
                )
            ],


            CONFIRM: [
                CallbackQueryHandler(
                    confirm_order,
                    pattern=r"^(confirm_order|cancel_order)$"
                )
            ]

        },


        fallbacks=[
            CommandHandler("start", start, filters=PRIVATE_CHAT_ONLY),
            CommandHandler("cancel", cancel_order, filters=PRIVATE_CHAT_ONLY),
            CommandHandler(
                "cancel_all_order",
                cancel_all_orders,
                filters=PRIVATE_CHAT_ONLY
            ),
        ]

    )


    buy_conversation = conversation_handler_without_per_message_warning(

        entry_points=[
            MessageHandler(
                PRIVATE_CHAT_ONLY & filters.Regex("^🔵 خرید بیتکوین$"),
                buy_start
            )
        ],

        states={
            BUY_AMOUNT: [
                MessageHandler(
                    PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND,
                    get_buy_amount
                )
            ],
            BUY_PRICE_METHOD: [
                CallbackQueryHandler(choose_buy_price_method, pattern=r"^buy_price_method:(market|percent|fixed)$")
            ],
            BUY_PRICE: [
                MessageHandler(PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND, get_buy_price)
            ],
            BUY_PRICE_PERCENT: [
                MessageHandler(PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND, get_buy_price_percent)
            ],
            BUY_DESCRIPTION: [
                MessageHandler(
                    PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND,
                    get_buy_description
                )
            ],
            BUY_CONFIRM: [
                CallbackQueryHandler(
                    confirm_buy_order,
                    pattern=r"^(confirm_buy_order|cancel_buy_order)$"
                )
            ]
        },

        fallbacks=[
            CommandHandler("start", start, filters=PRIVATE_CHAT_ONLY),
            CommandHandler("cancel", cancel_order, filters=PRIVATE_CHAT_ONLY),
            CommandHandler(
                "cancel_all_order",
                cancel_all_orders,
                filters=PRIVATE_CHAT_ONLY
            ),
        ]
    )


    app.add_handler(
        MessageHandler(
            PRIVATE_CHAT_ONLY
            & filters.User(user_id=ADMIN_ID)
            & filters.TEXT
            & ~filters.COMMAND
            & SupportNotificationReplyFilter(),
            reply_to_support_user
        )
    )


    app.add_handler(
        MessageHandler(
            PRIVATE_CHAT_ONLY & filters.Regex(
                f"^{re.escape(GUIDE_BUTTON_TEXT)}$"
            ),
            help_guide
        )
    )




    app.add_handler(
        MessageHandler(
            PRIVATE_CHAT_ONLY
            & (filters.TEXT | filters.PHOTO)
            & ~filters.COMMAND
            & SupportUserFollowupReplyFilter(),
            submit_support_followup
        )
    )


    app.add_handler(support_conversation)


    app.add_handler(sell_conversation)


    app.add_handler(
        buy_conversation
    )


    app.add_handler(
        CommandHandler("start", start, filters=PRIVATE_CHAT_ONLY)
    )


    app.add_handler(
        CommandHandler("help", help_guide, filters=PRIVATE_CHAT_ONLY)
    )

    app.add_handler(
        CommandHandler("about", about_project, filters=PRIVATE_CHAT_ONLY)
    )


    app.add_handler(
        CommandHandler("penalty", penalty_command, filters=PRIVATE_CHAT_ONLY)
    )

    app.add_handler(
        CommandHandler("royalty_status", royalty_status_command, filters=PRIVATE_CHAT_ONLY)
    )


    app.add_handler(
        CommandHandler(
            "maintenance_on",
            maintenance_on_command,
            filters=PRIVATE_CHAT_ONLY
        )
    )


    app.add_handler(
        CommandHandler(
            "maintenance_off",
            maintenance_off_command,
            filters=PRIVATE_CHAT_ONLY
        )
    )


    app.add_handler(
        CommandHandler(
            "score_adjust",
            score_adjust_command,
            filters=PRIVATE_CHAT_ONLY
        )
    )


    app.add_handler(
        CommandHandler(
            "admin_cancel",
            admin_cancel_order_command,
            filters=PRIVATE_CHAT_ONLY
        )
    )


    app.add_handler(
        CommandHandler(
            "admin_remove",
            admin_remove_order_command,
            filters=PRIVATE_CHAT_ONLY
        )
    )


    app.add_handler(
        CommandHandler("ban_user", ban_user_command, filters=PRIVATE_CHAT_ONLY)
    )


    app.add_handler(
        CommandHandler("unban_user", unban_user_command, filters=PRIVATE_CHAT_ONLY)
    )


    app.add_handler(
        CommandHandler("cancel", cancel_order, filters=PRIVATE_CHAT_ONLY)
    )


    app.add_handler(
        CommandHandler(
            "cancel_all_order",
            cancel_all_orders,
            filters=PRIVATE_CHAT_ONLY
        )
    )




    app.add_handler(
        CallbackQueryHandler(cancel_selected_order, pattern=r"^cancel_order:\d+$")
    )


    app.add_handler(
        CallbackQueryHandler(
            select_sell_order,
            pattern=r"^buy_order:\d+(?::\d+)?:[0-9a-f]{16}$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_sell_order,
            pattern=r"^confirm_channel_order:\d+(?::\d+)?:[0-9a-f]{16}$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            decline_channel_order,
            pattern=r"^decline_channel_order:\d+(?::\d+)?:[0-9a-f]{16}$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            select_buy_order_as_seller,
            pattern=r"^sell_buy_order:\d+(?::\d+)?:[0-9a-f]{16}$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buy_order_as_seller,
            pattern=r"^confirm_buy_channel_order:\d+(?::\d+)?:[0-9a-f]{16}$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            decline_buy_channel_order,
            pattern=r"^decline_buy_channel_order:\d+(?::\d+)?:[0-9a-f]{16}$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buy_seller_sats,
            pattern=r"^buy_seller_sats_received:\d+(?::\d+)?$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buy_buyer_fiat_sent,
            pattern=r"^buy_buyer_fiat_sent:\d+$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buy_seller_fiat_received,
            pattern=r"^buy_seller_fiat_received:\d+$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buy_buyer_sats_sent,
            pattern=r"^buy_buyer_sats_sent:\d+$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_seller_sats,
            pattern=r"^seller_sats_received:\d+(?::\d+)?$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buyer_fiat_sent,
            pattern=r"^buyer_fiat_sent:\d+$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_seller_fiat_received,
            pattern=r"^seller_fiat_received:\d+$"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            confirm_buyer_sats_sent,
            pattern=r"^buyer_sats_sent:\d+$"
        )
    )


    app.add_handler(
        MessageHandler(
            PRIVATE_CHAT_ONLY
            & filters.User(user_id=ADMIN_ID)
            & filters.TEXT
            & ~filters.COMMAND,
            manager_invoice_handler
        )
    )


    app.add_handler(
        MessageHandler(
            PRIVATE_CHAT_ONLY & filters.TEXT & ~filters.COMMAND,
            buyer_invoice_handler
        )
    )


    app.add_error_handler(global_error_handler)

    logger.info("BIFp2p bot starting; database=%s", DB_PATH)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

