# Changelog

All notable changes to Tradleware will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v3.0] - 2026-03-28

### 📈 Stock Trading — IBKR Integration & Major Refactor
![Tradleware v3 Crypto.com](screenshots/tradleware_v3.png)

#### 🆕 Interactive Brokers (IBKR) Stock Trader
Tradleware now supports **stock trading** via Interactive Brokers — the first broker integration on the stock side.

- **Webhook-driven buy/sell** — same webhook format as crypto; send `buy`/`sell` signals from TradingView or any strategy source and Tradleware will place market orders through IB Gateway
- **`dry_run` support** — simulates orders without executing them; attempts a real balance fetch first and only falls back to simulated values (`$10k` / `10 shares`) when the gateway is genuinely unreachable
- **Fractional shares** — `fractional_shares: true/false` per bot in `ibkr.yaml`; the trader calculates fractional quantities when enabled (note: not all symbols support this — IBKR rejects unsupported ones)
- **Extended hours trading** — configurable per bot via `extended_hours: true/false`; when enabled, orders are placed outside regular market hours
- **Market hours awareness** — `is_market_open()`, `can_trade_now()`, `get_market_status()`, `get_time_until_market_opens()` helpers built into `BaseStockTrader`; the trader checks hours before placing any order
- **Configurable market hours per bot** — `market_timezone`, `market_open`, `market_close`, `pre_market_open`, `after_hours_close` can be set per bot in `ibkr.yaml` (all optional; default: US Eastern / NYSE hours); supports any IANA timezone string (e.g. `Asia/Singapore` for SGX)
- **Robust connection management** — `_sync_connection_state()` and `_handle_ib_exception()` detect and recover from silent IB Gateway disconnections; applied to all IB API call sites
- **Per-account multi-bot support** — each bot has its own `account_id`, `symbol`, and `tradleware_api_key` in `ibkr.yaml`; the shared `gateway` block configures the IB Gateway container (host, port, trading mode, VNC password)
- **Dockerized IB Gateway** — `docker-compose.ibkr.yml` provides a ready-to-use IB Gateway container alongside Tradleware using [`cslev/ibkr-docker:latest`](https://github.com/cslev/ibkr-docker) — a custom multi-arch image (amd64 + arm64/Raspberry Pi) with persistent TWS settings support; see `IBKR_SETUP.md` for full setup instructions
- **`BaseStockTrader`** defines the abstract contract for all future broker integrations; `IBKRTrader` is the first concrete implementation

#### 🏗️ Breaking Changes — YAML-based bot configuration
All per-bot settings have moved from environment variables into dedicated YAML files:
- `bot_configs/crypto/` — one file per exchange (e.g. `okx.yaml`, `cryptocom.yaml`, `ir.yaml`)
- `bot_configs/stock/` — one file per broker (e.g. `ibkr.yaml`)
- `ACTIVE_TRADING_CONFIGS` and all `{IDENTIFIER}_{EXCHANGE}_*` env vars removed from `.env`
- `.env` now only contains Tradleware-level settings (dashboard auth, logging, webhook path, Gotify)
- Each bot is defined by an `id` field (lowercase) used as `trader_id` in webhook payloads — **update your TradingView alert payloads accordingly**
- See `.yaml.example` files in `bot_configs/` for the full structure

#### New Features
- **`src/misc/config_loader.py`** — new module; `get_bot_configs()` scans YAML files and returns a flat list of typed config dicts; validates required fields (including empty/null value checks) at load time and skips invalid bots with a warning
- **Dashboard live crypto price** — new `/price/{trader_id}` endpoint; price is fetched separately alongside balance and displayed in the Account Balances panel on each crypto bot card
- **`app.py` lifespan rewritten** — iterates `get_bot_configs()` instead of parsing env vars; trader dict key is now the lowercase bot `id`
- **All trader `__init__` signatures updated** — now accept a single `config: dict` instead of individual positional parameters

#### Bug Fixes
- **IBKR connection state staleness** — `_sync_connection_state()` and `_handle_ib_exception()` applied to all IB API call sites to detect and recover from silent disconnections
- **`dry_run` simulation fallback** — real balance fetch is attempted first; simulated values only used when gateway is unreachable
- **9 additional bugs fixed** across all trader classes and `app.py` (order logging edge cases, balance fetch race conditions, precision handling)
- **Starlette ≥ 0.36 `TemplateResponse` API break** — updated both `index.html` and `login.html` render calls in `app.py` from the old `TemplateResponse(name, {"request": request, ...})` signature to the new `TemplateResponse(request, name, {...})` signature; fixes `TypeError: unhashable type: 'dict'` crash on every page load
- **Docker Compose environment variable quoting** — removed extraneous inner quotes from `environment:` values in compose files; Docker Compose passes them literally, causing `GATEWAY_OR_TWS must be either 'gateway' or 'tws': got '"gateway"'` crash loop in `ib_gateway`

### Improvements
- **Logger: function name and line number** — log format now includes `%(funcName)s-(line %(lineno)d)` for both the console (colored) and file handlers, making it significantly easier to trace log output back to the exact source location
- **Logger: uncaught exception capture** — `_install_global_excepthook()` installed on first `CustomLogger` instantiation; routes all unhandled exceptions through the logging system (CRITICAL level) so they appear in the log file as well as stdout; `KeyboardInterrupt` is passed through unchanged
- **README restructured for end users** — "Getting Started" replaces the old "Docker Deployment" section; linear 4-step flow (clone → configure bots → configure `.env` → run); no more build/source references in the main path; dev/build content lives in `BUILD.md`
- **README: webhook payload documented** — new "Webhook Payload" subsection with full JSON example and field reference table; pointer to dashboard Webhook Details pane
- **`docker-compose.pi.yml` gitignored** — personal/local compose override files are no longer tracked

---
#### Technical Improvements
- **Config validation consolidated** — `config_loader._validate_bot()` is the single source of truth for required-field checks; redundant validation block removed from `BaseCryptoTrader.__init__`; `BaseStockTrader` is protected via the same loader
- **IBKR unimplemented stubs** — `fetch_account_value()`, `cancel_order()`, `fetch_open_orders()` raise `NotImplementedError` with explanatory docstrings instead of returning silent falsy values
- **pylint**: 10.00/10 maintained across all of `src/`

---

## [v2.1] - 2025-11-01

### 🎉 New Exchange & Improvements
![Tradleware v2.1 Crypto.com](screenshots/tradleware_v2.1.png)
#### New Features
- **Crypto.com Exchange Support** - Full integration with Crypto.com exchange
  - Subaccount support for multi-account management
  - Market and maker limit order execution strategies
  - Full-amount conversion (fiat to stablecoin) support with fee buffer handling
  - Cost-based market buy orders with automatic fallback
  - **NOTE**: 
    - `order_size` param in the webhook should not be 100 as crypto.com needs buffer for fee, try 98 instead (or adjust as deem fit)
    - Crypto.com requires IP whitelisting for API-based trading! If you are behind a dynamic IP, you need to manually update the API setting accordingly!
- **Trader Test Scripts** - Added standalone test functionality to all trader classes
  - Run individual trader files directly to test basic functions (fetch balance, open orders, market listings)
  - Automatic configuration detection from ACTIVE_TRADING_CONFIGS
  - Example: `python src/traders/okx_trader.py`
- **UI Improvements**
  - Bot ID now displayed at bottom of exchange logo header
  - Convert FIAT-to-Stablecoin button automatically disabled when trading pair is UNSUPPORTED
  - Convert FIAT-to-Stablecoin button disabled when fiat equals stablecoin (no conversion needed)

#### Bug Fixes
- **Order Execution Logging** - Completely redesigned order success messages
  - Now shows meaningful trade information: "Bought X BTC for Y USDT @ avg price Z"
  - Replaced unhelpful "Status: None, Price: N/A, Amount: N/A" messages
  - Different messages for buy vs sell orders with actual filled amounts and costs
- **Crypto.com Market Buy Orders** - Fixed insufficient balance errors
  - Added 0.5% buffer for crypto/stablecoin trades to leave dust for exchange fees
  - Fiat-to-stablecoin conversions still use 100% of available balance (exchange handles fees)
  - Note: Crypto.com requires order sizes < 100% due to fee structure
- **CCXT Compatibility** - Fixed KeyError for 'createMarketBuyOrderRequiresPrice'
  - Added safety checks before calling createMarketBuyOrderWithCost
  - Sets required exchange options to prevent KeyErrors
  - Better exception handling with fallback to standard order methods
- **Mobile Responsiveness** - Bot ID positioning fixed in card headers
  - Changed from justify-between to justify-end for proper bottom alignment

#### Technical Improvements
- All three traders (OKX, IR, Crypto.com) now have consistent order logging
- Improved error messages for market buy conversions with detailed cost breakdown
- Better fallback logging shows original vs adjusted amounts for debugging

---

## [v2.0] - 2025-10-26

### 🎉 Major Release

![Tradleware v2.0 Dashboard](screenshots/tradleware_v2.png)

- UI layout bugfixes for multi-bot support
- Card redesign and neon/cyberpunk style improvements
- Tabbed dashboard view: summary, webhook details, logs
- Added support for Independent Reserve exchange
- Trading pair support check: logs available markets if configured pair is unsupported
- If trading pair is unsupported, bot shows flashing red 'UNSUPPORTED' text in dashboard
- Webhook now supports 'order_size' to buy/sell a percentage of available assets
- Added LOG_LEVEL environment variable to control general log verbosity (console and file logs)
- LOG_LEVEL and GOTIFY_LOG_LEVEL are now fully independent for logging and notification filtering
- Suppressed dashboard access logs for IP 127.0.0.1 to avoid log spam from Docker health checks
- Improved trusted IP logging: skips log entry for 127.0.0.1
- Updated documentation and .env files to clarify logging configuration

---

## [v1.1] - 2025-10-20

### 🎉 Minor Release
![Tradleware v1.1 Dashboard](screenshots/tradleware_v1.png)

- Mobile friendly look for dashboard and login page
- Smart detection of HTTPS secure access, including support for proxied setups and Cloudflare Tunnel (uses X-Forwarded-Proto header)

---

## [v1.0] - 2025-10-19

### 🎉 Initial Release

First public release of Tradleware - your privacy-first, self-hosted autotrading middleware!

### ✨ Features

#### Exchange Support
- **OKX Exchange Integration** - Full support for OKX exchange with subaccount management
  - Spot trading support
  - Market and maker limit order types
  - Automatic fiat-to-stablecoin conversion
  - Real-time balance fetching
  - Open order management

#### Web Dashboard
- **FastAPI-based Web UI** - Modern, responsive dashboard for bot monitoring
  - Real-time log streaming with color-coded messages
  - Live balance display with auto-refresh
  - Per-bot log filtering
  - Dark theme optimized interface
  - Mobile-friendly design

#### Webhook System
- **TradingView Integration** - Secure webhook endpoints for automated trading signals
  - Per-bot API key authentication
  - Configurable webhook paths for enhanced security
  - Signal validation (buy/sell actions)
  - Ticker symbol verification
  - Balance checking before order execution

#### Security & Privacy
- **Privacy by Design** - Your API keys never leave your server
  - Self-hosted deployment via Docker
  - Session-based authentication for dashboard
  - Trusted IP whitelist support
  - Configurable webhook paths
  - Per-bot API key isolation

#### Notifications
- **Gotify Integration** - Real-time push notifications
  - Configurable log level filtering
  - Trade execution alerts
  - Error and warning notifications
  - Custom logger with color-coded console output

#### Code Quality
- **Production-Ready Codebase**
  - Pylint score: 10.00/10
  - Comprehensive error handling
  - Full traceback logging for debugging
  - Type hints throughout
  - Async/await pattern for performance

### 📋 Supported Exchanges

- ✅ **OKX** (www.okx.com) - MAS-approved exchange

### 🐳 Deployment

- Docker and Docker Compose support
- Python 3.11+ compatibility
- Simple `.env` configuration
- Hot-reload development mode

### 📚 Documentation

- Comprehensive README with setup instructions
- Environment variable reference table
- TradingView webhook configuration guide
- Security best practices

### 🔧 Technical Stack

- **Backend**: FastAPI (async Python web framework)
- **Exchange Library**: CCXT (unified cryptocurrency exchange API)
- **Logging**: Custom color-coded logger with Gotify support
- **Frontend**: Tailwind CSS, vanilla JavaScript
- **Containerization**: Docker, Docker Compose

---

## Future Roadmap

### Planned Features
- 🔄 Additional crypto exchange support (e.g., CoinbasePro)
- 📊 Add stock exchange support (e.g. Moomoo, IBKR)
- 🔔 Multiple notification channels (Telegram, Discord, Email)

### Community Contributions Welcome!
Have ideas or want to contribute? Check out our [GitHub repository](https://github.com/yourusername/tradleware)!

---

**Note**: This is the first stable release. While tested in production, always start with small amounts and monitor your bots closely. Cryptocurrency trading involves risk. Never invest more than you can afford to lose.

[1.0.0]: https://github.com/yourusername/tradleware/releases/tag/v1.0.0
