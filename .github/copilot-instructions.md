# Tradleware — Copilot Context

## What is Tradleware?

**Tradleware** is a free, open-source autotrading middleware built in Python. It bridges trading strategies (e.g., platforms like TradingView, custom strategy scripts or applications) via webhooks with cryptocurrency and stock exchanges. Hence the name Tradleware (trading middleware). It runs entirely on the user's own infrastructure — no third-party services, no subscriptions, no data sharing to make it the safest, most private option for autotrading.

- **License:** GPL v3
- **Language:** Python 3.11+
- **Deployment:** Docker / docker-compose
- **Location:** Singapore 🇸🇬

---

## Core Philosophy

- **Privacy-first:** API keys never leave the user's own servers
- **Free forever:** Self-hosted, no SaaS costs
- **Security by design:** Webhook auth, customizable webhook URLs to avoid automated bot scans, session encryption, trusted IP lists make dashboard access smoother on keyboardless systems like your raspberry pi.
- **Regulatory focus:** Prioritizes MAS (Monetary Authority of Singapore) compliant exchanges, but designed to be flexible for global users
- **Extensible architecture:** Base trader classes and clear patterns make it easy to add new exchanges and features without breaking existing functionality

---

## Architecture Overview

```
TradingView (or any webhook source)
         │
         ▼
   Tradleware (FastAPI web app)
   ├── Webhook endpoint (validates & routes signals)
   ├── Web UI dashboard (FastAPI + Tailwind CSS)
   └── Traders
       ├── Crypto: OKX, Independent Reserve, Crypto.com
       └── Stock:  IBKR (Interactive Brokers) — in progress
```

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11, FastAPI |
| Frontend | Jinja2 templates, Tailwind CSS |
| Deployment | Docker, docker-compose |
| Exchange APIs | CCXT (crypto), IBKR TWS API (stocks) |
| Notifications | Gotify (self-hosted push notifications) |
| Code quality | pylint (10.00/10 target) |

---

## Project Structure

```
src/
  traders/
    crypto/   # OKX, Independent Reserve, Crypto.com traders
    stock/    # IBKR trader (in progress)
  ui/         # FastAPI app, templates, static assets
  misc/       # Logger, env helpers
tests/
tradleware_data/ibkr/
```

---

## Adding a New Crypto Trader

Every exchange integration must subclass `BaseCryptoTrader` (`src/traders/crypto/base_crypto_trader.py`).

### Environment variable naming convention

All env vars are constructed automatically from `account_identifier` + `exchange_id`:

```
{IDENTIFIER}_{EXCHANGE}_API_KEY
{IDENTIFIER}_{EXCHANGE}_SECRET_KEY
{IDENTIFIER}_{EXCHANGE}_PASSPHRASE        # optional depending on exchange
{IDENTIFIER}_{EXCHANGE}_SUBACCOUNT_NAME   # optional
{IDENTIFIER}_{EXCHANGE}_HOSTNAME
{IDENTIFIER}_{EXCHANGE}_STABLECOIN_FIAT_PAIR   # e.g. USDT/SGD
{IDENTIFIER}_{EXCHANGE}_CRYPTO_STABLECOIN_PAIR # e.g. BTC/USDT
{IDENTIFIER}_{EXCHANGE}_TRADLEWARE_API_KEY      # per-bot webhook auth key
```

The exchange ID in the var names and in `ACTIVE_TRADING_CONFIGS` must use the fixed exchange identifiers: `OKX`, `CRYPTOCOM`, `IR`. Example entry: `MYBTCBOT_OKX`.

The base class reads and validates these automatically. Subclasses only need to call `super().__init__(account_identifier, exchange_id)` and then set up `self.exchange` as a CCXT instance.

### `post_init()` (async)

Must be called after construction (before any trading). It:
- Loads markets from the exchange via CCXT
- Sets `self.trading_pair_valid = True/False`
- Logs available pairs for the crypto symbol if the configured pair is unsupported

### Log buffer (`self.log_buffer`)

A `deque(maxlen=50)` that captures all logger calls for this trader. The web UI reads it via `get_recent_logs()` to display per-bot logs on the dashboard. This is wired automatically by `_setup_log_buffer()` — no action needed in subclasses.

### `close()` (async)

Closes the CCXT exchange connection cleanly. Must be awaited on shutdown to avoid `Unclosed client session` warnings.

### `_safe_api_call(api_method, *args, **kwargs)`

Wraps every CCXT call. It:
- Awaits the result if it's a coroutine (real exchange), or returns directly if sync (mocks in tests)
- Handles and logs `AuthenticationError`, `ExchangeNotAvailable`, `DDoSProtection`, `RateLimitExceeded`, `NetworkError`, `ExchangeError` — all re-raised so the caller can decide on retry logic
- Catches any unexpected `Exception`, logs the full traceback, and returns `None` to avoid a hard crash

Always wrap CCXT calls in `_safe_api_call`:
```python
result = await self._safe_api_call(self.exchange.fetch_balance)
```

### Abstract methods every subclass must implement

| Method | Signature |
|---|---|
| `fetch_balance` | `async def fetch_balance(self)` |
| `create_order` | `async def create_order(self, symbol, side, spend_percentage, quantity, order_execution_strategy, dry_run, params)` |
| `cancel_order` | `async def cancel_order(self, order_id, symbol, params)` |
| `fetch_open_orders` | `async def fetch_open_orders(self, symbol, since, limit, params)` |

Use `self._validate_order_params(symbol, side, spend_percentage, quantity)` at the top of `create_order` — it enforces that exactly one of `spend_percentage` or `quantity` is provided, checks valid sides, and validates ranges.

---

## Webhook Payload Schema

POST to `/{WEBHOOK_PATH}` with JSON body:

```json
{
  "api_key":    "<{IDENTIFIER}_{EXCHANGE}_TRADLEWARE_API_KEY>",
  "trader_id":  "<IDENTIFIER_EXCHANGE>",
  "ticker":     "<CRYPTO_STABLECOIN_PAIR>",
  "action":     "buy | sell",
  "timestamp":  "<unix seconds/ms or ISO 8601>",
  "alert_name": "<optional string>",
  "order_size":      100,
  "order_size_type": "percentage | quantity",
  "dry_run":         false
}
```

- `order_size_type: "percentage"` (default) — `order_size` is 0–100 (%). Passed as `spend_percentage` to `create_order`.
- `order_size_type: "quantity"` — `order_size` is an exact asset amount. Passed as `quantity` to `create_order`.
- `dry_run: true` — simulates the order without executing it. Good for testing functions and parsing. Still goes through all validation and logging, but `create_order` should skip the actual API call.
- `ticker` must exactly match the trader's configured `CRYPTO_STABLECOIN_PAIR`.

---

## Adding a New Stock Trader

Every stock broker integration must subclass `BaseStockTrader` (`src/traders/stock/base_stock_trader.py`).

> **Note:** Currently, IBKR (Interactive Brokers) is the only broker with a usable API that has been integrated. The patterns, env var conventions, and implementation details below are therefore based on IBKR and are **not as standardized** as the crypto side. When adding a different broker in the future, adapt as needed — the base class defines the contract, but the specifics (auth mechanism, connection method, env var names) will vary per broker.

### Key differences from crypto traders

- **No CCXT** — IBKR uses the TWS/IB API client directly; error wrapping must be handled in the subclass (no `_safe_api_call` provided by the base)
- **No `post_init()`** — replaced by `connect()` (abstract async method); call this after construction before any trading
- **Symbol is fixed per trader instance** — bound at init from the env var `{IDENTIFIER}_{BROKER}_SYMBOL`, not taken from the webhook
- **No `fetch_balance()`** — split into `fetch_positions()` (shares held) and `fetch_account_value()` (cash/buying power)
- **`fetch_open_orders()`** takes no arguments — scoped to the trader's symbol implicitly

### Environment variable naming convention

IBKR gateway credentials are **global** (shared across all IBKR bots):

```
IBKR_GATEWAY_HOST      # usually 127.0.0.1
IBKR_USERNAME
IBKR_PASSWORD
IBKR_TRADING_MODE      # 'paper' or 'live'
IBKR_VNC_PASSWORD      # to access the IBKR Gateway UI for easier troubleshooting on headless setups
IBKR_READ_ONLY         # true/false
```

Per-bot vars use `{IDENTIFIER}_{BROKER}` prefix (broker ID is `IBKR`):

```
{IDENTIFIER}_{BROKER}_ACCOUNT_ID         # e.g. U1234567
{IDENTIFIER}_{BROKER}_SYMBOL             # e.g. AAPL, TSLA
{IDENTIFIER}_{BROKER}_EXTENDED_HOURS     # true/false
{IDENTIFIER}_{BROKER}_TRADLEWARE_API_KEY
```

Example `ACTIVE_TRADING_CONFIGS` entry: `MYAPPLEBOT_IBKR`.

### Market hours helpers (built into base)

| Method | Returns |
|---|---|
| `is_market_open()` | `True` during regular hours (9:30–16:00 ET) |
| `can_trade_now()` | `True` if regular hours, or extended hours when `extended_hours=True` |
| `get_market_status()` | `'open'`, `'pre-market'`, `'after-hours'`, or `'closed'` |
| `get_time_until_market_opens()` | Human-readable string e.g. `"2h 34m"`, or `None` if open |

Always call `can_trade_now()` before placing orders.

### Abstract methods every subclass must implement

| Method | Notes |
|---|---|
| `connect()` | Establish broker connection; called after construction |
| `disconnect()` | Close broker connection; called by `close()` |
| `fetch_positions()` | Returns dict with quantity, avg_cost, market_value, unrealized P&L |
| `fetch_account_value()` | Returns dict with cash, buying_power, total_value |
| `get_market_price(symbol)` | Returns current price as float or None |
| `create_order(side, spend_percentage, order_execution_strategy, limit_price, params)` | `limit_price` required when strategy is `'maker_limit'` |
| `cancel_order(order_id)` | Returns bool |
| `fetch_open_orders()` | Returns list of open order dicts |

### `create_order` signature (stock)

Note: stock `create_order` uses `limit_price` instead of `quantity` for limit orders — more mature than the crypto side, where limit order webhook support is still a future goal.

```python
async def create_order(self,
                       side: str,                              # 'buy' | 'sell'
                       spend_percentage: float = 1.0,         # 0.0–1.0
                       order_execution_strategy: str = 'market',
                       limit_price: Optional[float] = None,   # required for 'maker_limit'
                       params: dict = None)
```

---

## Current State & Active Goals

See [.github/current_state.md](current_state.md) for the latest project status and active development goals. Any time you make progress on a goal, update that file to reflect the new state — this is crucial for maintaining context across sessions and ensuring the next session can pick up right where you left off without needing to re-explain everything.

---

## Conventions & Preferences

- Follow existing code patterns strictly — new traders must extend the appropriate base class
- Keep pylint score at 10.00/10
- Docker-first deployment; avoid requiring local Python installs for end users
- Environment variables for all secrets and config (never hardcode)
- Logging via the custom logger in `src/misc/logger.py` — not raw `print()`
- Comments and docstrings in English
- Keep the README and CHANGELOG updated with meaningful changes

---

## Standing Instruction for Copilot

**At the end of every session where progress was made, update `.github/current_state.md`:**
- Tick off completed goals
- Add any new goals or blockers discovered
- Update the "Last updated" date
- Note the current version if it changed

This ensures the next session can immediately pick up from where we left off without re-explaining context.
