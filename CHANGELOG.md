# Changelog

All notable changes to Tradleware will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
