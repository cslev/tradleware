# Tradleware — Current State & Active Goals

> Last updated: 21 Mar 2026

---

## Current State

**v2.2 in development**

- All crypto traders (OKX, Crypto.com, IR) fully layered: `create_order` has validation, market/balance context, sizing, and execution in all three. `_safe_amount_to_precision` in base class guards against exchange precision quirks. Real-market and dry_run tested.
- IBKR stock trader (Layers 1–4) operational: webhook-driven buy/sell working, `dry_run` supported, connection state tracking reliable.
- pylint score: **10.00/10** across all of `src/`

---

## Future Goals

### IBKR — unimplemented methods (none block current production use)
- [ ] `fetch_account_value()` — raises `NotImplementedError`. Cash is already fetched inline inside `create_order` via `accountSummaryAsync()`. Only needed for a future dashboard Summary tab.
- [ ] `cancel_order()` — raises `NotImplementedError`. All orders are market orders that fill immediately; nothing to cancel. Only becomes relevant if limit orders are ever added.
- [ ] `fetch_open_orders()` — raises `NotImplementedError`. Useful for dashboard visibility into pending orders only.

### Limit Order Support via webhook
- `order_execution_strategy` is hardcoded to `'market'` in `app.py` (not read from webhook payload)
- Base class `_calculate_order_size` already supports `maker_limit`; only the webhook wiring is missing
- Only worth implementing when a strategy produces specific entry price targets, not just buy/sell signals
- Required: add optional `order_execution_strategy` field to webhook payload, pass through in `app.py` to `create_order()`

### IBKR Stock Trader Improvements
- [ ] **Fractional share support**: Currently all quantity calculations use `int()` truncation (whole shares only). IBKR natively supports fractional shares — just pass a `float` to `MarketOrder`/`LimitOrder`. Implementation plan:
  - Add per-bot env var `{IDENTIFIER}_IBKR_FRACTIONAL_SHARES=true/false` (default: false)
  - Load it in `IBKRTrader.__init__` as `self.fractional_shares`
  - In `BaseStockTrader._calculate_order_size`: if `fractional_shares=True`, use `round(amount / price, 4)` instead of `int()`
  - In `app.py` stock branch: skip the hard `int(quantity)` cast when `fractional_shares=True`
  - Update `_validate_order_params`: `quantity` type hint `Optional[int]` → `Optional[float]`
  - Note: Not all stocks/ETFs support fractional shares on IBKR — verify per symbol before enabling live

### Market hours timezone from env var
- Currently hardcoded for US/Eastern (`America/New_York`) in `base_stock_trader.py`
- Should come from an env var to support other exchanges (SGX, HKEX, Euronext) accessible via the same IBKR brokerage

### Demo / Paper Trading Support (OKX)
- OKX paper trading lacks functions Tradleware depends on (e.g. fetch_balance). Demo trading support will not be developed. Use `dry_run` in webhooks for testing; note that actual exchange API responses are not captured in dry_run mode.

### Public Project Website
- A dedicated landing/documentation site for Tradleware
- Goals: explain the project, showcase features, link to Docker Hub and GitHub
- Tech TBD (static site preferred — e.g. GitHub Pages, Hugo, or plain HTML)

### Trading Strategy Development
- Develop and document ready-to-use TradingView Pine Script strategies
- Current strategies are in `src/strategies/` (Bollinger Band, Gaussian Channel, Supertrend, Bull Market Support Band)
- Goal: expand the library and add usage instructions per strategy

---

## Session History

### 21 Mar 2026
- **9-bug audit** across all trader classes + `app.py`: all fixed, committed `56a2d2f`
- **IBKR `is_connected` staleness fix** (`fb03783`): added `_sync_connection_state()` and `_handle_ib_exception()`; applied to `fetch_positions()` and `get_market_price()`
- **Connection state consistency pass**: `_handle_ib_exception` applied to all remaining IB API call sites (`fetch_positions` second `accountSummaryAsync`, Layer 3 live balance fetch, Layer 4 `placeOrder` handler)
- **dry_run $100k bug fixed**: Layer 3 dry_run branch now tries real IB balance first, falls back to simulated $10k/10 shares only if gateway is unreachable
- **pylint restored to 10.00/10**: fixed trailing whitespace (27 lines), misplaced `import math`, missing `raise ... from exc`, `too-many-nested-blocks` and `too-many-lines` in `app.py`
- **Fractional shares**: documented in Future Goals (not implemented — user deferred)
- **Git hygiene**: removed 4 accidentally tracked files (pine scripts, drawio backups) from git history

### 1 Mar 2026
- IRTrader fully layered and tested (Layers 1–4). Precision bugs solved for markets with `None` precision.
- `_safe_amount_to_precision` added to `BaseCryptoTrader`.
- Dry run + live buy/sell percent/quantity verified (PEPE/SGD). OKX/Crypto.com unchanged.

### 21 Feb 2026
- `_calculate_order_size` (Layer 3) implemented in `BaseCryptoTrader`, applied to OKX
- `pytz` removed — replaced with stdlib `zoneinfo`
- Webhook handler hardened: `order_size_type` + `order_size` now required (HTTP 400 on missing/invalid)
- Dashboard cURL examples updated with `order_size_type` and `dry_run` fields
