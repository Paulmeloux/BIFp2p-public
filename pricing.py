"""Pure pricing calculations shared by order flows."""

import asyncio
import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from settings import MARKET_PRICE_API_URL, MARKET_PRICE_TIMEOUT_SECONDS


def format_percent(value):
    decimal_value = Decimal(str(value))
    text = format(decimal_value.normalize(), "f")
    return text



def price_rule_text(price_type, price_toman=None, market_offset_percent=None):
    if price_type == "fixed":
        return f"{int(price_toman):,} تومان"
    offset = Decimal(str(market_offset_percent or 0))
    if offset == 0:
        return "قیمت بازار در لحظه پذیرش"
    return f"{format_percent(offset)}٪ بالاتر از قیمت بازار در لحظه پذیرش"



def dynamic_order_price_toman(market_price_toman, offset_percent):
    multiplier = Decimal("1") + (Decimal(str(offset_percent or 0)) / Decimal("100"))
    price_toman = int(Decimal(market_price_toman) * multiplier)
    return (price_toman // 1000) * 1000



def fiat_total_toman(amount_sats, btc_price_toman):
    amount_toman = int(
        Decimal(amount_sats)
        * Decimal(btc_price_toman)
        / Decimal("100000000")
    )
    return (amount_toman // 1000) * 1000




def market_price_method_keyboard(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 قیمت بازار", callback_data=f"{prefix}_price_method:market")],
        [InlineKeyboardButton("📈 درصد بالاتر از بازار", callback_data=f"{prefix}_price_method:percent")],
        [InlineKeyboardButton("✏️ قیمت دلخواه", callback_data=f"{prefix}_price_method:fixed")],
    ])



def _fetch_market_price_toman_sync():
    separator = "&" if "?" in MARKET_PRICE_API_URL else "?"
    url = (
        f"{MARKET_PRICE_API_URL}{separator}base=BTC&quote=IRT"
        "&source=binance&source_usdt=nobitex&weighted=true"
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "BIFp2p/1.0"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=MARKET_PRICE_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Market price service unavailable") from exc

    try:
        price_rial = Decimal(str(payload["price"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError("Market price service returned an invalid price") from exc
    if price_rial <= 0:
        raise RuntimeError("Market price service returned a non-positive price")
    # Despite quote=IRT, this API documents `price` in rial. Convert to toman.
    price_toman = int(price_rial / Decimal("10"))
    return (price_toman // 1000) * 1000



async def fetch_market_price_toman():
    return await asyncio.to_thread(_fetch_market_price_toman_sync)



async def current_order_price_details(price_type, price_toman, market_offset_percent, amount_sats):
    if price_type == "fixed":
        total_toman = int(price_toman)
        return None, None, total_toman
    market_price = await fetch_market_price_toman()
    final_price = dynamic_order_price_toman(market_price, market_offset_percent)
    return market_price, final_price, fiat_total_toman(amount_sats, final_price)



def _fetch_market_price_usd_sync():
    separator = "&" if "?" in MARKET_PRICE_API_URL else "?"
    url = (
        f"{MARKET_PRICE_API_URL}{separator}base=BTC&quote=USDT"
        "&source=binance"
    )
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "BIFp2p/1.0"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=MARKET_PRICE_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError
    ) as exc:
        raise RuntimeError("USD market price service unavailable") from exc

    try:
        price_usd = Decimal(str(payload["price"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError("USD market price service returned an invalid price") from exc

    if price_usd <= 0:
        raise RuntimeError("USD market price service returned a non-positive price")

    return int(price_usd.quantize(Decimal("1"), rounding=ROUND_HALF_UP))



async def fetch_market_price_usd():
    return await asyncio.to_thread(_fetch_market_price_usd_sync)

