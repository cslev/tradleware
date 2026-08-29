# Webhooks

The webhook endpoint, its payload schema, and the security requirements every signal must
meet. If you have not installed Tradleware yet, start with
**[GETTING_STARTED.md](GETTING_STARTED.md)**.

---

## Webhook Security Configuration

Tradleware supports configurable webhook endpoints to protect against DDoS attacks and unauthorized access attempts. By default, webhooks are accessible at `/webhook`, but you can customize this to any random or meaningful path.

### Why Configure a Custom Webhook Path?

**Security Benefits:**
- **Obscurity**: Random webhook paths make it harder for attackers to discover your endpoint
- **DDoS Protection**: Without knowing the path, automated bots cannot target your webhook with spam requests
- **Brute Force Prevention**: Reduces the attack surface by making the endpoint URL unpredictable
- **No Code Changes**: You can change the path anytime by just updating the `.env` file

### How to Configure

1. **Generate a Random Webhook Path** (Recommended):
   ```bash
   # Generate a 14-character random string
   pwgen -n 14
   # Example output: ka8Moh4aiNgai4
   ```

2. **Add to `.env` file**:
   ```env
   # Default (less secure)
   WEBHOOK_PATH=webhook
   
   # Recommended: Use a random string
   WEBHOOK_PATH=ka8Moh4aiNgai4
   
   # Or use a custom meaningful path
   WEBHOOK_PATH=my-trading-signals-2024
   ```

3. **Update TradingView Webhook URL**:
   After changing the path, update your TradingView alerts to use the new webhook URL:
   ```
   https://your-tradleware-domain.com/ka8Moh4aiNgai4
   ```

### Webhook Endpoint Details

**Default Endpoint:**
- URL: `https://your-domain.com/webhook`
- Easy to guess, vulnerable to scanning

**Secured Endpoint (Example):**
- URL: `https://your-domain.com/webhook-ka8Moh4aiNgai4`
- Nearly impossible to guess, protected from random attacks

The webhook path is displayed in:
- The web UI on each trading bot card
- The footer of the dashboard
- The cURL test examples

**Note:** The webhook still requires API key authentication, so even if someone discovers the URL, they cannot execute trades without the correct `tradleware_api_key` configured in the bot's YAML file. Moreover, each bot has different keys, thereby limiting further the impact of any small information being compromised.

> **Generate the key, don't invent it:** `openssl rand -hex 32`. The webhook does not throttle
> failed attempts, so a short or memorable key can be guessed. Tradleware checks each bot's key
> at startup and flags it on the dashboard when it is too small, too repetitive, reused across
> bots, or still one of the placeholders from the `.yaml.example` files — those are published in
> this repository, so they are not secret at all. Nothing is ever refused over a weak key; a bot
> that trades keeps trading, and the choice stays yours.

### Webhook Payload

Every webhook request must be a `POST` with a JSON body. Here's a full example:

```json
{
  "api_key":         "your_tradleware_api_key",
  "trader_id":       "mybtcbot",
  "ticker":          "BTC/USDT",
  "action":          "buy",
  "timestamp":       "2026-03-28T12:00:00Z",   // must be the current time — see Replay protection
  "alert_name":      "Supertrend Buy Signal",
  "order_size":      100,
  "order_size_type": "percentage",
  "dry_run":         false
}
```

Key fields:

| Field | Required | Description |
|-------|----------|-------------|
| `api_key` | Yes | The `tradleware_api_key` from the bot's YAML config |
| `trader_id` | Yes | The `id` field from the bot's YAML config (lowercase) |
| `ticker` | Yes | The bot's configured `crypto_stablecoin_pair` (crypto) or `symbol` (stock), written out in full |
|  |  | ⚠️ **Hardcode it — do not use `{{ticker}}`.** A bot trades exactly one instrument, so this field is an interlock confirming the alert is pointed at the right bot; it can never usefully vary. TradingView expands `{{ticker}}` to the venue-native spelling (`BTCUSDC` for `BINANCE:BTCUSDC`), not the `BTC/USDC` form Tradleware expects. Separator and case differences (`BTCUSDC`, `btc-usdc`) are accepted with a warning and rewritten to the configured pair, but a different instrument is always rejected. Perpetuals keep their `:` (`BTC/USDT:USDT` never matches spot `BTC/USDT`). |
| `action` | Yes | `buy` or `sell` |
| `timestamp` | Yes | Unix timestamp (seconds or ms) or ISO 8601 string — **must be the moment the signal fired**, see [Replay protection](#replay-protection) |
| `order_size` | Yes | Amount to trade — meaning depends on `order_size_type` |
| `order_size_type` | No | `percentage` (default), `quantity`, or `cash` |
| `alert_name` | No | Optional label shown in logs and notifications |
| `dry_run` | No | `true` to simulate without executing — useful for testing |

### Sizing modes

| `order_size_type` | `order_size` means | Example |
|---|---|---|
| `percentage` | percent of available cash (buy) or of the position (sell), 0–100 | `25` → a quarter |
| `quantity` | exact shares or coins | `12` → 12 shares |
| `cash` | **buy only** — the currency you pay with: the account currency for stocks, the pair's quote currency for crypto | `300` → 300 USD of an ETF, or 300 USDT of BTC |

`cash` is what monthly DCA wants: the amount is fixed rather than derived from the
balance. The same payload works on either broker family — a signal never needs to know
which one it is hitting. Two things follow from the amount being fixed.

**Stocks:** without `fractional_shares: true`, the order is **truncated to whole shares** — $300 into
a $110 ETF buys 2 shares ($220) and logs `Spent 220.00 of 300.00 requested`. Unlike
percentage mode this does not catch up on its own, because the next order is pinned to
the same amount and never reads the balance. Enable fractional shares where the
instrument supports it, or expect the remainder to accumulate.

**Crypto:** the amount is checked against the pair's quote balance and against the
exchange's minimum notional, so an amount below the venue's floor is refused with the
limit named rather than rejected by the exchange.

Cash-denominated **sells are rejected**, on both broker families. Sizing an exit in cash changes meaning as the
price moves and cannot express "close the position" — use `percentage` with `100`.

> **Tip:** Each bot's dashboard card has a **Webhook Details** pane showing the exact endpoint URL, a ready-to-use cURL example, and a live test button — the easiest way to verify your setup without leaving the UI.

### Webhooks must use HTTPS

The `api_key` travels **inside the request body**. Delivered over plain HTTP it is readable
by anyone on the network path, who can then place orders of their own — replay protection
does not help against that, since they can compose a brand-new signal rather than repeat
an old one. Webhooks are therefore refused with `403` unless they arrived over TLS.

> A working nginx config is in [`examples/nginx/tradleware.conf`](examples/nginx/tradleware.conf).

**Tradleware does not terminate TLS itself.** There is no certificate configuration; uvicorn
serves plain HTTP inside the container. So the supported setup is a TLS-terminating proxy in
front, and `TRUSTED_PROXIES` set so its `X-Forwarded-Proto` header is believed:

```env
TRUSTED_PROXIES=172.18.0.0/16   # the address your proxy connects from
WEBHOOK_REQUIRE_HTTPS=true      # the default
```

Those two settings work together — without `TRUSTED_PROXIES`, the proxy's header is
(correctly) distrusted and **every webhook is rejected**. Tradleware warns about exactly this
combination at startup. The rejection log line names which of the three cases applies:

| Log says | Meaning | Fix |
|---|---|---|
| *arrived over plain HTTP* | No TLS anywhere in the chain | Put a TLS-terminating proxy in front |
| *not in TRUSTED_PROXIES* | Proxy is terminating TLS, but Tradleware does not trust it | Set `TRUSTED_PROXIES` to the proxy's address |
| *client reached the proxy over plain HTTP* | The proxy is reachable over `http://` | Redirect HTTP→HTTPS at the proxy |

If you terminate TLS in uvicorn directly (`--ssl-keyfile` / `--ssl-certfile`, outside Docker),
that is recognised too and no proxy configuration is needed.

`WEBHOOK_REQUIRE_HTTPS=false` disables the check. That is only reasonable when the signal
source runs on the same host or a trusted LAN — never for a webhook reachable from the
internet, which includes every TradingView setup.

### Replay protection

The `api_key` travels inside the request body, so anyone who captures one webhook request
holds a reusable trading capability: the same bytes, sent again, would place another real
order. Tradleware blocks that in two ways, both always on:

1. **Freshness window** — the `timestamp` in the payload must be within `WEBHOOK_MAX_AGE_S`
   seconds of this host's clock (default 300s, in either direction). Anything older or
   further in the future is rejected with `400`.
2. **Single use** — the exact request body is remembered for as long as it could still pass
   the freshness check, and a repeat is rejected with `409`. The record is written to disk,
   so restarting Tradleware does not reopen the window. Expired records are discarded
   automatically, so the file only ever holds the last few minutes of signals.

**⚠️ TradingView users: send `{{timenow}}`, not `{{time}}`.**

```json
"timestamp": "{{timenow}}"
```

`{{time}}` is the timestamp of the *bar*, not of the alert. On a 4-hour chart it is already
up to 4 hours old when the alert fires, and on a daily chart up to 24 hours — every signal
would be rejected as stale. `{{timenow}}` is the moment the alert fired, which is what the
freshness window needs. The example on each bot's **Webhook Details** pane is already
correct; copy it from there.

If signals start being rejected, the log line names the cause — a stale timestamp, a clock
that is out of sync with the signal source, or a duplicate delivery. `WEBHOOK_MAX_AGE_S`
can be widened if your source is slow, but it cannot be switched off: an unbounded window
means captured requests replay forever.

#### Keep the host clock synced

Freshness is measured against this host's clock, so a machine that drifts more than
`WEBHOOK_MAX_AGE_S` seconds from real time will reject perfectly valid signals. The
*timezone* is irrelevant — everything is compared in UTC — only *accuracy* matters. This
bites Raspberry Pi setups in particular: a Pi has no battery-backed clock and starts up
with the wrong time until NTP corrects it.

Fix it on the **host**, not in the container — Docker inherits the host clock:

```bash
sudo timedatectl set-ntp true   # Debian / Raspberry Pi OS / Ubuntu
timedatectl status              # want: "System clock synchronized: yes"
```


