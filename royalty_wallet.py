"""Lightning-address resolution and the operator's royalty wallet boundary.

LNbits API: https://github.com/lnbits/lnbits/blob/main/lnbits/core/views/payment_api.py
BOLT11 decoding/signature verification: https://github.com/lnbits/bolt11
No live requests are made on import or configuration validation.
"""
import hashlib
import importlib
import json
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bolt11 import decode
import settings

BENEFICIARY = "bitcoinisfreedombot@coinos.io"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(url):
    p = urlsplit(url)
    if (p.scheme != "https" or not p.hostname or p.username or p.password
            or p.fragment or any(ord(c) < 33 for c in url)):
        raise ValueError("A valid HTTPS URL without credentials is required")
    return p


def request_json(url, *, payload=None, headers=None):
    https_url(url)
    request = urllib.request.Request(
        url, data=None if payload is None else json.dumps(payload).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        method="GET" if payload is None else "POST",
    )
    # No redirects, token forwarding, retries or unbounded responses.
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError("Oversized wallet response")
    result = json.loads(body)
    if not isinstance(result, dict) or result.get("status") == "ERROR":
        raise ValueError("Invalid payment service response")
    return result


@dataclass(frozen=True)
class RoyaltyInvoice:
    bolt11: str
    payment_hash: str
    amount_sats: int
    expires_at: int


@dataclass(frozen=True)
class PaymentEvidence:
    payment_hash: str
    amount_sats: int
    preimage: str


def resolve_invoice(amount_sats):
    """Obtain and authenticate an exact invoice from the fixed beneficiary."""
    if type(amount_sats) is not int or amount_sats <= 0:
        raise ValueError("Positive integer royalty amount required")
    params = request_json("https://coinos.io/.well-known/lnurlp/bitcoinisfreedombot")
    metadata = params.get("metadata")
    if params.get("tag") != "payRequest" or not isinstance(metadata, str):
        raise ValueError("Invalid LNURL pay metadata")
    low, high = params.get("minSendable"), params.get("maxSendable")
    if (type(low) is not int or type(high) is not int
            or not low <= amount_sats * 1000 <= high):
        raise ValueError("Royalty outside beneficiary invoice limits")
    callback = params.get("callback")
    if not isinstance(callback, str):
        raise ValueError("Missing beneficiary callback")
    parts = https_url(callback)
    if parts.hostname != "coinos.io" or parts.port not in (None, 443):
        raise ValueError("Beneficiary callback must stay on coinos.io")
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "amount"]
    query.append(("amount", str(amount_sats * 1000)))
    reply = request_json(urlunsplit(parts._replace(query=urlencode(query))))
    invoice = reply.get("pr")
    if not isinstance(invoice, str) or not invoice.startswith("lnbc") or invoice != invoice.lower():
        raise ValueError("Mainnet BOLT11 invoice required")
    decoded = decode(invoice)  # validates signature and mandatory tags
    now = int(time.time())
    expires_at = decoded.date + decoded.expiry
    if (decoded.currency != "bc" or decoded.amount_msat != amount_sats * 1000
            or expires_at <= now + 30 or decoded.date > now + 300
            or decoded.description_hash != hashlib.sha256(metadata.encode()).hexdigest()):
        raise ValueError("Invoice amount, network, expiry or metadata mismatch")
    payment_hash = decoded.payment_hash
    if not isinstance(payment_hash, str) or len(bytes.fromhex(payment_hash)) != 32:
        raise ValueError("Invalid invoice payment hash")
    return RoyaltyInvoice(invoice, payment_hash, amount_sats, expires_at)


class LNbitsWallet:
    """Pay once; independently look up by the persisted invoice hash."""
    def __init__(self, url, admin_key):
        p = https_url(url)
        if p.query or p.path not in ("", "/"):
            raise ValueError("LNbits URL must be an HTTPS origin")
        if not admin_key or any(ord(c) < 33 for c in admin_key):
            raise ValueError("LNbits admin key is required")
        self.url = url.rstrip("/")
        self._key = admin_key
        self.identity = "lnbits:" + hashlib.sha256((self.url + "|" + admin_key).encode()).hexdigest()

    def pay_invoice(self, invoice):
        # A returned response is deliberately NOT accepted as settlement evidence.
        request_json(self.url + "/api/v1/payments", headers={"X-Api-Key": self._key},
                     payload={"out": True, "bolt11": invoice.bolt11})

    def lookup_payment(self, payment_hash):
        result = request_json(self.url + "/api/v1/payments/" + payment_hash,
                              headers={"X-Api-Key": self._key})
        details = result.get("details")
        if result.get("paid") is not True or not isinstance(details, dict):
            return None
        amount_msat = details.get("amount")
        # An authenticated outgoing record, never an incoming or amount-only match.
        if (details.get("payment_hash") != payment_hash or type(amount_msat) is not int
                or amount_msat >= 0 or amount_msat % 1000):
            return None
        return PaymentEvidence(payment_hash, -amount_msat // 1000, result.get("preimage"))


def configured_wallet():
    """Other trusted local wallet modules may provide create_wallet()."""
    if settings.ROYALTY_WALLET_ADAPTER == "lnbits":
        wallet = LNbitsWallet(settings.ROYALTY_LNBITS_URL, settings.ROYALTY_LNBITS_ADMIN_KEY)
    else:
        wallet = importlib.import_module(settings.ROYALTY_WALLET_ADAPTER).create_wallet()
    if (not isinstance(getattr(wallet, "identity", None), str) or not wallet.identity
            or len(wallet.identity) > 200 or any(ord(c) < 33 for c in wallet.identity)
            or not callable(getattr(wallet, "pay_invoice", None))
            or not callable(getattr(wallet, "lookup_payment", None))):
        raise TypeError("Wallet requires a stable non-secret identity, pay_invoice and lookup_payment")
    return wallet


def verified_evidence(evidence, payment_hash, amount_sats):
    if (not isinstance(evidence, PaymentEvidence) or evidence.payment_hash != payment_hash
            or type(evidence.amount_sats) is not int or evidence.amount_sats != amount_sats
            or not isinstance(evidence.preimage, str)):
        return False
    try:
        preimage = bytes.fromhex(evidence.preimage)
    except ValueError:
        return False
    return len(preimage) == 32 and hashlib.sha256(preimage).hexdigest() == payment_hash
