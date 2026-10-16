"""Runtime settings, user-facing text, and conversation state constants."""

import os
import re
from decimal import Decimal

from project_identity import PROJECT_ATTRIBUTION


TOKEN = os.getenv("BOT_TOKEN")


COINOS_TOKEN = os.getenv("COINOS_TOKEN")


COINOS_API_BASE = os.getenv("COINOS_API_BASE", "https://coinos.io/api").rstrip("/")


ROYALTY_MODE = os.getenv("ROYALTY_MODE", "UNCONFIGURED").strip().upper()


ROYALTY_DESTINATION_KIND = os.getenv("ROYALTY_DESTINATION_KIND", "").strip().upper()


ROYALTY_DESTINATION = os.getenv("ROYALTY_DESTINATION", "").strip()


ROYALTY_ACCOUNTING_TEST_ACK_PHRASE = "I_UNDERSTAND_ACCOUNTING_TEST_IS_NOT_PRODUCTION"


ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED = (
    os.getenv("ROYALTY_ACCOUNTING_TEST_ACK", "").strip()
    == ROYALTY_ACCOUNTING_TEST_ACK_PHRASE
)


def _nonnegative_environment_integer(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return value


ROYALTY_SETTLEMENT_GRACE_SECONDS = _nonnegative_environment_integer(
    "ROYALTY_SETTLEMENT_GRACE_SECONDS", 0
)


BUYER_PAYOUT_PROCESSING_STALE_SECONDS = _nonnegative_environment_integer(
    "BUYER_PAYOUT_PROCESSING_STALE_SECONDS", 5 * 60
)


CHANNEL_ID = os.getenv("BIFP2P_CHANNEL_ID", "").strip()


OFFICIAL_CHANNEL_ID = os.getenv("BIFP2P_OFFICIAL_CHANNEL_ID", CHANNEL_ID).strip()


OFFICIAL_CHANNEL_URL = os.getenv("BIFP2P_OFFICIAL_CHANNEL_URL", "").strip()


OFFICIAL_GROUP_ID = os.getenv("BIFP2P_OFFICIAL_GROUP_ID", "").strip()


BOT_USERNAME = os.getenv("BIFP2P_BOT_USERNAME", "").strip().lstrip("@")




MIN_ORDER_SATS = 10_000


MAX_ORDER_SATS = 100_000


OPEN_ORDER_LIFETIME_SECONDS = 12 * 60 * 60




ORDER_TIMEOUT_POLL_SECONDS = 30


MARKET_PRICE_API_URL = os.getenv("MARKET_PRICE_API_URL", "").strip()


MARKET_PRICE_TIMEOUT_SECONDS = 8


MAX_MARKET_OFFSET_PERCENT = Decimal("5")


REPOST_CLAIM_STALE_SECONDS = 5 * 60


OPEN_ORDER_EXPIRATION_MESSAGE = (
    "⏳ سفارش شما طی ۱۲ ساعت گذشته توسط کسی پذیرفته نشد و از کانال حذف شد.\n\n"
    "در صورت تمایل می‌توانید با اعمال تغییرات لازم، سفارش جدیدی ثبت کنید."
)


SUPPORT_BUTTON_TEXT = "🆘 پشتیبانی"




GUIDE_BUTTON_TEXT = "📜 راهنما و قوانین"


GUIDE_TEXT = """<b>راهنمای استفاده:</b>

بات BIFp2p ابزاری برای خرید و فروش همتا به همتای بیتکوین با تسویه Lightning است.

برای خرید یا فروش بیتکوین گزینه مربوط را انتخاب کنید، مقدار ساتوشی، روش قیمت‌گذاری و در صورت نیاز توضیحات سفارش را وارد کنید. قیمت می‌تواند بازار، درصدی بالاتر از بازار یا قیمت دلخواه باشد.

برای استفاده از بات، داشتن نام کاربری (Username) تلگرام الزامی است و نیازی به عضویت در کانال نیست. سفارش‌های خرید و فروش در کانال بیتکوین راه آزادی @BitcoinIsFreedom منتشر می‌شوند. پس از پذیرفته‌شدن سفارش توسط طرف مقابل، بات مرحله به مرحله شما را تا پایان معامله راهنمایی خواهد کرد.

حداقل مقدار هر سفارش 10,000 ساتوشی و حداکثر 100,000 ساتوشی است.

اگر در هر مرحله با مشکلی مواجه شدید از بخش پشتیبانی بخواهید شما را راهنمایی کند.

برای تبادل تجربه، همفکری و کمک به بهبود بات میتوانید عضو گروه BIFChat شوید:
https://t.me/BitcoinIsFreedomChat

<b>سیستم اعتبار:</b>

امتیاز کاربران بر اساس سابقه فعالیت و عضویت اختیاری در کانال محاسبه میشود.

عضویت در کانال بیتکوین راه آزادی: 1,000 امتیاز

هر معامله موفق: 100 امتیاز
به ازای هر ساتوشی معامله موفق: 1 امتیاز

معاملات ناقص یا لغوشده امتیاز مثبت ایجاد نمیکنند.

در صورتی که پس از انتقال بیتکوین به بات یکی از طرفین بدون ارائه توضیح قابل قبول به پشتیبانی باعث لغو معامله و اتلاف وقت و هزینه طرفین شود، پس از بررسی پشتیبانی ممکن است 1,000 امتیاز از اعتبار او کسر شود.

امتیاز کاربر هنگام انتشار سفارش بدون نمایش هویت او به‌صورت عمومی نمایش داده میشود.

با افزایش اعتبار، کارمزد فروشنده کاهش پیدا میکند.

<b>کارمزد:</b>

کارمزد فقط از فروشنده بیتکوین دریافت میشود و بر اساس امتیاز فروشنده در لحظه پذیرش سفارش محاسبه و برای همان معامله قفل میشود.

کمتر از 100,000 امتیاز: 1%
از 100,000 امتیاز: 0.9%
از 200,000 امتیاز: 0.8%
از 300,000 امتیاز: 0.7%
از 400,000 امتیاز: 0.6%
از 500,000 امتیاز به بالا: 0.5%

خریدار هیچ کارمزدی پرداخت نمیکند.

<b>قوانین و امنیت:</b>

معاملات بیتکوین در این بات از طریق Lightning انجام میشوند.
پرداخت فیات مستقیما میان خریدار و فروشنده انجام میشود.
تا زمانی که واقعا پرداخت یا دریافت وجه انجام نشده گزینه تأیید مربوط به آن مرحله را انتخاب نکنید.
در صورت بروز اختلاف فرآیند معامله را متوقف کرده و به پشتیبانی پیام دهید.

بات BIFp2p برای استفاده از خدمات خود از کاربران KYC، اطلاعات هویتی یا اطلاعات بانکی درخواست نمیکند.

ممکن است در جریان معامله خریدار و فروشنده برای انجام پرداخت فیات اطلاعات هویتی یا بانکی خود را مستقیما با یکدیگر مبادله کنند.

نگهداری و حفاظت از این اطلاعات مسئولیتی خطیر بر عهده طرفین معامله است.

اطلاعات هویتی و بانکی طرف مقابل را در اختیار دیگران قرار ندهید و پس از تکمیل کامل معامله اطلاعات طرف مقابل را حذف کنید.

<b>درباره BIFp2p:</b>

بات BIFp2p برای تسهیل مبادله مستقیم بیتکوین میان افراد، گسترش استقلال مالی و کاهش وابستگی به واسطه‌های متمرکز ساخته شده است.

هدف این پروژه کمک به گسترش استفاده از بیتکوین به‌عنوان پولی کمیاب، غیرمتمرکز و یکی از مهندسی‌شده‌ترین اشکال پول در تاریخ است.
"""


AMOUNT, PRICE_METHOD, PRICE, PRICE_PERCENT, DESCRIPTION, CONFIRM = range(6)


BUY_AMOUNT, BUY_PRICE_METHOD, BUY_PRICE, BUY_PRICE_PERCENT, BUY_DESCRIPTION, BUY_CONFIRM = range(6, 12)


ESCROWED_ORDER_STATUSES = (
    "SELLER_SATS_CONFIRMED",
    "FIAT_SENT",
    "FIAT_RECEIVED",
    "BUYER_INVOICE_RECEIVED",
)


ACTIVE_ORDER_STATUSES = (
    "OPEN",
    "REOPENING",
    "BUYER_ASSIGNED",
    "SELLER_ASSIGNED",
    *ESCROWED_ORDER_STATUSES,
)


DB_PATH = os.getenv("BIFP2P_DB_PATH", "users.db")


SELLER_INVOICE_PAYMENT_SECONDS = 20 * 60


ADMIN_ID = _nonnegative_environment_integer("BIFP2P_ADMIN_ID", 0)


SUPPORT_NOTIFICATION_PREFIX = "🛟 درخواست پشتیبانی جدید"


SUPPORT_ACK_PREFIX = "✅ پیام شما برای پشتیبانی ارسال شد."


SUPPORT_REPLY_PREFIX = "🛟 پاسخ پشتیبانی"


SUPPORT_MESSAGE = 12





def validate_runtime_config():
    if ROYALTY_MODE not in ("ACCOUNTING_TEST", "PRODUCTION_ENFORCED"):
        raise RuntimeError(
            "ROYALTY_MODE must be ACCOUNTING_TEST or PRODUCTION_ENFORCED"
        )
    if ROYALTY_MODE == "ACCOUNTING_TEST" and not ROYALTY_ACCOUNTING_TEST_ACKNOWLEDGED:
        raise RuntimeError(
            "ACCOUNTING_TEST requires the explicit ROYALTY_ACCOUNTING_TEST_ACK "
            "development/staging acknowledgement"
        )
    required = {"BIFP2P_CHANNEL_ID": CHANNEL_ID, "BIFP2P_OFFICIAL_CHANNEL_URL": OFFICIAL_CHANNEL_URL, "BIFP2P_BOT_USERNAME": BOT_USERNAME, "MARKET_PRICE_API_URL": MARKET_PRICE_API_URL}
    missing = [key for key, value in required.items() if not value or value.upper().startswith("REPLACE_WITH_")]
    if ADMIN_ID <= 0:
        missing.append("BIFP2P_ADMIN_ID")
    if missing:
        raise RuntimeError("Missing operator configuration: " + ", ".join(missing))
    if BUYER_PAYOUT_PROCESSING_STALE_SECONDS < 120:
        raise RuntimeError(
            "BUYER_PAYOUT_PROCESSING_STALE_SECONDS must be at least 120"
        )
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    if any(ord(character) < 32 or ord(character) == 127 for character in TOKEN):
        raise RuntimeError("BOT_TOKEN contains non-printable characters")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", TOKEN):
        raise RuntimeError("BOT_TOKEN has an invalid format")

