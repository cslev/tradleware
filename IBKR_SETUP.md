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

### 2. Start IB Gateway

```bash
docker-compose -f docker-compose.ibkr.yml up -d
```

### 3. Check Gateway Status

```bash
docker-compose -f docker-compose.ibkr.yml logs -f
```

Wait for the message indicating successful connection to IBKR servers.

### 4. Monitor Gateway (Optional)

Access the gateway web interface via noVNC at `http://localhost:6080` — no VNC client needed, works in any browser.

### 5. Start Tradleware

```bash
docker-compose up -d
```

Tradleware reads `bot_configs/stock/ibkr.yaml` and automatically connects to IB Gateway at `host:port`.

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

