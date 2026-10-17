"""Coinos royalty transfer boundary.

A real Coinos HTTP transport is implemented here but NEVER invoked by bot
startup or scheduled workers. Production remains gated in royalty.py.

The Coinos upstream payments route has an internal(username, amount) handler.
Verify the deployed server route and response shape before wiring this module
into the batch ledger. A successful HTTP response alone is NOT settlement.
"""
import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROYALTY_BENEFICIARY_USERNAME = "bitcoinisfreedombot"
ROYALTY_BENEFICIARY_ADDRESS = "bitcoinisfreedombot@coinos.io"


class UncertainRoyaltyTransfer(RuntimeError):
    """Payment may have executed; do not automatically retry."""


def validate_coinos_endpoint(base_url):
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or parsed.hostname != "coinos.io":
        raise ValueError("Coinos royalty transfers require https://coinos.io")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Coinos URL contains unsupported components")
    if parsed.port not in (None, 443):
        raise ValueError("unexpected Coinos port")
    if parsed.path.rstrip("/") != "/api":
        raise ValueError("unexpected Coinos API path")
    return "https://coinos.io/api"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def prepare_internal_royalty_transfer(amount_sats, beneficiary=None):
    if type(amount_sats) is not int or amount_sats <= 0:
        raise ValueError("royalty transfer must be a positive integer number of sats")
    if beneficiary is not None and beneficiary != ROYALTY_BENEFICIARY_ADDRESS:
        raise ValueError("royalty beneficiary cannot be overridden")
    return {"username": ROYALTY_BENEFICIARY_USERNAME, "amount": amount_sats}


def royalty_transfer_fingerprint(batch_id, amount_sats):
    if type(batch_id) is not int or batch_id <= 0:
        raise ValueError("invalid batch ID")
    payload = prepare_internal_royalty_transfer(amount_sats)
    canonical = json.dumps({"batch": batch_id, **payload}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def submit_internal_royalty_transfer_once(
    *, base_url, token, amount_sats, payment_path="/payments/internal",
    opener=None
):
    """Submit exactly one request; caller MUST first durably claim the batch.

    The default route is provisional until checked against the operator's
    deployed Coinos server. All response and network failures are uncertain.
    This function deliberately does not interpret HTTP 200 as final payment.
    It must not be called again for an UNKNOWN batch.
    """
    base = validate_coinos_endpoint(base_url)
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Coinos API token required")
    if payment_path != "/payments/internal":
        raise ValueError("unexpected royalty payment route")
    body = json.dumps(prepare_internal_royalty_transfer(amount_sats)).encode()
    request = urllib.request.Request(
        base + payment_path,
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    client = opener if opener is not None else urllib.request.build_opener(_NoRedirect)
    try:
        with client.open(request, timeout=60) as response:
            result = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, ValueError) as exc:
        raise UncertainRoyaltyTransfer("Coinos transfer outcome is unknown") from None
    if not isinstance(result, dict) or not isinstance(result.get("id"), str):
        raise UncertainRoyaltyTransfer("Coinos transfer response is not verifiable")
    # The returned ID is evidence for reconciliation, NOT proof of settlement.
    return {"payment_id": result["id"], "verification_required": True}


async def submit_internal_royalty_transfer_once_async(**kwargs):
    return await asyncio.to_thread(submit_internal_royalty_transfer_once, **kwargs)
