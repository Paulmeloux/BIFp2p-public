"""Menus, authenticated callback payloads, and order deep links."""

import hashlib
import hmac
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from settings import BOT_USERNAME, GUIDE_BUTTON_TEXT, SUPPORT_BUTTON_TEXT, TOKEN
from project_identity import PROJECT_ATTRIBUTION


def project_about_text():
    """Visible, operator-independent attribution for an About command/view."""
    return PROJECT_ATTRIBUTION


def main_menu():

    keyboard = [
        ["🟠 فروش بیتکوین", "🔵 خرید بیتکوین"],
        [GUIDE_BUTTON_TEXT],
        [SUPPORT_BUTTON_TEXT]
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )



def confirm_keyboard():

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ ثبت سفارش",
                callback_data="confirm_order"
            ),
            InlineKeyboardButton(
                "❌ لغو",
                callback_data="cancel_order"
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)



def buy_callback_data(order_id, open_generation=None):

    if open_generation is None:
        signature_payload = str(order_id)
        callback_payload = f"buy_order:{order_id}"
    else:
        callback_payload = f"buy_order:{order_id}:{open_generation}"
        signature_payload = callback_payload

    signature = hmac.new(
        TOKEN.encode(),
        signature_payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{callback_payload}:{signature}"



def confirm_channel_order_callback_data(order_id, open_generation=None):

    payload = f"confirm_channel_order:{order_id}"
    if open_generation is not None:
        payload = f"{payload}:{open_generation}"
    signature = hmac.new(
        TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload}:{signature}"



def decline_channel_order_callback_data(order_id, open_generation=None):

    payload = f"decline_channel_order:{order_id}"
    if open_generation is not None:
        payload = f"{payload}:{open_generation}"
    signature = hmac.new(
        TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload}:{signature}"



def sell_buy_order_callback_data(order_id, open_generation=None):

    payload = f"sell_buy_order:{order_id}"
    if open_generation is not None:
        payload = f"{payload}:{open_generation}"
    signature = hmac.new(
        TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload}:{signature}"



def confirm_buy_channel_order_callback_data(order_id, open_generation=None):

    payload = f"confirm_buy_channel_order:{order_id}"
    if open_generation is not None:
        payload = f"{payload}:{open_generation}"
    signature = hmac.new(
        TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload}:{signature}"



def decline_buy_channel_order_callback_data(order_id, open_generation=None):

    payload = f"decline_buy_channel_order:{order_id}"
    if open_generation is not None:
        payload = f"{payload}:{open_generation}"
    signature = hmac.new(
        TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload}:{signature}"



def order_deep_link_payload(action, order_id, open_generation):
    payload = f"{action}_{order_id}_{open_generation}"
    signature = hmac.new(
        TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:12]
    return f"{payload}_{signature}"



def order_deep_link_url(action, order_id, open_generation):
    payload = order_deep_link_payload(action, order_id, open_generation)
    return f"https://t.me/{BOT_USERNAME}?start={payload}"



def parse_order_deep_link(payload):
    match = re.fullmatch(r"(buy|sell)_(\d+)_(\d+)_([0-9a-f]{12})", payload or "")
    if match is None:
        return None
    action = match.group(1)
    order_id = int(match.group(2))
    open_generation = int(match.group(3))
    expected = order_deep_link_payload(action, order_id, open_generation)
    if not hmac.compare_digest(payload, expected):
        return None
    return action, order_id, open_generation



def parse_open_order_callback(callback_data, action, callback_builder):
    callback_match = re.fullmatch(
        rf"{re.escape(action)}:(\d+)(?::(\d+))?:[0-9a-f]{{16}}",
        callback_data
    )
    if callback_match is None:
        return None

    order_id = int(callback_match.group(1))
    generation_text = callback_match.group(2)
    callback_generation = int(generation_text) if generation_text is not None else 0
    expected_callback = callback_builder(
        order_id,
        callback_generation if generation_text is not None else None
    )
    if not hmac.compare_digest(callback_data, expected_callback):
        return None
    return order_id, callback_generation

