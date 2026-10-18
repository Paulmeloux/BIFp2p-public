"""Runtime settings, user-facing text, and conversation state constants."""

import os
import re
from decimal import Decimal

from project_identity import PROJECT_ATTRIBUTION


TOKEN = os.getenv("BOT_TOKEN")


COINOS_TOKEN = os.getenv("COINOS_TOKEN")


COINOS_API_BASE = os.getenv("COINOS_API_BASE", "https://coinos.io/api").rstrip("/")


ROYALTY_MODE = os.getenv("ROYALTY_MODE", "UNCONFIGURED").strip().upper()


ROYALTY_DESTINATION_KIND = "LIGHTNING_ADDRESS"


ROYALTY_DESTINATION = "bitcoinisfreedombot@coinos.io"


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


# Fixed license deadline; operators cannot extend it through environment settings.
ROYALTY_SETTLEMENT_GRACE_SECONDS = 72 * 60 * 60
ROYALTY_PAYMENT_TRANSPORT_ENABLED = os.getenv("ROYALTY_PAYMENT_TRANSPORT_ENABLED", "false").lower() == "true"
ROYALTY_LNBITS_URL = os.getenv("ROYALTY_LNBITS_URL", "").strip()
ROYALTY_LNBITS_ADMIN_KEY = os.getenv("ROYALTY_LNBITS_ADMIN_KEY", "").strip()
ROYALTY_WALLET_ADAPTER = os.getenv("ROYALTY_WALLET_ADAPTER", "lnbits").strip()



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
    "Your order expired after 12 hours without a counterparty and was removed "
    "from the channel. You can create a new order."
)

SUPPORT_BUTTON_TEXT = "Support"
GUIDE_BUTTON_TEXT = "Guide and rules"

GUIDE_TEXT = """<b>BIFp2p | Guide and rules</b>

BIFp2p helps people exchange Bitcoin directly using Lightning.
Buyers and sellers settle fiat directly with one another. Coinos is used
as a custodial Lightning escrow and payout provider; this is not a
trustless or anonymous service.

<b>Creating an order</b>
Choose Buy or Sell, enter the amount in satoshis, select market-based
or fixed pricing, and confirm the offer. The operator publishes orders
in their configured Telegram channel. A Telegram username is required.

Order size: 10,000 to 100,000 satoshis.

<b>Fees and reputation</b>
The seller pays the operator commission, determined when the offer is
accepted and locked for that trade. The buyer pays no BIFp2p fee.
Seller commission tiers based on reputation: 1%, 0.9%, 0.8%, 0.7%,
0.6%, or 0.5%. The original project's 0.1% royalty is paid from
the operator's commission, not added to either trader's fee.

<b>Safety</b>
Confirm a fiat transfer only after it actually happens. If a dispute
arises, stop and contact your operator's support. Do not share identity
documents or banking credentials with the bot. Counterparties may
choose to share settlement details directly; protect that information.
Telegram, Coinos, and your trading partner introduce additional risks.

<b>Project origin</b>
BIFp2p was created by Paul under Bitcoin Is Freedom.
Original project: https://t.me/BitcoinIsFreedom
Source: https://github.com/Paulmeloux/BIFp2p-public
Use /about for project attribution. Your operator is independent from
the original project unless explicitly stated otherwise.
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


SUPPORT_NOTIFICATION_PREFIX = "New support request"


SUPPORT_ACK_PREFIX = "Your message has been sent to support."


SUPPORT_REPLY_PREFIX = "Support reply"


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


