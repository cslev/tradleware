# Tradleware — Copilot Context

## What is Tradleware?

**Tradleware** is a free, open-source autotrading middleware built in Python. It bridges trading strategies (e.g., platforms like TradingView, custom strategy scripts or applications) via webhooks with cryptocurrency and stock exchanges. Hence the name Tradleware (trading middleware). It runs entirely on the user's own infrastructure — no third-party services, no subscriptions, no data sharing to make it the safest, most private option for autotrading.

- **License:** GPL v3
- **Language:** Python 3.11+
- **Deployment:** Docker / docker-compose

---

## Core Philosophy

- **Privacy-first:** API keys never leave the user's own servers
- **Free forever:** Self-hosted, no SaaS costs
- **Security by design:** Webhook auth, customizable webhook URLs to avoid automated bot scans, session encryption, trusted IP lists make dashboard access smoother on keyboardless systems like your raspberry pi.
- **Regulatory focus:** Prioritizes regulated, industry-standard licensed exchanges; designed to be flexible for global users
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
       ├── Crypto: OKX, Independent Reserve, Crypto.com, Coinbase
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
  crypto/     # Per-exchange YAML files (okx.yaml, cryptocom.yaml, ir.yaml, coinbase.yaml)
  stock/      # Per-broker YAML files (ibkr.yaml)
src/
  traders/
    crypto/   # OKX, Independent Reserve, Crypto.com, Coinbase traders
    stock/    # IBKR trader
  ui/         # FastAPI app, templates, static assets
  misc/       # Logger, env helpers, config_loader, replay_guard
tests/        # pytest suite (see "Testing" below)
tradleware_data/
```

## Testing

```bash
pip install -r requirements-dev.txt   # pytest, pytest-asyncio, httpx, pytest-randomly
pytest
```

`tests/conftest.py` does the setup that makes the app testable, and the reasons matter:

- **The real `.env` is not read.** `app.py` calls `load_dotenv(..., override=True)`, which
  would overwrite the pinned test configuration with whatever is on the machine, so
  `load_dotenv` is neutralised before the app is imported.
- **Outbound HTTP is blocked.** `_fetch_public_ip()` runs at module scope; `requests.get`
  is stubbed so the suite is offline. `requests.post` is left alone — the logging tests
  exercise Gotify delivery against a local stand-in server.
- **Module globals are restored after every test.** Configuration lives in module-level
  constants and traders in a module dict, so `isolate_app_state` snapshots and restores
  them. Anything derived from app configuration must be read from the app, never
  recomputed in a fixture, or the assertion checks the fixture instead of the code.
- **Settings read at import cannot be changed per test.** `WEBHOOK_PATH` is baked into the
  route decorator and the session middleware is constructed once; use the `webhook_url`
  and `reconfigure_session` fixtures rather than reassigning the globals.

When fixing a security issue, add a test that **fails when the fix is reverted** — verify
that by actually reverting it. Several of these behaviours are invariants with no visible
symptom when broken (the replay fingerprint TTL must be twice the freshness window; the
rotating log handler must be shared across loggers; Gotify must never be sent inline).

---

## Adding a New Crypto Trader

Every exchange integration must subclass `BaseCryptoTrader` (`src/traders/crypto/base_crypto_trader.py`).

### Checklist

1. Create `bot_configs/crypto/<exchange>.yaml.example`
2. Create `src/traders/crypto/<exchange>_trader.py` subclassing `BaseCryptoTrader`
3. Register the class in `src/ui/app.py` → `EXCHANGE_TRADER_CLASSES`
4. Run `pylint src/traders/crypto/<exchange>_trader.py` — must be 10.00/10

### Bot configuration (YAML)

Each exchange has a single YAML file in `bot_configs/crypto/` (e.g. `okx.yaml`, `cryptocom.yaml`, `ir.yaml`, `coinbase.yaml`). Each file contains a list of bots under that exchange. See the `.yaml.example` files for the full structure.

Key fields per bot:
```yaml
bots:
  - id: mybtcbot              # lowercase, used as trader_id in webhooks
    api_key: ...
    secret_key: ...
    passphrase: ...           # optional — only if the exchange requires it (e.g. OKX)
    subaccount_name: ...      # optional — only if the exchange supports subaccounts
    hostname: my.okx.com
    stablecoin_fiat_pair: USDT/SGD
    crypto_stablecoin_pair: BTC/USDT
    tradleware_api_key: ...   # per-bot webhook auth key
```

The config is discovered automatically by `src/misc/config_loader.py` via `get_bot_configs()`. The exchange name is derived from the filename (e.g. `coinbase.yaml` → `exchange = 'coinbase'`). Subclasses receive a `config: dict` and only need to call `super().__init__(config, default_type, self.logger)` then set up `self.exchange` as a CCXT async instance.

**Special auth formats (example — Coinbase CDP keys):**
Some exchanges use non-standard API key formats. Document these clearly in the `.yaml.example` and in the class docstring. CCXT handles the auth internally in most cases (e.g. JWT signing for Coinbase CDP keys is automatic when `apiKey` starts with `organizations/`).

### Registering in `app.py`

After creating the trader class, add it to `EXCHANGE_TRADER_CLASSES` in `src/ui/app.py`:
```python
from src.traders.crypto.<exchange>_trader import <Exchange>Trader

EXCHANGE_TRADER_CLASSES = {
  ...
  '<exchange>': <Exchange>Trader,   # key must match the YAML filename without .yaml
}
```

### `__init__` pattern

Always create the `CustomLogger` first, then call `super().__init__()`, then instantiate `self.exchange`:
```python
def __init__(self, config: dict, default_type: str = 'spot'):
    self.logger = CustomLogger(
      name=self.__class__.__name__,
      gotify_url=get_env('GOTIFY_SERVER_URL'),
      gotify_token=get_env('GOTIFY_APP_TOKEN'),
      gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30'))
    )
    super().__init__(config, default_type, self.logger)
    self.exchange = ccxt_async.<exchange>({
      'apiKey': self.api_key,
      'secret': self.secret_key,
      'hostname': self.hostname if self.hostname else '<default_hostname>',
      'options': {'defaultType': self.default_type},
      'enableRateLimit': True,
    })
```

### `post_init()` (async)

Inherited — must be called after construction (before any trading). It:
- Loads markets from the exchange via CCXT
- Sets `self.trading_pair_valid = True/False`
- Logs available pairs for the crypto symbol if the configured pair is unsupported

### Log buffer (`self.log_buffer`)

A `deque(maxlen=50)` that captures all logger calls for this trader. The web UI reads it via `get_recent_logs()` to display per-bot logs on the dashboard. This is wired automatically by `_setup_log_buffer()` — no action needed in subclasses.

### `close()` (async)

Inherited — closes the CCXT exchange connection cleanly. Must be awaited on shutdown to avoid `Unclosed client session` warnings.

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

### `create_order` — the 4-layer pattern

Every `create_order` implementation follows this exact structure. Do not deviate.

```
LAYER 1 — _validate_order_params()         Validates symbol, side, spend%/quantity exclusivity, ranges
LAYER 2 — _resolve_market_and_balance()    Loads CCXT market dict + fetches live balances into ctx dict
LAYER 3 — _calculate_order_size()          Returns (order_type, amount_to_trade, price)
DRY RUN — return mock order dict           Skips Layer 4 entirely; no API call
LAYER 4 — exchange-specific execution      Place the real order via _safe_api_call
```

**Layer 3 contract:** For `spend_percentage` market buys, `amount_to_trade` is returned in **QUOTE currency** (the cost to spend). For all other modes (quantity, sells, limit orders) it is in **BASE currency** with precision applied. Layer 4 must handle this distinction.

**Layer 4 — spend% market buy best practice:** Prefer `createMarketBuyOrderWithCost(symbol, cost, params)` if the exchange supports it — it passes the exact quote cost to the exchange and avoids precision loss. Fall back to a ticker-based base-amount calculation with a ~0.5% fee buffer (`adjusted_cost = amount * 0.995`) if unsupported. See `coinbase_trader.py` or `okx_trader.py` for the full pattern.

### Standard optional methods (implement for all traders)

These are not abstract but every trader should include them for feature parity:

- **`list_fiat_markets(fiat_currency)`** — loads all markets, filters by fiat currency, logs results. Useful for discovering available pairs.
- **`convert_fiat_to_stablecoin(spend_percentage, order_execution_strategy, max_slippage)`** — high-level helper that buys the configured stablecoin with fiat, including slippage check and order status polling.

See any existing trader (`coinbase_trader.py`, `okx_trader.py`, `cryptocom_trader.py`) for the canonical implementation — the logic is identical across exchanges since it uses `create_order` and standard CCXT calls internally.

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
  host: ib_gateway  # Docker container name; use 127.0.0.1 only when running outside Docker
  port: 8888
  # Credentials and trading mode live in .env.ibkr at the project root (for Docker only)

bots:
  - id: myapplebot            # lowercase, used as trader_id in webhooks
    account_id: U1234567
    symbol: AAPL
    extended_hours: false
    fractional_shares: false  # not all symbols support this; IBKR rejects if unsupported
    account_currency: USD     # optional, default USD — which TotalCashValue row to size against
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
- **Never** add `Co-Authored-By:` or any AI attribution trailer to commit messages.

### Development instructions and patterns
- Always use 2-space indentation 
- always put imports on the top and follow the existing import patterns in the file (e.g. relative vs absolute imports, grouping standard library vs local imports) to make the code consistent and clean and compliant with pylint
- always avoid trailing whitespaces to be compliant with pylint C0303
- Always run `pylint` on the changed files and make sure the score is 10.00/10 before committing. This ensures that the code quality remains high and consistent across the project.

### Build instructions

- Before building, if there is a version number change, make sure to update the version in `app.py` and `.github/current_state.md` to reflect the new version under development. Also update the tag in `docker-compose.yml` file's `build` section to match the new version, **but keep the latest as well**, so we should have two tags: **latest** and **new version**.
- The project uses **multi-arch builds** (amd64 + arm64) via Docker Buildx so the image works on both x86 servers and Raspberry Pi.
- **One-time builder setup** (only needed once per machine):
  ```bash
  sudo docker buildx create --name multiarch --driver docker-container --use
  sudo docker buildx inspect --bootstrap
  ```

#### 🚨 Release label prompt for builds

- **When you ask Copilot to build and push Docker images, Copilot will now prompt you for a release label or changelog message.**
- You should copy-paste your release notes or changelog (e.g. from CHANGELOG.md) when prompted. Copilot will then add it as a Docker label using `--label org.opencontainers.image.description="..."` in the build command.
- This label will be embedded in the image and can be inspected later with:
  ```bash
  docker inspect cslev/tradleware:vX.Y | grep description
  ```

- **To build and push to Docker Hub** (replace `vX.Y` with the new version):
  ```bash
  sudo docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --tag cslev/tradleware:latest \
    --tag cslev/tradleware:vX.Y \
    --label org.opencontainers.image.description="<paste your changelog or release notes here>" \
    --push \
    .
  ```
  Note: `--push` is required for multi-arch builds — they cannot be loaded to the local Docker daemon.

- **To test locally** (single-arch, no push):
  ```bash
  sudo docker buildx build --platform linux/amd64 --tag cslev/tradleware:latest --load .
  ```

**Copilot must always prompt for a release label before building Docker images, and use the label in the build command.**
