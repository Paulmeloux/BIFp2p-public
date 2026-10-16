# Royalty recipient configuration

Confirmed by the operator on 2026-10-16:

- Lightning address: `bitcoinisfreedombot@coinos.io`
- Coinos account public URL: `https://coinos.io/bitcoinisfreedombot`
- Coinos account username (derived from the address and URL): `bitcoinisfreedombot`

The operator states that this **same Coinos account is already the BIFp2p trading bot's wallet**.

## Critical self-payment safeguard

Do not initiate a royalty transfer from this account to itself. An internal self-transfer may fail, be a no-op, or create misleading settlement records. Do not mark royalty obligations as PAID based on such a transaction.

The royalty ledger may record this as the operator-designated beneficiary account, but automated settlement must remain disabled until the source and destination account identities are distinct and a safe Coinos payment/reconciliation contract has been verified.

This document contains only public account identifiers. Never commit API tokens, authentication material, or private wallet credentials.

## Outstanding decision

To implement real outgoing royalty payments, provide a **separate** recipient Coinos account (or a distinct Lightning recipient), or explicitly adopt an accounting-only model where royalties remain in the operator's account and are *not* represented as paid out. A change in business model or license terms requires operator approval.
