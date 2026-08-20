# Getting Started with Tradleware

Installing, configuring and running Tradleware. For the webhook payload schema and the
security requirements signals must meet, see **[WEBHOOKS.md](WEBHOOKS.md)**. For building
from source, see **[BUILD.md](BUILD.md)**.

---

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
| `WEBHOOK_REQUIRE_HTTPS` | No | `true` | Refuse webhooks not delivered over TLS — requires `TRUSTED_PROXIES`, see [Webhooks must use HTTPS](WEBHOOKS.md#webhooks-must-use-https) |
| `TRADER_LOCK_TIMEOUT_S` | No | `60` | How long a request waits for a bot that is already executing before returning `503` |
| `WEBHOOK_FAILURE_LIMIT` | No | `20` | Failed webhook authentications from one address per window before it gets `429` |
| `WEBHOOK_FAILURE_WINDOW_S` | No | `60` | Length of that window, in seconds |
| `WEBHOOK_REJECTION_SUMMARY_S` | No | `300` | How often repeated webhook rejections are collapsed into one summary line |
| `WEBHOOK_MAX_AGE_S` | No | `300` | How stale a signal's timestamp may be before it is rejected (minimum `30`) — see [Replay protection](WEBHOOKS.md#replay-protection) |
| `TRUSTED_IPS` | No | — | Comma-separated IPs that bypass authentication |
| `TRUSTED_PROXIES` | No | — | Comma-separated IPs/CIDRs of reverse proxies allowed to report the client IP — see below |
| `LOG_REFRESH_INTERVAL_MS` | No | `5000` | Dashboard log refresh interval (ms) |
| `LOG_LEVEL` | No | `10` | Min log level: 10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR |
| `LOG_MAX_BYTES` | No | `10485760` | Rotate the log file once it reaches this size (10 MB) |
| `LOG_BACKUP_COUNT` | No | `5` | Rotated files to keep — with the default size, a ~60 MB ceiling |
| `LOG_COMPRESS_ROTATED` | No | `true` | Gzip rotated files (~8x), bringing that ceiling to ~16 MB. Read them with `zcat`/`zgrep` |
| `WEBHOOK_REPLAY_DB` | No | `src/logs/webhook_replay.json` | Where accepted signals are remembered across restarts |
| `UPDATE_CHECK_INTERVAL_S` | No | `21600` | Seconds between checks for a newer release (6h) |
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

> For different versions and architectures (including arm64 for Raspberry Pi), visit [Docker Hub](https://hub.docker.com/r/cslev/tradleware/tags).

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

