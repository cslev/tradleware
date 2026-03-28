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
       └── Stock:  IBKR (Interactive Brokers)
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
bot_configs/
  crypto/     # Per-exchange YAML files (okx.yaml, cryptocom.yaml, ir.yaml)
  stock/      # Per-broker YAML files (ibkr.yaml)
src/
  traders/
    crypto/   # OKX, Independent Reserve, Crypto.com traders
    stock/    # IBKR trader
  ui/         # FastAPI app, templates, static assets
  misc/       # Logger, env helpers, config_loader
tests/
tradleware_data/
```

---

## Adding a New Crypto Trader

Every exchange integration must subclass `BaseCryptoTrader` (`src/traders/crypto/base_crypto_trader.py`).

### Bot configuration (YAML)

Each exchange has a single YAML file in `bot_configs/crypto/` (e.g. `okx.yaml`, `cryptocom.yaml`, `ir.yaml`). Each file contains a list of bots under that exchange. See the `.yaml.example` files for the full structure.

Key fields per bot:
```yaml
bots:
  - id: mybtcbot              # lowercase, used as trader_id in webhooks
    api_key: ...
    secret_key: ...
    passphrase: ...           # optional depending on exchange
    subaccount_name: ...      # optional
    hostname: my.okx.com
    stablecoin_fiat_pair: USDT/SGD
    crypto_stablecoin_pair: BTC/USDT
    tradleware_api_key: ...   # per-bot webhook auth key
```

The config is discovered automatically by `src/misc/config_loader.py` via `get_bot_configs()`. Subclasses receive a `config: dict` and only need to call `super().__init__(config, default_type, self.logger)` then set up `self.exchange` as a CCXT instance.

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
  "api_key":    "<tradleware_api_key from bot YAML>",
  "trader_id":  "<bot id from YAML, lowercase, e.g. mybtcbot>",
  "ticker":     "<crypto_stablecoin_pair from YAML, e.g. BTC/USDT>",
  "action":     "buy | sell",
  "timestamp":  "<unix seconds/ms or ISO 8601>",
  "alert_name": "<optional string>",
  "order_size":      100,
  "order_size_type": "percentage | quantity",
  "dry_run":         false
}
```

- `trader_id` must match the `id` field in the bot's YAML config (always lowercase).
- `order_size_type: "percentage"` (default) — `order_size` is 0–100 (%). Passed as `spend_percentage` to `create_order`.
- `order_size_type: "quantity"` — `order_size` is an exact asset amount. Passed as `quantity` to `create_order`.
- `dry_run: true` — simulates the order without executing it. Good for testing functions and parsing. Still goes through all validation and logging, but `create_order` should skip the actual API call.
- `ticker` must exactly match the trader's configured `crypto_stablecoin_pair` in the YAML.

---

## Adding a New Stock Trader

Every stock broker integration must subclass `BaseStockTrader` (`src/traders/stock/base_stock_trader.py`).

> **Note:** Currently, IBKR (Interactive Brokers) is the only broker with a usable API that has been integrated. The patterns and implementation details below are based on IBKR and are **not as standardized** as the crypto side. When adding a different broker in the future, adapt as needed — the base class defines the contract, but the specifics (auth mechanism, connection method, YAML structure) will vary per broker.

### Key differences from crypto traders

- **No CCXT** — IBKR uses the TWS/IB API client directly; error wrapping must be handled in the subclass (no `_safe_api_call` provided by the base)
- **No `post_init()`** — replaced by `connect()` (abstract async method); call this after construction before any trading
- **Symbol is fixed per trader instance** — bound at init from `config['symbol']`, not taken from the webhook
- **No `fetch_balance()`** — split into `fetch_positions()` (shares held) and `fetch_account_value()` (cash/buying power). Note: `fetch_account_value()` is abstract in the base but the IBKR implementation raises `NotImplementedError` — cash is fetched inline inside `create_order` via `accountSummaryAsync()`, so the dashboard Summary tab does not show cash values yet.
- **`fetch_open_orders()`** takes no arguments — scoped to the trader's symbol implicitly

### Bot configuration (YAML)

IBKR bot config lives in `bot_configs/stock/ibkr.yaml`. The YAML contains a `gateway` block (shared across all IBKR bots on that host) and a `bots` list:

```yaml
gateway:
  host: 127.0.0.1
  port: 8888
  username: your_ibkr_username
  password: your_ibkr_password
  trading_mode: live          # 'paper' or 'live'
  vnc_password: changeme
  read_only: false

bots:
  - id: myapplebot            # lowercase, used as trader_id in webhooks
    account_id: U1234567
    symbol: AAPL
    extended_hours: false
    fractional_shares: false  # not all symbols support this; IBKR rejects if unsupported
    tradleware_api_key: ...   # per-bot webhook auth key
    # Optional market hours (defaults to US Eastern / NYSE hours):
    # market_timezone: America/New_York
    # market_open: "09:30"
    # market_close: "16:00"
    # pre_market_open: "04:00"
    # after_hours_close: "20:00"
```

The config is discovered automatically by `src/misc/config_loader.py`. Subclasses receive a `config: dict` and call `super().__init__(config, logger)`.

### Market hours helpers (built into base)

| Method | Returns |
|---|---|
| `is_market_open()` | `True` during regular hours (per bot config; default 9:30–16:00 ET) |
| `can_trade_now()` | `True` if regular hours, or extended hours when `extended_hours=True` |
| `get_market_status()` | `'open'`, `'pre-market'`, `'after-hours'`, or `'closed'` |
| `get_time_until_market_opens()` | Human-readable string e.g. `"2h 34m"`, or `None` if open |

Market hours are configured per bot in the YAML (all optional, US Eastern defaults):
```yaml
    market_timezone: America/New_York  # IANA timezone; default: America/New_York
    market_open: "09:30"               # default: 09:30
    market_close: "16:00"              # default: 16:00
    pre_market_open: "04:00"           # default: 04:00
    after_hours_close: "20:00"         # default: 20:00
```

Always call `can_trade_now()` before placing orders.

### Abstract methods every subclass must implement

| Method | Notes |
|---|---|
| `connect()` | Establish broker connection; called after construction |
| `disconnect()` | Close broker connection; called by `close()` |
| `fetch_positions()` | Returns dict with quantity, avg_cost, market_value, unrealized P&L |
| `fetch_account_value()` | Returns dict with cash, buying_power, total_value. **Raises `NotImplementedError` in `IBKRTrader`** — cash is fetched inline in `create_order`. |
| `get_market_price(symbol)` | Returns current price as float or None |
| `create_order(side, spend_percentage, order_execution_strategy, limit_price, quantity, params)` | `limit_price` required when strategy is `'maker_limit'` |
| `cancel_order(order_id)` | Returns bool. **Raises `NotImplementedError` in `IBKRTrader`** — market orders fill immediately; only needed if limit orders are added. |
| `fetch_open_orders()` | Returns list of open order dicts. **Raises `NotImplementedError` in `IBKRTrader`** — dashboard visibility only; not a trading blocker. |

### `create_order` signature (stock)

`spend_percentage` and `quantity` are mutually exclusive — pass exactly one. `limit_price` is only required when `order_execution_strategy` is `'maker_limit'`.

```python
async def create_order(self,
                       side: str,                              # 'buy' | 'sell'
                       spend_percentage: float = None,        # 0.0–1.0; mutually exclusive with quantity
                       order_execution_strategy: str = 'market',
                       limit_price: Optional[float] = None,   # required for 'maker_limit'
                       quantity: Optional[float] = None,      # exact share count; mutually exclusive with spend_percentage
                       params: dict = None)
```

---

## Current State & Active Goals

See [.github/current_state.md](current_state.md) for the latest project status and active development goals. Any time you make progress on a goal, update that file to reflect the new state — this is crucial for maintaining context across sessions and ensuring the next session can pick up right where you left off without needing to re-explain everything.
Once a future goal or feature is completed, **remove it from the Future Goals section entirely** and record it in the Session History with a brief note. Do not keep completed items with strikethrough.

---

## Conventions & Preferences

- Follow existing code patterns strictly — new traders must extend the appropriate base class
- Keep pylint score at 10.00/10
- Docker-first deployment; avoid requiring local Python installs for end users
- Bot secrets and config live in `bot_configs/` YAML files — never in `.env` or hardcoded
- `.env` is for Tradleware-level settings only (dashboard auth, logging, webhook path, Gotify)
- Logging via the custom logger in `src/misc/logger.py` — not raw `print()`
- Comments and docstrings in English
- Keep the README and CHANGELOG updated with meaningful changes

---

## Important Instruction for Copilot

### At the end of every session where progress was made, update `.github/current_state.md`
- Tick off completed goals
- Add any new goals or blockers discovered
- Update the "Last updated" date
- Note the current version if it changed
- Modify the version in app.py if a new version is under development

This ensures the next session can immediately pick up from where we left off without re-explaining context.

### GIT instructions
- Every time when you are explicitly asked to commit changes, always go by the implemented feature/bugfix/minor change instead of the files changed. If a new feature/bugfix/minor change spans across multiple files, commit them together with a clear message describing the feature/bugfix/minor change, not the files. For example, if you implemented the IBKR stock trader, the commit message should be "Implement IBKR stock trader with market order support" instead of "Changes in ibkr_trader.py, base_stock_trader.py, app.py, .env.example". This way, the commit history will be more meaningful and easier to understand.
- Always do this step-by-step, and wait for my approval after each commit before proceeding to the next one. This allows me to review the changes incrementally and provide feedback if necessary, ensuring that we maintain a high-quality codebase and stay aligned on the project goals.

### Development instructions and patterns
- Always use 2-space indentation 
- always put imports on the top and follow the existing import patterns in the file (e.g. relative vs absolute imports, grouping standard library vs local imports) to make the code consistent and clean and compliant with pylint
- always avoid trailing whitespaces to be compliant with pylint C0303