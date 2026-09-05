# Interactive Brokers Gateway Configuration

## Configuration Files

IBKR configuration is split across two files in `bot_configs/stock/`:

| File | Purpose |
|---|---|
| `bot_configs/stock/ibkr.yaml` | Tradleware bot config — gateway `host`/`port`, per-bot settings (account, symbol, API key, market hours) |
| `.env.ibkr` | IB Gateway Docker container credentials — IBKR login, trading mode, read-only flag. **Not read by Tradleware** — only used by `docker-compose.ibkr.yml` |

Both files are gitignored. Use the provided `.example` files as templates:

```bash
cp bot_configs/stock/ibkr.yaml.example bot_configs/stock/ibkr.yaml
cp .env.ibkr.example                   .env.ibkr
```

### `.env.ibkr` (project root)

```bash
# Your IBKR login credentials
USERNAME=your_ibkr_username
PASSWORD=your_ibkr_password

# Trading mode: 'paper' or 'live'
IBC_TradingMode=paper

# Read-only API mode: 'yes' = no orders will be placed (safer for testing)
IBC_ReadOnlyApi=no
```

### `bot_configs/stock/ibkr.yaml` (gateway section)

```yaml
gateway:
  host: "ib_gateway"   # Docker container name — resolves via tradleware-network DNS
  port: 8888           # extrange/ibkr image always uses 8888 for both paper and live

bots:
  - id: myapplebot
    account_id: "U1234567"
    symbol: "AAPL"
    extended_hours: false
    fractional_shares: false
    account_currency: "USD"   # optional, defaults to USD — see below
    tradleware_api_key: "your_tradleware_api_key_here"
```

### Non-US instruments

Two settings decide **what the account holds**; two decide **what the instrument is**.
They default to a US-listed, USD setup, so a plain `AAPL` bot needs none of them.

| Field | Default | Meaning |
|---|---|---|
| `client_id` | auto-assigned | Unique IB client id. IB refuses two connections sharing one, so a duplicate leaves a bot unable to connect. Assigned automatically when absent — but the number follows config order, so pin it if you want it stable |
| `account_currency` | `USD` | Which `TotalCashValue` row orders are sized against |
| `trading_currency` | same as `account_currency` | The currency the instrument trades in |
| `exchange` | `SMART` | Routing venue; `SMART` lets IB choose |
| `primary_exchange` | *(unset)* | Disambiguates a cross-listed ticker |

`SMART` is right for US listings and ambiguous for anything cross-listed — the same
ticker exists on several European venues in different currencies, and IB may qualify a
listing you did not mean. Name the venue when that matters:

```yaml
  - id: myetfbot
    account_id: "U1234567"
    symbol: "VWCE"
    account_currency: "EUR"
    trading_currency: "EUR"
    primary_exchange: "AEB"   # Euronext Amsterdam
    fractional_shares: false  # most UCITS ETFs are not fractional-eligible at IBKR
    tradleware_api_key: "your_tradleware_api_key_here"
```

`trading_currency` follows `account_currency` unless set, so a EUR account buying a EUR
instrument only needs the one line. Set them apart when they genuinely differ — a USD
account buying a EUR instrument, where IB converts or lends.

**`account_currency`** names which cash balance the bot sizes orders against. IB reports
`TotalCashValue` once per currency an account holds, so a multi-currency account returns
several rows. Leave it out for a USD account. Set it to match the currency the instrument
trades in — sizing against USD while buying a EUR-denominated ETF reads a balance that has
nothing to do with the order. If the configured currency is not among those IB reports,
the bot logs a warning naming the ones it did return, rather than silently sizing
against zero.

**Important:** With the [`cslev/ibkr-docker`](https://github.com/cslev/ibkr-docker) Docker image:
- **Always connect to port 8888** — the image handles internal routing
- The `IBC_TradingMode` setting determines which IBKR servers the gateway authenticates with
- The image automatically forwards to the correct internal port (4001 for live, 4002 for paper)
- No port change is needed when switching between paper and live modes

---

## Quick Start

### 1. Create your config files

```bash
cp bot_configs/stock/ibkr.yaml.example bot_configs/stock/ibkr.yaml
cp .env.ibkr.example                   .env.ibkr
```

Edit both files: credentials in `.env.ibkr`, bot settings in `ibkr.yaml`.

### 2. Create the shared network

Tradleware and IB Gateway talk over a Docker network named `tradleware-network`, which is
how `host: "ib_gateway"` in `ibkr.yaml` resolves. Create it **before bringing up either
stack** — neither one owns it, and either may be started first:

```bash
docker network create tradleware-network
```

Once per machine; it survives `docker-compose down`. If you already created it during
[Getting Started](GETTING_STARTED.md), skip this. Starting the gateway without it fails
with:

```
network tradleware-network declared as external, but could not be found
```

### 3. Start IB Gateway

```bash
docker-compose -f docker-compose.ibkr.yml up -d
```

### 4. Check Gateway Status

```bash
docker-compose -f docker-compose.ibkr.yml logs -f
```

Wait for the message indicating successful connection to IBKR servers.

### 5. Monitor Gateway (Optional)

Access the gateway web interface via noVNC at `http://localhost:6080` — no VNC client needed, works in any browser.

### 6. Start Tradleware

```bash
docker-compose up -d
```

Tradleware reads `bot_configs/stock/ibkr.yaml` and automatically connects to IB Gateway at `host:port`.

### Running Tradleware outside Docker

For development, Tradleware often runs on the host while IB Gateway stays in its
container. The container name `ib_gateway` does not resolve from the host, so the log
fills with:

```
Failed to connect to IB Gateway: [Errno -2] Name or service not known
```

`docker-compose.ibkr.yml` publishes `8888:8888`, so point the bot at the host instead:

```yaml
gateway:
  host: "127.0.0.1"    # was "ib_gateway" — container DNS only works from inside Docker
  port: 8888
```

No network is needed in this arrangement; the published port is enough. Change it back
to `ib_gateway` before running Tradleware in Docker again.

---

## Switching to Live Trading

1. Update `.env.ibkr`:
   ```bash
   IBC_TradingMode=live
   ```

2. Restart the gateway:
   ```bash
   docker-compose -f docker-compose.ibkr.yml restart
   ```

3. **That's it!** Your bots continue connecting to port 8888 — the gateway handles routing to live servers.

---

## Stopping Gateway

```bash
docker-compose -f docker-compose.ibkr.yml down
```

---

## Troubleshooting

### `network tradleware-network declared as external, but could not be found`

The gateway was started before anything created the network. Run
`docker network create tradleware-network`, then bring the stack up again.

### Connection Refused
- Ensure gateway is fully started (check logs) — allow 30–60 seconds after starting
- Check firewall settings

### Authentication Failed
- Verify `USERNAME` and `PASSWORD` in `.env.ibkr` are correct
- Ensure paper trading is enabled on your IBKR account if using `IBC_TradingMode=paper`
- Check IBKR website for account status

### Gateway Keeps Restarting
- Check logs: `docker-compose -f docker-compose.ibkr.yml logs`
- Verify credentials are valid
- Ensure no other IB Gateway or TWS instance is running on the same machine

---

## Security Notes

- Never commit `.env.ibkr` — it is gitignored; keep it on your server only
- Set `IBC_ReadOnlyApi=yes` for testing to prevent accidental order placement
- noVNC (port 6080) allows browser-based gateway monitoring without a VNC client
- For production, consider restricting port 6080 to localhost only in `docker-compose.ibkr.yml`

