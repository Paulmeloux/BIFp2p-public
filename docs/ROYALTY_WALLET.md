# Per-trade royalty wallet

The built-in adapter uses LNbits' authenticated payment API. The royalty wallet
is separate from the Coinos wallet used for trade escrow. LNbits can connect to
different Lightning funding backends; using Coinos for trade escrow does not
require using Coinos for royalty payments.

## Configuration

Install `requirements.txt`. Configure the usual Telegram/trade environment and
a dedicated LNbits wallet with its own admin key, trusted HTTPS origin and a
small operator-controlled balance. Use LNbits' fee controls as appropriate.
Never put the wallet key in Git, Telegram messages or reports.

The example below describes the intended operator configuration. It is not an
instruction to activate payments on the original project's server. Validate
an independent deployment and obtain authorization for any live testing first.

```text
ROYALTY_MODE=PRODUCTION_ENFORCED
ROYALTY_WALLET_ADAPTER=lnbits
ROYALTY_LNBITS_URL=https://your-lnbits.example
ROYALTY_LNBITS_ADMIN_KEY=<dedicated-wallet-admin-key>
ROYALTY_PAYMENT_TRANSPORT_ENABLED=true
```

Defaults never send royalties: transport is `false`, mode is `UNCONFIGURED`.
The configuration template does not activate itself. `ACCOUNTING_TEST` uses
the explicit test acknowledgement and never submits payments; obligations
created there are never converted into payable production obligations.

The beneficiary is fixed to `bitcoinisfreedombot@coinos.io`; the grace period
is fixed to 72 hours. Former destination/grace environment overrides are ignored.
Do not relabel an old test ledger for production. Start an independent deployment
with a fresh database, or review any ledger transition explicitly.

## Operation and proof

The worker runs every 30 seconds independently of trade recovery. For each
successful trade it calculates `original_amount_sats // 1000`, obtains a fresh
invoice from the fixed Lightning Address, and checks its signature, mainnet
network, exact millisatoshi amount, metadata hash and expiry. Credentials are
sent only to the configured wallet origin; redirects are disabled.

Before submission, a transaction records the attempt, wallet identity and
invoice payment hash. Each invoice hash is unique across attempts. The raw
invoice, wallet credentials and preimage are not stored or logged by this
royalty code. Accounting records retain the amount, beneficiary, timestamps,
invoice hash and settlement reference.

The worker submits once and separately requests payment status. LNbits must
return an authenticated outgoing record for the exact hash and amount, plus
a 32-byte preimage whose SHA256 matches that hash. A successful POST alone
does not mark an obligation `PAID`.

Failure before the submission boundary can be retried after a 60-second backoff.
After the boundary, timeout, crash, a missing record, or an unverified response
remains `UNKNOWN`. Only read-only lookup is repeated, never another payment.
Changing the wallet URL or key changes its identity and prevents automatic
reconciliation of old attempts through the new wallet. Preserve the original
configuration and recover it before resolving those attempts.

## Incidents and recovery

Use the administrator-only `/royalty_status` command for local status.
Faults, unresolved payments and overdue obligations produce an admin Telegram
notice; the operator must report them to `paulmeloux@proton.me` without user
identities, bank information, invoices, credentials, seeds or keys. The software
does not email Paul and does not notify him for successful payments.

At 72 hours after completion, any positive unresolved royalty pauses new
order creation and acceptance. Existing trade settlement, refund and recovery
continue. Verified payment releases that debt's gate automatically.

For `UNKNOWN`, keep the database and original wallet configuration. Let the
read-only lookup reconcile verified success. Do not delete attempts, edit
`PAID` into the database or send a replacement invoice blindly. If the wallet
cannot supply proof, contact Paul and review the wallet records; new orders
remain paused after the deadline. There is deliberately no unverified
force-paid or resend command.

Back up the database and wallet configuration together. A database restored
from an old backup may predate a real payment; **do not resume production
automatically**. Review wallet history and reconcile every potentially missing
obligation/attempt before allowing the worker to create invoices. The current
code does not guarantee exactly-once behavior after database loss or rollback.

## Other wallets

Set `ROYALTY_WALLET_ADAPTER` to a trusted, installed local Python module exposing
`create_wallet()`. This is operator code, not an untrusted remote plugin.
Its returned object must have:

- `identity`: a stable non-secret string identifying the exact wallet account.
- `pay_invoice(invoice)`: pay the supplied `RoyaltyInvoice.bolt11` exactly once;
  never internally retry an ambiguous POST or replace the invoice.
- `lookup_payment(payment_hash)`: read-only lookup returning `PaymentEvidence`
  with the exact hash, principal amount in integer sats and settled preimage,
  or `None` for unresolved outcomes. Never infer success from amount/time alone.

Methods are synchronous and run on background threads. Constructors/factories
must validate locally without network calls or payments. The adapter must
enforce wallet ownership and outgoing direction. The worker independently
checks the returned proof. Wallets without a compatible proof/lookup mechanism
are not supported by this adapter contract; manual assertions are not proof.

This implementation has been tested offline only. Live compatibility and
deployment validation remain separate from source preparation.
