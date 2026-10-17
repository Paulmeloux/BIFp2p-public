"""Per-trade, wallet-neutral Lightning royalty payment contract.

No batching and no payment credentials in this module. The operator's
wallet adapter must expose invoice payment and independent payment lookup.
A Lightning Address is an invoice discovery endpoint, not a wallet.
"""
from dataclasses import dataclass

ROYALTY_ADDRESS = "bitcoinisfreedombot@coinos.io"


@dataclass(frozen=True)
class RoyaltyPayment:
    order_id: int
    amount_sats: int
    destination: str = ROYALTY_ADDRESS


def royalty_for_completed_trade(order_id, original_amount_sats, status):
    """Produce exactly one trade-specific obligation, not a batch."""
    if status != "COMPLETED":
        return None
    if type(order_id) is not int or order_id <= 0:
        raise ValueError("invalid order ID")
    if type(original_amount_sats) is not int or original_amount_sats < 0:
        raise ValueError("invalid original amount")
    sats = original_amount_sats // 1000
    return RoyaltyPayment(order_id, sats) if sats else None


def validate_exact_invoice(invoice, expected_sats, decode_amount):
    """Check BOLT11 amount using an injected decoder before wallet payment.

    The wallet adapter must separately verify invoice signature, network,
    expiration, payment hash and destination before paying.
    """
    if not isinstance(invoice, str) or not invoice:
        raise ValueError("invoice required")
    if decode_amount(invoice) != expected_sats:
        raise ValueError("invoice amount does not match this trade's royalty")
    return invoice


def validate_wallet_adapter(wallet):
    """Prevent accidental use of a wallet with no recovery interface."""
    if not callable(getattr(wallet, "pay_invoice", None)):
        raise TypeError("wallet adapter must implement pay_invoice")
    if not callable(getattr(wallet, "lookup_payment", None)):
        raise TypeError("wallet adapter must implement lookup_payment")
    return wallet
