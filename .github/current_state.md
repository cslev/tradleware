# Tradleware — Current State & Active Goals

> Last updated: 13 Apr 2026 (session 6)

---

## Current State

**v3.0.1b under development**

- All crypto traders (OKX, Crypto.com, IR) fully operational.
- IBKR stock trader operational: webhook-driven buy/sell, `dry_run`, connection state tracking.
- IBKR order account ID now explicitly set on every order — fixes gateway rejection with sub-accounts.
- IBKR health-check loop: background task probes connections every 30 min; Gotify notification on loss/restore.
- Server public IP displayed on dashboard footer (fetched once at startup).
- Informational IBKR error codes (2107, 2109, 2119, 10167) silenced to DEBUG — no Gotify noise.
- pylint score: **10.00/10** across all of `src/`
- Multi-arch Docker image (`amd64` + `arm64`) last published as `cslev/tradleware:v3.0`.

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

### 13 Apr 2026 (session 6)
- **IBKR order account fix**: `order.account = self.account_id` now set before every `placeOrder` call; fixes rejection when gateway has sub-accounts or FA setup
- **IBKR health-check loop**: background `asyncio` task in `app.py`; probes each IBKR bot every `IBKR_HEALTH_CHECK_INTERVAL_S` seconds (default 1800s); Gotify error on loss/still-down, Gotify success on restore, debug on healthy
- **Informational IBKR codes silenced**: error codes 2107, 2109, 2119, 10167 added to the debug-only list — no more spurious Gotify warnings during out-of-hours gateway startup
- **Server public IP on dashboard**: fetched once at startup via `api.ipify.org`; displayed in dashboard footer with globe icon; useful for exchange API key IP whitelist verification
- **Tailwind CSS rebuilt**: `output.css` regenerated to include new utility classes
- **pylint**: 10.00/10 maintained

### 28 Mar 2026 (session 5)
- **Starlette ≥0.36 `TemplateResponse` fix**: updated `app.py` render calls to new `TemplateResponse(request, name, context)` signature; fixes `TypeError: unhashable type: 'dict'` crash on all page loads
- **Logger: function name + line number**: format string updated to include `%(funcName)s-(line %(lineno)d)` in both console and file handlers
- **Logger: uncaught exception capture**: `_install_global_excepthook()` routes all unhandled exceptions to the log file via `sys.excepthook`
- **Docker Compose env var quoting fix**: removed inner `"` quotes from `environment:` values; these are passed literally by Compose, causing `ib_gateway` crash loop
- **README restructured**: linear "Getting Started" 4-step flow; webhook payload section with JSON example; `BUILD.md` pointer; `tradleware_v3.png` screenshot
- **`.gitignore`**: added `docker-compose.pi.yml` pattern for local compose overrides
- **Multi-arch image pushed**: `cslev/tradleware:latest` + `cslev/tradleware:v3.0` (amd64 + arm64) on Docker Hub

### 28 Mar 2026 (session 4)
- **Config validation consolidated**: `config_loader._validate_bot()` now rejects empty/null field values (not just missing keys); redundant check removed from `BaseCryptoTrader.__init__`; `BaseStockTrader` gets the same protection via the loader
- **Market hours configurable per bot**: `base_stock_trader` reads `market_timezone`, `market_open`, `market_close`, `pre_market_open`, `after_hours_close` from YAML config (all optional, US Eastern defaults); `ibkr.yaml.example` documents optional fields with SGX example
- **pylint**: 10.00/10 maintained

### 21 Mar 2026 (session 3)
- **YAML-based bot config system**: replaced `ACTIVE_TRADING_CONFIGS` env var + `{IDENTIFIER}_{EXCHANGE}_*` env vars with per-exchange YAML files in `bot_configs/crypto/` and `bot_configs/stock/`
- **`src/misc/config_loader.py`** added: `get_bot_configs()` scans YAML files and returns a flat list of typed config dicts
- **All trader `__init__` signatures updated**: now accept `config: dict` instead of individual `account_identifier`, `exchange_id` etc. parameters — `BaseCryptoTrader`, `BasStockTrader`, all subclasses
- **`app.py` lifespan rewritten**: iterates `get_bot_configs()` instead of parsing `ACTIVE_TRADING_CONFIGS`; `traders` dict key is now lowercase bot `id` (e.g. `myokxbot`) instead of `MYBOT_OKX`
- **`.env` cleaned up**: all per-bot vars and `ACTIVE_TRADING_CONFIGS` removed; only Tradleware-level settings remain
- **`docker-compose.yml`**: added `bot_configs/` volume mount (read-only) into the container
- **`copilot-instructions.md`**: updated env var convention sections → YAML config sections throughout
- **pylint**: 10.00/10 maintained
- **Webhook `trader_id`**: now lowercase bot `id` from YAML (e.g. `"myokxbot"`) — update TradingView alert payloads accordingly

### 21 Mar 2026 (session 2)
- **Fractional shares fully implemented** across `ibkr_trader.py`, `base_stock_trader.py`, `app.py`, `.env.example` (Future Goals goal ticked off)
- **IBKR placeholders removed**: `fetch_account_value`, `cancel_order`, `fetch_open_orders` now raise `NotImplementedError` with explanatory docstrings instead of silent falsy returns
- **State doc**: Active Goals merged into Future Goals; limit order goal annotated with context
- **UI — crypto card live price**: Added `/price/{trader_id}` FastAPI endpoint (fallback chain: `last` → `close` → `bid` → `ask`); `refreshBalance()` JS fetches price separately and renders it inside the Account Balances blue box
- **UI — misc**: Webhook URL row removed from all bot cards; Fractional Shares enabled/disabled indicator added to stock cards
- **pylint**: 10.00/10 maintained throughout

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
