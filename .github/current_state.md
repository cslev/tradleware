# Tradleware — Current State & Active Goals

> Last updated: 1 Mar 2026

---

## Current State

**v2.2 in development (1 Mar 2026)**
  - All crypto traders (OKX, Crypto.com, IR) now fully layered (`create_order` parity: validation, market/balance/context, sizing, execution)
  - Sell/buy, quantity/percentage, dry_run/live tested on IR for both precision=None/defined; no more bugs
  - _safe_amount_to_precision in base class guards all order paths against exchange precision quirks (esp. IR)
  - No breaking changes for OKX/Crypto.com; all codepaths robust for future additions
  - Real-market and DRY RUN tested: PEPE/SGD, percent and quantity, buy/sell
  - IBKR (stock) integration underway (new base structure, Docker)

---

## Active Goals / What We're Working On

### `create_order` refactor (crypto traders)

### `create_order` layering and precision (crypto traders)
- [x] Layers 1–4 (validation, market/balance, sizing, execution) now applied to: OKX, Crypto.com, IR
- [x] _safe_amount_to_precision in base class (fixes IR/precision edge cases everywhere)
- [x] All buy/sell percent/quantity (live and dry_run) scenarios tested, OKX/Crypto.com behaviour unchanged, IR now robust

### Other active
- [ ] Complete IBKR trader integration (Interactive Brokers stock trading via TWS/Gateway API)
- [ ] Ensure IBKR trader follows the same patterns as crypto traders (base class, test scripts, webhook support)
- [ ] Maintain pylint 10.00/10 score across all new code

---

## Completed This Session (1 Mar 2026)

- **IRTrader fully layered and tested**: All logic now matches base (Layers 1–4). Precision bugs fully solved on markets with None precision. Dry run + live buy/sell/percent/quantity verified (e.g., PEPE/SGD). OKX/Crypto.com continue to work as before.

---

## Future Goals

### IBKR Stock Trader Improvements
- [ ] Implement fetch_account_value to return actual account, cash, and buying power information instead of a placeholder.
- [ ] Implement cancel_order and fetch_open_orders in IBKRTrader; currently these methods are placeholders and always return dummy values. Proper cancelation/open tracking is necessary for robustness.
- [ ] **Fractional share support**: Currently all quantity calculations use `int()` truncation (whole shares only). IBKR natively supports fractional shares — just pass a `float` to `MarketOrder`/`LimitOrder`. Implementation plan:
  - Add per-bot env var `{IDENTIFIER}_IBKR_FRACTIONAL_SHARES=true/false` (default: false)
  - Load it in `IBKRTrader.__init__` as `self.fractional_shares`
  - In `BaseStockTrader._calculate_order_size`: if `fractional_shares=True`, use `round(amount / price, 4)` instead of `int()`
  - In `app.py` stock branch: remove the hard `int(quantity)` cast when `fractional_shares=True`
  - Update `_validate_order_params`: `quantity` type hint `Optional[int]` → `Optional[float]`
  - Note: Not all stocks/ETFs support fractional shares on IBKR — check before using live


### Demo / Paper Trading Support (OKX)
- OKX paper trading requires lacks of many functions Tradleware uses, like fetch balance, which makes demo trading via Tradleware itself useless. API supports actual trade execution, but cannot see the balances, or even convert fiat to stable coin. Hence, demo trading support is not going to be developed to tradleware. Use 'dry-run' in the webhooks to test functionality, altough certain API messages from the exchange would not be captured as dry-run stops before execution.

### Limit Order Support
- Order execution strategy logic (`maker_limit`) is fully implemented in the base class (`_calculate_order_size`)
- The webhook handler does **not** yet accept or pass through `order_execution_strategy` — still hardcoded to `'market'` in `app.py`
- Required changes to complete:
  - Add `order_execution_strategy` field to the webhook payload (optional, default: `"market"`)
  - Pass it through to `create_order()` in `app.py`
  - Add to webhook JSON examples in dashboard once wired up

### Market hours
- Currently hardcoded for US/Eastern (`America/New_York`) in `base_stock_trader.py`
- Should come from an env var — other exchanges (SGX, HKEX, Euronext) have different hours but are accessible via the same IBKR brokerage

### Public Project Website
- A dedicated landing/documentation site for Tradleware
- Goals: explain the project, showcase features, link to Docker Hub and GitHub
- Tech TBD (static site preferred — e.g. GitHub Pages, Hugo, or plain HTML)

### Trading Strategy Development
- Develop and document ready-to-use TradingView Pine Script strategies
- Current strategies are in `src/strategies/` (Bollinger Band, Gaussian Channel, Supertrend, Bull Market Support Band)
- Goal: expand the library and add usage instructions per strategy


---

## Current State

- **v2.1** is the latest stable release (Nov 2025)
  - Full crypto support: OKX, Independent Reserve, Crypto.com
  - Web dashboard with real-time logs, multi-bot support, mobile-friendly UI
  - Webhook-driven buy/sell with `order_size` + `order_size_type` params (both now required)
  - Gotify push notifications
  - Fiat → stablecoin convert functionality

- **IBKR (stock trading) integration is in active development**
  - Files: `src/traders/stock/ibkr_trader.py`, `src/traders/stock/base_stock_trader.py`
  - Separate docker-compose: `docker-compose.ibkr.yml`
  - Setup guide: `IBKR_SETUP.md`

---

## Active Goals / What We're Working On

### `create_order` refactor (crypto traders)
Layered refactor of `create_order` to push shared logic into `BaseCryptoTrader`. Progress on OKX:

- [x] **Layer 1** — `_validate_order_params` extended: added `order_execution_strategy` + `dry_run` validation, removed dead `allow_both_none` param, removed duplicate range check
- [x] **Layer 2** — `_resolve_market_and_balance(symbol)` added to base class; applied to OKX
- [x] **Layer 3** — `_calculate_order_size(...)` added to base class (quantity mode + spend% mode, all strategies); applied to OKX
- [x] **Layer 4** — OKX's `createMarketBuyOrderWithCost` execution block stays inline in `create_order` — it is exchange-specific and is the core job of the method; no extraction needed
- [ ] **Layer 5** — Apply all layers to IRTrader and CryptocomTrader (bring them to parity: add `quantity`, `dry_run`, call `_validate_order_params`, `_resolve_market_and_balance`, `_calculate_order_size`; remove `spend_percentage=1.0` default)

### Other active
- [ ] Complete IBKR trader integration (Interactive Brokers stock trading via TWS/Gateway API)
- [ ] Ensure IBKR trader follows the same patterns as crypto traders (base class, test scripts, webhook support)
- [ ] Maintain pylint 10.00/10 score across all new code

---

## Completed This Session (21 Feb 2026)

- `_calculate_order_size` implemented in base class and applied to OKX (Layer 3)
- Layer banner comments added to OKX's `create_order` for all implemented layers
- `pytz` dependency removed — replaced with stdlib `zoneinfo` in `base_stock_trader.py` and `requirement.txt`
- Webhook handler hardened: `order_size_type` and `order_size` are now required fields (HTTP 400 on missing/invalid); all silent fallbacks removed
- Webhook JSON examples and cURL examples in dashboard updated: added `order_size_type` and `dry_run` fields
- Dashboard webhook `<pre>` blocks made scrollable with configurable max-height

---

## Future Goals

### Limit Order Support
- Order execution strategy logic (`maker_limit`) is fully implemented in the base class (`_calculate_order_size`)
- The webhook handler does **not** yet accept or pass through `order_execution_strategy` — still hardcoded to `'market'` in `app.py`
- Required changes to complete:
  - Add `order_execution_strategy` field to the webhook payload (optional, default: `"market"`)
  - Pass it through to `create_order()` in `app.py`
  - Add to webhook JSON examples in dashboard once wired up

### Market hours
- Currently hardcoded for US/Eastern (`America/New_York`) in `base_stock_trader.py`
- Should come from an env var — other exchanges (SGX, HKEX, Euronext) have different hours but are accessible via the same IBKR brokerage

### Public Project Website
- A dedicated landing/documentation site for Tradleware
- Goals: explain the project, showcase features, link to Docker Hub and GitHub
- Tech TBD (static site preferred — e.g. GitHub Pages, Hugo, or plain HTML)

### Trading Strategy Development
- Develop and document ready-to-use TradingView Pine Script strategies
- Current strategies are in `src/strategies/` (Bollinger Band, Gaussian Channel, Supertrend, Bull Market Support Band)
- Goal: expand the library and add usage instructions per strategy
