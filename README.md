# Tradleware: The Sovereign Trading Appliance

<p align="center">
  <img src="src/ui/static/images/logos/logo_v5_horizontal.jpg" alt="Tradleware Logo" width="800">
</p>

<p align="center">
  <a href="https://tradleware.com"><img src="https://img.shields.io/badge/website-tradleware.com-blue.svg" alt="Website"></a>
  <img src="https://img.shields.io/badge/License-GPL%20v3-blue.svg" alt="License: GPL v3">
  <img src="https://img.shields.io/badge/pylint-10.00/10-brightgreen" alt="Pylint Score">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-docker-blue.svg" alt="Docker">
  <img src="https://img.shields.io/badge/built%20for-the%20global%20community-blue.svg" alt="Built for the global community">
  <img src="https://img.shields.io/badge/privacy-by%20design-green.svg" alt="Privacy by Design">
  <img src="https://img.shields.io/badge/cost-FREE%20💰-brightgreen.svg" alt="Free">
</p>



> 🌐 **Project website:** [tradleware.com](https://tradleware.com)

## Own your infrastructure. Own your alpha.

Tradleware (*/ˈtreɪ.dəl.wɛər/*) is a free, open-source autotrading middleware that bridges the gap between your trading strategies and the world’s most regulated exchanges. Tradleware is **Security-by-Design**, born from a unique intersection of two worlds:

- PhD-level Engineering: Forged with the obsessive precision of a PhD in Computer Science and the "Zero-Trust" mindset of a Cybersecurity Researcher.
- Built for the global community: Designed to run anywhere, on any hardware, for any trader who values privacy and control.

If a system isn't hardened, audited, and optimized for 24/7 reliability, it isn't worth running. 

**Tradleware** isn't just a script; it’s a high-performance engine for traders who treat their capital like a mission-critical asset.

## Why Tradleware?
Most "trading bridges" are black boxes in the cloud that demand your API keys and a monthly subscription. Tradleware is different. It is a Sovereign Appliance designed to run on a Raspberry Pi or your home lab.

The "dle" in Tradleware stands for Cradle: our mission is to cradle your sensitive credentials and logic safely within your own network. Your keys never leave your sight. No third-party services. No data harvesting. No "subscription tax." Just raw, private execution.


## High-Level Architecture
Tradleware acts as the hardened "Switchboard" between your signals and the market:
1.  **Ingress:** Listen on an unpredictable, custom webhook path for JSON signals.
2.  **Validation:** Verify signals against local, YAML-defined bot configurations.
3.  **Processing:** Apply custom logic (sizing, fractional checks, fiat-to-stablecoin conversion).
4.  **Execution:** Secure, local dispatch to the Exchange/Broker API.
<p align="center">
  <img src="src/ui/static/images/arch_v4_revamped.png" alt="Tradleware architecture" width="800">
</p>

## Industry-Standard Licensed Exchanges
**Tradleware** is built for regulated, industry-standard exchanges — eschewing offshore "ghost" exchanges in favour of fully-licensed platforms. It provides native, high-performance execution for Interactive Brokers (IBKR) for professional-grade TradFi, Independent Reserve for institutional crypto-fiat rails, and OKX, Crypto.com, Coinbase, Kraken, and Binance for liquid, fully-licensed spot markets.

| Exchange | Type | Regulated | MAS Approved |
|---|---|---|---|
| OKX | Crypto | ✅ | ✅ |
| Independent Reserve | Crypto | ✅ | ✅ |
| Crypto.com | Crypto | ✅ | ✅ |
| Coinbase | Crypto | ✅ | ✅ |
| Kraken | Crypto | ✅ | ❌ |
| Binance | Crypto | ✅ | ❌ |
| Interactive Brokers (IBKR) | Stock / TradFi | ✅ | ✅ |

> MAS = Monetary Authority of Singapore. All exchanges with ✅ hold or have received a Major Payment Institution (MPI) or Capital Markets Services (CMS) licence from MAS.

## Key Features

### Maximum Operational Security
* **Zero-Trust Architecture:** 100% on-premise. Your API keys never leave your network.
* **The "Cradle" Concept:** Designed to cradle your private credentials safely within your own infrastructure, protecting them from third-party "black box" vulnerabilities.
* **Webhook Hardening:** Custom endpoints (e.g., changing `/webhook` to an unpredictable path) via `.env` to stay invisible to scanners and block DDoS/Spam bots.
* **Trusted IP Auto-Login:** Optimized for keyboard/mouse-less Raspberry Pi setups—auto-login from your trusted home subnet only.
* **E2E Visibility:** Persistent dashboard indicators confirm your session is encrypted end-to-end.

### Precision Execution & Logic

* **Hybrid Trade Sizing:** Support for both **Percentage-based** and **Fixed Quantity** trading—essential for traditional stocks that do not support fractional shares.
* **Conflict-Free ROI:** Tradleware is broker-agnostic. It executes *your* logic, not an exchange's "AI bot" designed to farm transaction fees.
* **The "USB-C" of Trading:** Pluggable and extensible. Are you a developer? Inject custom Python logic *after* a signal arrives but *before* it hits the exchange.

### Efficiency & Sustainability
* **The 15W Trading Desk:** Specifically tuned for ARM/Raspberry Pi. Run 24/7 with a carbon footprint smaller than a household lightbulb.
* **Docker-First:** One-command deployment for both the middleware and the IBKR Gateway.
* **Real-time Monitoring:** FastAPI Web UI with color-coded logs and **Gotify** push notifications.


## Why Free?
Tradleware is a personal, open-source hobby project provided free to the developer community. It is not a commercial service or a business enterprise — if you run it on your own hardware, there's nothing to charge you for.

Financial sovereignty shouldn't have a middleman tax. If you find value in it, feel free to contribute to the code or the project.

---

<p align="center">
  <img src="screenshots/tradleware_v3.png" alt="Tradleware v3 Dashboard" width="50%">
</p>

## Getting Started

Tradleware runs from a pre-built Docker image — no compilation, no Python setup required. All you need is Docker, your exchange API keys, and the config template files.

### Step 1 — Get the deployment files

Clone the repo to get `docker-compose.yml` and all config templates. You won't touch the source code — this is just the quickest way to get everything in one step:

```bash
git clone --depth 1 https://github.com/cslev/tradleware
cd tradleware
```

> `--depth 1` fetches only the latest snapshot — no full git history, minimal download.

### Step 2 — Configure your bots

Tradleware uses two config layers:

- **`bot_configs/`** — per-exchange YAML files with API keys and bot settings (gitignored, stays on your server)
- **`.env`** — Tradleware-level settings only (dashboard auth, webhook path, logging, Gotify)

Copy only the example files for the exchanges you use:

```bash
# Crypto (copy only what you need)
cp bot_configs/crypto/okx.yaml.example       bot_configs/crypto/okx.yaml
cp bot_configs/crypto/cryptocom.yaml.example bot_configs/crypto/cryptocom.yaml
cp bot_configs/crypto/ir.yaml.example        bot_configs/crypto/ir.yaml
cp bot_configs/crypto/coinbase.yaml.example  bot_configs/crypto/coinbase.yaml
cp bot_configs/crypto/kraken.yaml.example    bot_configs/crypto/kraken.yaml
cp bot_configs/crypto/binance.yaml.example   bot_configs/crypto/binance.yaml

# Stock (IBKR) — also needs .env.ibkr for the gateway container credentials
cp bot_configs/stock/ibkr.yaml.example bot_configs/stock/ibkr.yaml
cp .env.ibkr.example                   .env.ibkr
```

Edit each file with your real credentials. Example structure for `bot_configs/crypto/okx.yaml`:

```yaml
bots:
  - id: mybtcbot              # lowercase — used as trader_id in webhook payloads
    api_key: your_okx_api_key
    secret_key: your_okx_secret_key
    passphrase: your_okx_passphrase
    subaccount_name: your_subaccount_name #always better to have a separate subaccount for trading strategies
    hostname: my.okx.com
    stablecoin_fiat_pair: USDT/USD
    crypto_stablecoin_pair: BTC/USDT
    tradleware_api_key: your_webhook_auth_key  # openssl rand -hex 32
```

For IBKR, see `bot_configs/stock/ibkr.yaml.example` for the full structure and [IBKR_SETUP.md](IBKR_SETUP.md) for the gateway setup. The IB Gateway runs as a separate Docker container — use the [cslev/ibkr-docker](https://github.com/cslev/ibkr-docker) image, which supports both `linux/amd64` and `linux/arm64` (Raspberry Pi).

### Step 3 — Configure `.env`

```bash
cp .env.example .env
```

Minimum settings to change:

```env
DASHBOARD_USERNAME="yourusername"
DASHBOARD_PASSWORD="your-secure-password"

# Randomize the webhook path to protect against automated scans
# Generate with: pwgen -n 14
WEBHOOK_PATH="ka8Moh4aiNgai4"
```

#### Full `.env` reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DASHBOARD_USERNAME` | No | `admin` | Dashboard login username |
| `DASHBOARD_PASSWORD` | No | `changeme` | Dashboard login password (⚠️ **Change this!**) |
| `SESSION_SECRET_KEY` | No | Auto-generated | Session encryption key — `openssl rand -hex 32`. Auto-generated keys change on restart, signing everyone out |
| `SESSION_HTTPS_ONLY` | No | `true` | Mark the session cookie `Secure`. With this on, signing in requires HTTPS — set `false` only for LAN-only setups |
| `SESSION_MAX_AGE_S` | No | `43200` | Session lifetime in seconds (12h). Signed cookies cannot be revoked server-side, so this is the only bound on a stolen one |
| `WEBHOOK_PATH` | No | `webhook` | Webhook URL path — randomize for security (`pwgen -n 14`) |
| `WEBHOOK_REQUIRE_HTTPS` | No | `true` | Refuse webhooks not delivered over TLS — requires `TRUSTED_PROXIES`, see [Webhooks must use HTTPS](#webhooks-must-use-https) |
| `TRADER_LOCK_TIMEOUT_S` | No | `60` | How long a request waits for a bot that is already executing before returning `503` |
| `WEBHOOK_FAILURE_LIMIT` | No | `20` | Failed webhook authentications from one address per window before it gets `429` |
| `WEBHOOK_FAILURE_WINDOW_S` | No | `60` | Length of that window, in seconds |
| `WEBHOOK_REJECTION_SUMMARY_S` | No | `300` | How often repeated webhook rejections are collapsed into one summary line |
| `WEBHOOK_MAX_AGE_S` | No | `300` | How stale a signal's timestamp may be before it is rejected (minimum `30`) — see [Replay protection](#replay-protection) |
| `TRUSTED_IPS` | No | — | Comma-separated IPs that bypass authentication |
| `TRUSTED_PROXIES` | No | — | Comma-separated IPs/CIDRs of reverse proxies allowed to report the client IP — see below |
| `LOG_REFRESH_INTERVAL_MS` | No | `5000` | Dashboard log refresh interval (ms) |
| `LOG_LEVEL` | No | `10` | Min log level: 10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR |
| `LOG_MAX_BYTES` | No | `10485760` | Rotate the log file once it reaches this size (10 MB) |
| `LOG_BACKUP_COUNT` | No | `5` | Rotated files to keep — with the default size, a ~60 MB ceiling |
| `LOG_COMPRESS_ROTATED` | No | `true` | Gzip rotated files (~8x), bringing that ceiling to ~16 MB. Read them with `zcat`/`zgrep` |
| `GOTIFY_SERVER_URL` | No | — | Gotify server URL for push notifications |
| `GOTIFY_APP_TOKEN` | No | — | Gotify application token |
| `GOTIFY_LOG_LEVEL` | No | `30` | Min log level for Gotify notifications |

#### Running behind a reverse proxy or tunnel

`TRUSTED_IPS` is matched against the address Tradleware sees the connection come from. When
Tradleware sits behind nginx, Caddy, Traefik or a Cloudflare Tunnel, every request arrives from
the proxy, so the real client address is only available in the `X-Forwarded-For` / `X-Real-IP`
headers — which the client itself can set to anything.

Tradleware therefore ignores those headers unless the request comes from an address listed in
`TRUSTED_PROXIES`:

```env
# The address (or subnet) Tradleware sees your proxy connect from
TRUSTED_PROXIES=172.18.0.0/16
```

> **Finding the right address.** Traffic reaching Tradleware from the Docker host — a kiosk
> browser on the same machine, Home Assistant embedding the dashboard, a proxy in a sibling
> container — arrives from the Docker network's **gateway**, not from the container's own IP.
> The dashboard footer shows the address Tradleware actually resolved, which is the one to
> put in `TRUSTED_IPS` / `TRUSTED_PROXIES`. Docker can reassign that gateway when networks are
> recreated, silently breaking the match; `docker-compose.yml` carries a commented `ipam`
> block for pinning the subnet if you depend on it.

- **No proxy (default):** leave `TRUSTED_PROXIES` empty. Forwarded headers are ignored entirely
  and `TRUSTED_IPS` is matched against the real connection address.
- **With a proxy:** list only the proxy's own address. Tradleware then takes the rightmost
  `X-Forwarded-For` hop that is not itself a trusted proxy — the address the proxy actually
  observed, not the one the client claimed.
- ⚠️ Never set `TRUSTED_PROXIES` to a range you do not control (and never to `0.0.0.0/0`).
  Doing so lets anyone bypass `TRUSTED_IPS` with a single spoofed header.

Make sure the proxy **overwrites** rather than appends the client's headers — for nginx,
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` is correct, since Tradleware
reads the chain from the right. If you run Tradleware outside Docker, start uvicorn with
`--no-proxy-headers` so `TRUSTED_PROXIES` stays the single source of truth (the bundled
Dockerfile already does this).

### Step 4 — Run

```bash
docker-compose up -d
```

Docker pulls `cslev/tradleware:latest` automatically on first run. The dashboard will be available at `http://localhost:8080`.

> For different versions and architectures (including arm64 for Raspberry Pi), visit [Docker Hub](https://hub.docker.com/repository/docker/cslev/tradleware/tags).

### Updating to a new version

```bash
docker-compose down
docker-compose pull
docker-compose up -d
```

### View logs

```bash
docker-compose logs -f tradleware
```

### Stop

```bash
docker-compose down
```

**Note:** `docker-compose.yml` mounts `./tradleware_data/logs` (persistent logs) and `./bot_configs` (read-only config) — both survive container restarts.

> Want to build from source or contribute? See [BUILD.md](BUILD.md).

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the security behaviour of the dashboard and webhook: client address
resolution behind proxies, session cookie flags, credential comparison, webhook transport
and replay protection, per-bot execution serialisation, and log rotation.

It is self-contained — no exchange is contacted, no notification is sent anywhere real, and
your `.env` is deliberately **not** read, so results do not depend on how your own instance
happens to be configured. Test order is shuffled on every run to expose state leaking
between tests; the seed is printed in the header and can be replayed with
`pytest -p no:randomly` (fixed order) or `pytest --randomly-seed=<seed>`.

## Gotify Integration

Tradleware supports real-time push notifications via Gotify for important trading events and critical errors — so you never miss a trade execution, a failed order, or anything that needs your attention, even when you're away from the dashboard.

### Setup Gotify Notifications

1. **Install Gotify Server** (if you don't have one):
   ```bash
   # Using Docker
   docker run -p 80:80 gotify/server
   ```

2. **Configure Environment Variables**:
   Add these to your Tradleware's `.env` file:
   ```env
   GOTIFY_SERVER_URL=https://your-gotify-server.com
   GOTIFY_APP_TOKEN=your_app_token_here
   GOTIFY_LOG_LEVEL=30
   ```

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
| `ticker` | Yes | Must match the bot's configured `crypto_stablecoin_pair` exactly |
|  |  | ⚠️ **Important:** If you use TradingView or any system where you cannot control the exact ticker format, always hardcode the `crypto_stablecoin_pair` (e.g., `BTC/USDT`) in your webhook payload. TradingView may send `BTCUSD` or `BTC USD`, which will **not** match the required format. The `ticker` field **must** match your bot's YAML `crypto_stablecoin_pair` (e.g., `BTC/USDT`, `ETH/USDT`) exactly, or the signal will be rejected. |
| `action` | Yes | `buy` or `sell` |
| `timestamp` | Yes | Unix timestamp (seconds or ms) or ISO 8601 string — **must be the moment the signal fired**, see [Replay protection](#replay-protection) |
| `order_size` | Yes | Amount to trade — percentage (0–100) or exact quantity depending on `order_size_type` |
| `order_size_type` | No | `percentage` (default) or `quantity` |
| `alert_name` | No | Optional label shown in logs and notifications |
| `dry_run` | No | `true` to simulate without executing — useful for testing |

> **Tip:** Each bot's dashboard card has a **Webhook Details** pane showing the exact endpoint URL, a ready-to-use cURL example, and a live test button — the easiest way to verify your setup without leaving the UI.

### Webhooks must use HTTPS

The `api_key` travels **inside the request body**. Delivered over plain HTTP it is readable
by anyone on the network path, who can then place orders of their own — replay protection
does not help against that, since they can compose a brand-new signal rather than repeat
an old one. Webhooks are therefore refused with `403` unless they arrived over TLS.

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

