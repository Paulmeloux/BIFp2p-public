"""Reserve stable escrow invoice IDs and reconcile Coinos invoices."""

import uuid

from coinos_api import CoinosNotFound, coinos_request
from database import db_connect


def reserve_coinos_invoice_id(order_id, order_side, seller_id, assignment_attempt):
    assigned_status = "BUYER_ASSIGNED" if order_side == "SELL" else "SELLER_ASSIGNED"
    conn = db_connect(timeout=30)
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        """
        SELECT coinos_invoice_id, coinos_invoice_attempt, amount_sats,
               manager_invoice, seller_invoice_sent_at_unix, fee_percent
        FROM orders
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND assignment_attempt = ?
        """,
        (order_id, order_side, assigned_status, seller_id, assignment_attempt),
    ).fetchone()
    if row is None:
        conn.close()
        return None

    invoice_id, invoice_attempt, amount_sats, manager_invoice, sent_at, fee_percent = row
    if invoice_id is not None and invoice_attempt == assignment_attempt:
        conn.commit()
        conn.close()
        return invoice_id, amount_sats, manager_invoice, sent_at, fee_percent

    invoice_id = str(uuid.uuid4())
    cursor.execute(
        """
        UPDATE orders
        SET coinos_invoice_id = ?, coinos_invoice_attempt = ?,
            coinos_payment_hash = NULL
        WHERE id = ? AND order_side = ? AND status = ?
          AND seller_id = ? AND assignment_attempt = ?
          AND manager_invoice IS NULL
        """,
        (
            invoice_id,
            assignment_attempt,
            order_id,
            order_side,
            assigned_status,
            seller_id,
            assignment_attempt,
        ),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        conn.close()
        return None
    conn.commit()
    conn.close()
    return invoice_id, amount_sats, None, None, fee_percent



async def get_or_create_coinos_invoice(invoice_id, amount_sats, order_code):
    try:
        invoice = await coinos_request("GET", f"/invoice/{invoice_id}")
    except (CoinosNotFound, RuntimeError):
        try:
            invoice = await coinos_request(
                "POST",
                "/invoice",
                {
                    "invoice": {
                        "id": invoice_id,
                        "amount": amount_sats,
                        "memo": f"BIFp2p {order_code}",
                    }
                },
            )
        except Exception:
            # A timed-out POST can still have succeeded remotely. Reconcile
            # using the same pre-reserved id; never generate a second invoice.
            invoice = await coinos_request("GET", f"/invoice/{invoice_id}")

    api_amount = int(invoice.get("amount") or 0)
    bolt11 = invoice.get("hash") or invoice.get("bolt11")
    if api_amount != amount_sats or not bolt11:
        raise RuntimeError("Coinos returned an invalid escrow invoice")
    return invoice, bolt11

