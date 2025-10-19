# Changelog

All notable changes to Tradleware will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-10-19

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
