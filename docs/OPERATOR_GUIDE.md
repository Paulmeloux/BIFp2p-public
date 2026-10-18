# BIFp2p operator setup and recovery

BIFp2p | Bitcoin Is Freedom | Created by Paul  
Source: https://github.com/Paulmeloux/BIFp2p-public  
Maintainer: https://github.com/Paulmeloux  
Contact: **paulmeloux@proton.me**

Read [LICENSE](../LICENSE), [SECURITY.md](../SECURITY.md), and the
[royalty wallet guide](ROYALTY_WALLET.md). This is a development preview.
The original Telegram service is separate from your independent deployment.

## 1. Requirements and separation

- Python **3.11 or 3.12**, Git, and a host that stays online.
- Your own Telegram bot token, administrator ID, order channel and support.
- Your own Coinos account/API token for Lightning escrow and buyer payouts.
- Your own compatible market-price endpoint, described below.
- A dedicated royalty-paying wallet. The built-in adapter is **LNbits** with
  a trusted HTTPS origin and a wallet admin key. Another wallet requires an
  adapter implementing the documented lookup/proof contract.

Use separate bots, wallets and databases for tests and live operation. Never
copy the original project's database, server configuration or wallet material.
Only one running bot process should use a given deployment database.

## 2. Download, install and check offline

The current preparation branch is shown explicitly below. It is not a tagged
production release. For a future reviewed release, select its published tag
or commit instead; do not assume that a moving branch is certified.

```bash
git clone --branch dev/royalty-batching-reconciliation https://github.com/Paulmeloux/BIFp2p-public.git
cd BIFp2p-public
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
```

On Windows PowerShell, use `py -3.11 -m venv .venv` and
`.\.venv\Scripts\Activate.ps1`, then the same `python` commands. If PowerShell
blocks activation, use `.\.venv\Scripts\python.exe` directly; changing the
system-wide execution policy is unnecessary. On Windows, also install the IANA
timezone database with `python -m pip install tzdata` so `Asia/Tehran` is available.

These checks do not start the bot or make real payments. Installing dependencies
requires network access; the tests use fake services.

## 3. Operator configuration

Copy `.env.example` to `.env` and fill it with your own values. `.env` is ignored
by Git. On Linux, restrict it with `chmod 600 .env` and set `umask 077` before
first runtime use so new databases are private. Secure database directories and
backups on Windows using the deployment account's filesystem permissions.

| Variable | Meaning |
| --- | --- |
| `BOT_TOKEN` | Your Telegram bot token. |
| `COINOS_TOKEN` | Your trade escrow/payout API token, not the royalty wallet key. |
| `COINOS_API_BASE` | Trade API origin; default `https://coinos.io/api`. |
| `BIFP2P_ADMIN_ID` | Your positive numeric Telegram administrator ID. |
| `BIFP2P_CHANNEL_ID` | Your order channel, such as `@your_channel`. |
| `BIFP2P_OFFICIAL_CHANNEL_ID` | Your channel used for membership reputation; normally the order channel. |
| `BIFP2P_OFFICIAL_CHANNEL_URL` | The public URL of your own channel. |
| `BIFP2P_OFFICIAL_GROUP_ID` | Your community group where applicable; optional in current runtime validation. |
| `BIFP2P_BOT_USERNAME` | Your bot username, without `@`. |
| `BIFP2P_DB_PATH` | Private SQLite path; use a different path for each deployment. |
| `MARKET_PRICE_API_URL` | Your compatible price endpoint. |
| `ROYALTY_MODE` | Explicitly choose one of the two modes described below. |
| `ROYALTY_PAYMENT_TRANSPORT_ENABLED` | Defaults to `false`; production payments require explicit `true`. |
| `ROYALTY_WALLET_ADAPTER` | `lnbits`, or a trusted local adapter module. |
| `ROYALTY_LNBITS_URL` | HTTPS LNbits origin, with no path, credentials or query string. |
| `ROYALTY_LNBITS_ADMIN_KEY` | Admin key for your dedicated royalty wallet. |
| `BUYER_PAYOUT_PROCESSING_STALE_SECONDS` | Default 300; minimum 120. Stale payouts become reconciliation-only. |

Make the bot an administrator in its order channel with permission to publish
and delete its posts. Allow the membership lookups needed for your configured
channel. Users need Telegram usernames; the operator's admin identity is separate
from the project's attribution. Buyers and sellers settle fiat with each other.

### Price endpoint contract

The bot appends `base=BTC&quote=IRT&source=binance&source_usdt=nobitex&weighted=true`
for Iranian-market pricing, and `base=BTC&quote=USDT&source=binance` for USD posts.
Return JSON with a positive numeric `price` field. **For the IRT request, the
current code expects rial**, divides by 10 to obtain toman, then rounds down to
the nearest 1,000 toman. For example, `{"price": 1000000000}` means 100,000,000
toman per BTC in that request. USDT responses represent the BTC dollar price.
An endpoint that already returns toman must be adapted; otherwise the displayed
price will be wrong by a factor of ten. Review actual prices before use.

### Modes

- `ACCOUNTING_TEST`: only isolated development with
  `ROYALTY_ACCOUNTING_TEST_ACK=I_UNDERSTAND_ACCOUNTING_TEST_IS_NOT_PRODUCTION`.
  Keep transport `false`. Royalty obligations are non-payable test entries.
  **This does not disable real Telegram or Coinos trade calls** if you start the
  bot. The unit tests, not a funded bot in this mode, are the offline test route.
- `PRODUCTION_ENFORCED`: requires a working royalty wallet and explicit transport
  enablement before new orders can open. Use a fresh independent database;
  accounting-test entries are never silently converted into live obligations.

The fixed beneficiary and 72-hour deadline cannot be changed through environment
variables. See [ROYALTY_WALLET.md](ROYALTY_WALLET.md) for payment configuration.

## 4. Load configuration and start your own deployment

The application **does not automatically load `.env`**. For an operator-owned
Linux configuration containing plain `KEY=value` assignments:

```bash
set -a
. ./.env
set +a
python -c 'from settings import validate_runtime_config; validate_runtime_config(); print("Runtime configuration valid")'
python bot.py
```

Do not source configuration from an untrusted party. In PowerShell, a plain
`.env` file without quoted values can be loaded without executing it:

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
python -c "from settings import validate_runtime_config; validate_runtime_config(); print('Runtime configuration valid')"
python bot.py
```

Starting the bot contacts Telegram and can initiate recovery and royalty wallet
operations according to your environment. It is not an offline check. Use a
dedicated unprivileged account, protected environment configuration and a service
manager for unattended operation. Stop the old process before starting another.
Credentials must never be passed in public logs, command examples or Git.

The UI contains Persian trade messages and English guide/support labels; this
release does not promise an English-only interface. Current order limits are
10,000–100,000 sats, open offers expire after 12 hours, and market offsets are
limited to 5%. The current price worker posts at 00:00 Asia/Tehran; it does not
implement a noon post. These are current source behaviors, not new features.

## 5. Validate before accepting other users' funds

Contact Paul to arrange controlled real-payment evaluation. If he is unavailable,
complete and verify payment independently to **bitcoinisfreedombot@coinos.io**.
Owner acknowledgement is not a prerequisite for verified payment. Follow the
[required validation procedure](ROYALTY_WALLET.md#required-real-payment-validation).

Use operator-owned funds and a separate test bot/channel. Exercise escrow funding,
fiat confirmations, buyer payout and the corresponding per-trade royalty using
controlled participant accounts. Check the wallet's outgoing record, amount,
beneficiary, payment hash and settlement proof; confirm `/royalty_status` reflects
the exact trade. Test refunds and restart/recovery with controlled funds before
opening the service. Offline tests do not prove compatibility with your deployed
Coinos or wallet version.

A standalone manual payment confirms that payment, not an untested adapter.
Never attach it to an unrelated trade or treat it as credit for future royalties.
Do not repeat a payment with an uncertain outcome. The current code has no
standalone funded self-test command and no unverified force-paid command.

## 6. Daily operation and incidents

Use your own Telegram admin account for `/royalty_status`, `/maintenance_on`
and `/maintenance_off`. Maintenance pauses new trades; existing safety paths
must remain available. Read the reported status before changing configuration.

Royalties are paid from your commission. Successful payments remain in local
records; no email is required. Faults or unresolved outcomes notify your local
administrator. You must report them to **paulmeloux@proton.me**, without user
identities, bank details, invoices or wallet secrets. The bot does not email Paul.

After 72 hours, positive unresolved royalty obligations pause new orders.
Existing trades and refunds continue. Verified settlement clears that debt's
gate. For `UNKNOWN`, retain the database and original wallet configuration and
allow read-only reconciliation. Do not delete the attempt, resend blindly or
manually edit a row to `PAID`. Wallet key or URL changes prevent reconciliation
through the new identity; recover the original configuration and review first.

## 7. Backup, restore and upgrades

Keep encrypted backups of your database and environment configuration outside
Git with access restricted to the deployment account. Keep more than one version
and record when each snapshot was taken. Never send these backups to support.
An online SQLite backup can be created using Python's backup API, rather than
copying an actively written file:

```python
import sqlite3
from settings import DB_PATH

with sqlite3.connect(DB_PATH) as source:
    with sqlite3.connect("private-backup.sqlite3") as backup:
        source.backup(backup)
```

Store the resulting file privately and encrypt it. Test restoration on a copy
without starting a production bot or payment worker; SQLite `PRAGMA quick_check`
must report `ok`. Never run two deployments against copied credentials and ledgers.

Before upgrades, enable maintenance, preserve a backup, stop the bot and review
the selected revision and database changes. Run offline tests, then restart one
instance. Do not downgrade code against a migrated database without review.

**An old database backup may predate a payment that really happened.** After
database rollback or loss, do not automatically restart production mode. Compare
trade payouts and royalty attempts with the original wallets' histories first,
recover missing references and resolve uncertainty before enabling submissions.
Neither deleting the ledger nor restoring a backup proves nonpayment.
Exactly-once behavior across lost database state is not guaranteed by this code.

For suspected credential exposure, stop unauthorized access, preserve evidence,
rotate the affected credentials, and reconcile all potentially submitted payments
before resuming. Report vulnerabilities privately using [SECURITY.md](../SECURITY.md).
