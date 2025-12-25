# Interactive Brokers Gateway Configuration

## Environment Variables

Add these to your `.env` file in the project root:

```bash
# IBKR Gateway Connection
IBKR_GATEWAY_HOST=127.0.0.1
IBKR_GATEWAY_PORT=8888  # extrange/ibkr image always uses 8888

# IBKR Account Credentials
IBKR_USERNAME=your_ibkr_username
IBKR_PASSWORD=your_ibkr_password

# Trading Mode: 'paper' or 'live'
# This determines which IBKR servers the gateway connects to
IBKR_TRADING_MODE=paper

# Read-only mode (true = no orders will be placed, just data access)
IBKR_READ_ONLY=false
```

**Important:** With the `extrange/ibkr` Docker image:
- **Always connect to port 8888** - the image handles internal routing
- The `IBKR_TRADING_MODE` setting determines which IBKR servers the gateway authenticates with (paper vs live)
- You don't need to change ports when switching modes - port 8888 works for both
- The image automatically forwards to the correct internal port (4001 for live, 4002 for paper)

## Quick Start

### 1. Start IB Gateway (Paper Trading)

```bash
docker-compose -f docker-compose.ibkr.yml up -d
```

### 2. Check Gateway Status

```bash
docker-compose -f docker-compose.ibkr.yml logs -f
```

Wait for the message indicating successful connection to IBKR servers.

### 3. Monitor Gateway (Optional)

Access the gateway web interface via noVNC at `http://localhost:6080` - no VNC client needed, works in any web browser.

### 4. Test Connection

Your Tradleware app will automatically connect to port 8888 regardless of trading mode:
- **Paper trading** (`IBKR_TRADING_MODE=paper`): Gateway internally routes to IBKR paper servers
- **Live trading** (`IBKR_TRADING_MODE=live`): Gateway internally routes to IBKR live servers

## Switching to Live Trading

1. Update `.env`:
   ```bash
   IBKR_TRADING_MODE=live
   ```

2. Restart gateway:
   ```bash
   docker-compose -f docker-compose.ibkr.yml restart
   ```

3. **That's it!** Your bots continue connecting to port 8888 - the gateway handles the internal routing to live servers

## Stopping Gateway

```bash
docker-compose -f docker-compose.ibkr.yml down
```

## Troubleshooting

### Connection Refused
- Ensure gateway is fully started (check logs)
- Wait 30-60 seconds after starting for initialization
- Check firewall settings

### Authentication Failed
- Verify IBKR_USERNAME and IBKR_PASSWORD are correct
- Ensure you have an IBKR paper trading account enabled
- Check IBKR website for account status

### Gateway Keeps Restarting
- Check logs: `docker-compose -f docker-compose.ibkr.yml logs`
- Verify credentials are valid
- Ensure no other IB Gateway/TWS is running

## Security Notes

- Never commit your `.env` file with real credentials
- noVNC (port 6080) allows browser-based gateway monitoring without VNC client
- Consider using `IBKR_READ_ONLY=true` for testing
- For production, restrict ports to localhost only in docker-compose.yml
