"""Guide replies and periodic market price posts."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes

from observability import audit_event, logger
from pricing import fetch_market_price_toman, fetch_market_price_usd
from settings import CHANNEL_ID, GUIDE_TEXT


async def help_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(GUIDE_TEXT, parse_mode="HTML")



async def publish_periodic_market_price(app: Application) -> None:
    try:
        price_usd, price_toman = await asyncio.gather(
            fetch_market_price_usd(),
            fetch_market_price_toman(),
        )

        text = (
            "<b>₿ قیمت بیتکوین</b>\n\n"
            f"{price_usd:,} دلار\n"
            f"{price_toman:,} تومان\n\n"
            "قیمت را فقط برای اطلاع دنبال می‌کنیم و ارزش بیتکوین را با نوسان‌های روزانه نمی‌سنجیم."
        )

        await app.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode="HTML"
        )

        audit_event(
            "periodic_market_price_published",
            price_usd=price_usd,
            price_toman=price_toman,
        )

    except Exception:
        logger.exception("Periodic market price publication failed")



async def market_price_worker(app: Application) -> None:
    tehran = ZoneInfo("Asia/Tehran")
    last_slot = None

    while True:
        try:
            now = datetime.now(tehran)

            if now.hour == 0 and now.minute == 0:
                slot = now.strftime("%Y-%m-%d-%H")

                if slot != last_slot:
                    await publish_periodic_market_price(app)
                    last_slot = slot

            await asyncio.sleep(20)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Market price worker failed")
            await asyncio.sleep(30)



async def stop_market_price_worker(app: Application) -> None:
    worker = app.bot_data.get("market_price_worker")
    if worker is None:
        return

    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass

