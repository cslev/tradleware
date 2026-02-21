# Tradleware — Current State & Active Goals

> Last updated: Feb 2026

---

## Current State

- **v2.1** is the latest stable release (Nov 2025)
  - Full crypto support: OKX, Independent Reserve, Crypto.com
  - Web dashboard with real-time logs, multi-bot support, mobile-friendly UI
  - Webhook-driven buy/sell with `order_size` percentage param
  - Gotify push notifications
  - Fiat → stablecoin convert functionality

- **IBKR (stock trading) integration is in active development**
  - Files: `src/traders/stock/ibkr_trader.py`, `src/traders/stock/base_stock_trader.py`
  - Separate docker-compose: `docker-compose.ibkr.yml`
  - Setup guide: `IBKR_SETUP.md`

---

## Active Goals / What We're Working On

- [ ] Complete IBKR trader integration (Interactive Brokers stock trading via TWS/Gateway API)
- [ ] Ensure IBKR trader follows the same patterns as crypto traders (base class, test scripts, webhook support)
- [ ] Expand exchange support as needed
- [ ] Maintain pylint 10.00/10 score across all new code

---

## Future Goals

### Limit Order Support
- Order execution strategy logic (`maker_limit`) is partially implemented in individual trader classes (OKX, IR, Crypto.com)
- The webhook handler currently only routes to market orders — it needs to accept and pass through `order_execution_strategy` from the payload
- Required changes:
  - Add `order_execution_strategy` field to the webhook payload (default: `"market"`)
  - Pass it through to `create_order()` in `app.py`
  - Validate it against `VALID_ORDER_TYPES` in the base class
  - Verify each trader's `maker_limit` path is complete and tested

### Market hours
 - currently, it is hardcoded for the US/Eastern time, later it should be coming from an env var as other stock exchanges (e.g., SG, HK, EUR) has different hours,
 yet they are accessible via the same brokerage (e.g., IBKR)
### Public Project Website
- A dedicated landing/documentation site for Tradleware
- Goals: explain the project, showcase features, link to Docker Hub and GitHub
- Tech TBD (static site preferred — e.g. GitHub Pages, Hugo, or plain HTML)

### Trading Strategy Development
- Develop and document ready-to-use TradingView Pine Script strategies
- Current strategies are in `src/strategies/` (Bollinger Band, Gaussian Channel, Supertrend, Bull Market Support Band)
- Goal: expand the library and add usage instructions per strategy
