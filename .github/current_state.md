# Tradleware — Current State & Active Goals

> Last updated: 16 May 2026 (session 14)
> Last updated: 23 May 2026 (session 15)
> Last updated: 02 Jun 2026 (session 16)
> Last updated: 18 Aug 2026 (session 17)


## Current State
**Unreleased — security hardening pass on top of v3.3.2b** (version not yet bumped;
`TRADLEWARE_VERSION` in `app.py` and the tag comment in `docker-compose.yml` still read
v3.3.2b, and CHANGELOG.md has no entry for this work yet)

⚠️ **Three breaking changes are pending release.** They must lead the release notes:
1. `TRUSTED_PROXIES` must be set when running behind a reverse proxy or tunnel, or
   forwarded headers are ignored and trusted-IP access stops working.
2. TradingView alerts must send `{{timenow}}` instead of `{{time}}`, or every signal is
   rejected as stale on timeframes above ~5 minutes.
3. Dashboard logins now require HTTPS. Trusted-IP access over plain HTTP is unaffected.

- **Auth bypass fixed**: `X-Forwarded-For` was honoured unconditionally, so any client
  could pass as a `TRUSTED_IPS` entry with one header. Forwarded headers are now trusted
  only from `TRUSTED_PROXIES`, taking the rightmost non-proxy hop.
- **Secrets out of logs**: dashboard password, Gotify token, submitted webhook API keys,
  and the full webhook payload were all being written to `tradleware.log`.
- **Dashboard credentials masked**: exchange and Tradleware API keys were rendered as
  first-8 + last-8; now a fixed mask plus a 4-character suffix via a `mask_secret` filter.
- **Webhook replay protection**: freshness window (`WEBHOOK_MAX_AGE_S`, default 300s,
  never disableable) plus single-use signal fingerprints persisted across restarts in
  `src/logs/webhook_replay.json`. New module `src/misc/replay_guard.py`.
- **Webhook requires HTTPS** (`WEBHOOK_REQUIRE_HTTPS`): the API key rides in the body, so
  a cleartext delivery leaks a working trading credential.
- **Per-bot execution lock**: concurrent signals (or a signal racing a dashboard fiat
  conversion) both read the same balance and both ordered — 1000 USDT spent where 750 was
  intended. Serialised via `trader_execution_lock()`; different bots stay parallel.
- **Session cookie hardened**: `Secure` flag, 12h lifetime instead of Starlette's 14 days.
- **Gotify sending no longer blocks the event loop**: one unauthenticated request used to
  freeze the whole app for the length of the notification round-trip (measured 1.5s).
  Delivery now runs on a background thread with a bounded queue.
- **Log rotation**: `RotatingFileHandler` with gzip, ~16 MB ceiling. One handler shared by
  all loggers — independent rotators on the same file would corrupt each other.
- pylint: **10.00/10** across all of `src/`

**v3.3.2b released**

**v3.3.1b released**

**v3.3.0b released**

- All crypto traders (OKX, Crypto.com, IR, Coinbase, Kraken, **Binance**) fully operational.
- IBKR stock trader operational: webhook-driven buy/sell, `dry_run`, connection state tracking.
- IBKR order account ID now explicitly set on every order — fixes gateway rejection with sub-accounts.
- IBKR health-check loop: background task probes connections every 30 min; **auto-reconnects** on connection drop; Gotify notification on loss/restore.
- IBKR false order failure on 10349 transient Cancelled fixed — polling loop now skips transient Cancelled caused by TIF preset.
- IBKR error 10349 silenced to DEBUG — no Gotify noise on every order.
- IBKR error 2150 (invalid position derived value, fires outside market hours) silenced to DEBUG — no Gotify noise.
- IBKR error codes 1101 and 1102 (connection restored) merged into a single handler — one `success` log per reconnect event instead of two separate messages.
- IBKR informational error codes list consolidated: 10349 folded into the shared debug-only branch alongside 2103–2158 and 10167.
- IBKR duplicate error handler registrations fixed: `_on_error` is now unregistered before re-registering in `connect()`, preventing N handlers accumulating after N reconnects and causing N identical log lines + Gotify notifications per error event.
- **Update availability indicator**: background task polls GitHub Tags API at startup and every 6 hours; dashboard footer shows pulsing neon-magenta "⬆ Update available: vX.X.X" when a newer tag exists, or a static green "✔ Up to date!" when current. Configurable via `UPDATE_CHECK_INTERVAL_S` in `.env`.
- `fetch_positions()` now only logs the position for the configured symbol — no more full account position dumps.
- Order failure reason (IB error code + message) now surfaced in RuntimeError.
- Server public IP displayed on dashboard footer (fetched once at startup).
- Informational IBKR error codes (2107, 2109, 2119, 10167) silenced to DEBUG.
- **Sticky navbar added to dashboard**: logo + cyberpunk "Tradleware" brand text (Fira Code, neon blue/pink) + logout button, frosted-glass backdrop, neon-cyan bottom border.
- **Version hidden from login page**: prevents unauthenticated fingerprinting; version still shown in authenticated dashboard footer.
- `hostname` YAML field is now optional for all crypto traders — each falls back to its exchange default and displays the resolved hostname on the bot card.
- Return type annotations added to all five crypto trader classes.
- pylint score: **10.00/10** across all of `src/`
 Multi-arch Docker image (`amd64` + `arm64`) published as `cslev/tradleware:v3.3.1b` and `latest`.


## Future Goals

### Security — deferred items from the session 17 hardening pass
- [ ] **Webhook rate limiting** — no throttle on failed authentication. The DoS teeth were
      removed by making Gotify non-blocking, and log growth is now capped by rotation, so
      what remains is alert fatigue (one Gotify push per failed attempt, unbounded) and
      brute-forcing a weak `tradleware_api_key`. Best fixed by coalescing failure
      notifications ("47 failed attempts in the last hour") rather than a blanket limiter.
- [ ] **Pre-auth logging of attacker-controlled fields** — the "📥 Webhook received" line
      logs `trader_id`, `action` and `ticker` before authentication, letting anyone who
      knows the path write chosen strings into the log. Deferring it past the API key check
      halves the volume and removes the injection vector.

### Config hot-reload — prerequisites now in place
`get_trader_lock(trader_id)` is exposed so a reload can take a bot's lock before swapping
its trader instance, guaranteeing no request is mid-trade against the old one. Three things
to handle when building it:
- Re-resolve `traders[trader_id]` **inside** the lock — the handler currently binds `trader`
  well before acquiring it, so a request could execute against a swapped-out, closed instance.
- Replacing a dict value during iteration is safe, but adding or removing bots is not:
  `read_root` and the IBKR health loop both iterate `traders.items()` across await points.
  Build a new dict and rebind instead of mutating in place.
- `_TRADER_LOCKS` is never pruned; a reload that removes bots should drop their entries.

### IBKR — unimplemented methods (none block current production use)
 **Version bumped to v3.3.1b**
- [ ] `cancel_order()` — raises `NotImplementedError`. All orders are market orders that fill immediately; nothing to cancel. Only becomes relevant if limit orders are ever added.
- [ ] `fetch_open_orders()` — raises `NotImplementedError`. Useful for dashboard visibility into pending orders only.

### Limit Order Support via webhook
- `order_execution_strategy` is now read from the webhook payload in `app.py` (no longer hardcoded to `'market'`)
- However, the field is not yet formally documented in the webhook schema or validated at the API boundary — the payload `order_execution_strategy` is only passed through if present
- Only worth full validation/documentation when strategies actively use specific entry price targets


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

### 18 Aug 2026 (session 17) — security hardening pass
Thirteen commits, each verified with a purpose-built harness driving the real ASGI app
(152 checks total; no exchange APIs touched). pylint 10.00/10 maintained throughout.

- **Auth bypass via `X-Forwarded-For`** (`c23554e`): `get_client_ip()` returned the header
  verbatim, so `curl -H 'X-Forwarded-For: <trusted ip>'` granted full dashboard access with
  no proxy deployed. Added `TRUSTED_PROXIES` (IPs or CIDRs); forwarded headers are honoured
  only from those peers, walking `X-Forwarded-For` right-to-left for the rightmost non-proxy
  hop. `X-Forwarded-Proto` gated the same way. Dockerfile gained `--no-proxy-headers` so
  uvicorn cannot rewrite the client address from the header itself.
- **Secrets in logs** (`c3ab69b`): `DASHBOARD_PASSWORD` logged at INFO, `GOTIFY_APP_TOKEN`
  printed by every logger construction, and the submitted webhook API key logged at ERROR
  (which pushes to Gotify). Also the full payload via `json.dumps(data)`, found later and
  fixed in the replay commit. Added `USING_DEFAULT_CREDENTIALS`, reused for the login banner.
- **Dashboard credential exposure** (`5465102`): four render sites showed first-8 + last-8 of
  live keys, including the Tradleware webhook key on both card types. New `mask_secret` Jinja
  filter: fixed-width mask (does not leak length) plus a 4-char suffix, short keys fully hidden.
- **Webhook replay protection** (`7bd9a6d`, `8076480`): new `src/misc/replay_guard.py`.
  Freshness window plus SHA-256 fingerprints of the exact request bytes, checked and inserted
  under one lock, persisted atomically. Fingerprint TTL is **twice** the window: a signal dated
  in the future stays fresh until `timestamp + window`, which can be `2 x window` after it was
  accepted — a shorter TTL left it replayable in the gap. Timestamp parsing rewritten to return
  tz-aware UTC; the old parser mixed naive-local and aware values and would have raised
  `TypeError` on unix timestamps. Verified identical across five host timezones.
- **NTP requirement documented** (`dd11448`): freshness is measured against the host clock, and
  a Pi has no battery-backed RTC.
- **Webhook requires HTTPS** (`c2b2db9`): checked before the body is read. Rejection log names
  which of three misconfigurations applies, since a mistake here silently stops all trading.
- **Loopback checks** (`3d790ee`): `client_ip != "127.0.0.1"` appeared three times and missed
  `::1` — which the healthcheck can use, since `localhost` resolves to both in the image.
  Replaced with `is_loopback()`; it is a logging filter only and grants nothing.
- **Per-bot execution lock** (`57f5044`): reproduced two concurrent "buy 50%" signals spending
  1000 USDT where 750 was intended, both returning 200. `trader_execution_lock(trader_id)` is
  held across read-balance → place-order in the webhook handler and around `/convert`.
  Contended requests queue, bounded by `TRADER_LOCK_TIMEOUT_S` then 503.
- **Session cookie** (`e928752`): added `Secure` and a 12h lifetime (Starlette defaults to 14
  days; `httponly` was already set). Login over plain HTTP now fails with an explanation
  instead of an invisible redirect loop, since browsers discard a `Secure` cookie there.
- **Docker gateway documented** (`6f9d4ef`): host-local traffic arrives from the network
  gateway, not the container IP. Commented `ipam` block in `docker-compose.yml` for pinning it.
- **Gotify unblocked** (`fa0fa58`): `requests.post` ran inline inside async handlers, so one
  unauthenticated request froze the event loop for the whole round-trip — measured 1504ms,
  now 3ms. Background worker thread, bounded queue that drops rather than blocks, flushed at
  shutdown. Fixes the stall for every log path, including inside order placement.
- **Log rotation** (`c94466e`): 10 MB x 5 with gzip, ~16 MB ceiling. The handler is shared
  across all loggers — independent `RotatingFileHandler`s on one file rotate against each
  other and lose writes. Handler left at NOTSET; file opened UTF-8 for the emoji.
- **Default webhook path surfaced, not enforced** (`b827d0c`): a banner after login plus a
  footer line with a `ⓘ` tooltip carrying the generation recipe, shown only when
  `WEBHOOK_PATH` is literally `webhook`. Deliberately advisory — the API key is what
  protects the endpoint, so a predictable path costs scanner noise, not trade safety.
  Startup-time randomisation was rejected: it would change on every restart and break
  every TradingView alert.
- **Webhook API key strength surfaced** (`740fbc9`): new `src/misc/key_strength.py`
  grades each bot's `tradleware_api_key` by estimated search space, distinct characters,
  reuse across bots, and — the worst case — whether it is still one of the placeholders
  from the `.yaml.example` files, which are public in the repository. Reported at startup
  and on the dashboard (banner plus a per-card marker with the fix in a tooltip). Advisory
  only: refusing to load a bot over a weak key would stop a live deployment trading, which
  is worse than the risk. Entropy estimation measures size, not predictability, so
  `Password2026` scores like twelve random characters — the placeholder list covers the
  common case that slips through.
- **Webhook rejection flooding** (`17634ac`): every rejection wrote a log line and
  pushed a notification, both unbounded and drivable by anyone who knew the path. Because
  the log rotates on size, a flood also *evicted* real history — the whole 63 MB in about
  an hour at 100 req/s, taking the evidence with it. New `src/misc/rejection_reporter.py`
  reports the first of each distinct problem immediately and collapses repeats into one
  summary per window. Measured: 144 bytes/rejection to 0, and 360,000 pushes/hour to 12.
  The "webhook received" line also moved after authentication — it fired for every
  request with three attacker-chosen fields in it, so coalescing the error alone would
  have left the flood half intact. Closes the pre-auth logging item too.
- **CSRF on /convert** (`__HASH__`): the endpoint spends a bot's whole fiat balance and
  a bare cross-site POST succeeded when the browser sat on a trusted IP. SameSite=lax
  covers the session cookie, but the TRUSTED_IPS path has no cookie for SameSite to
  govern — which is exactly the Raspberry Pi kiosk setup. Now requires an
  `X-Tradleware-Request` header; a custom header forces a CORS preflight that Tradleware
  never answers, so a browser will not send the real request. Read-only endpoints are
  deliberately unguarded: same-origin policy already stops a cross-site page reading them.
- **Constant-time credential comparison** (`69e3f52`): the webhook key used a plain `!=`,
  which returns on the first differing byte and leaks the key through response timing.
  Now `secrets.compare_digest`. Fixing it surfaced a live bug on the **login form**, which
  already used `compare_digest` but on `str` arguments — that raises `TypeError` on
  non-ASCII, so any `DASHBOARD_PASSWORD` containing an accent made signing in impossible
  (correct password → unhandled 500), and any client could raise a 500 on the
  unauthenticated login endpoint with a non-ASCII username. Both now compare as bytes.

### 02 Jun 2026 (session 16)
- **Security: CVE-2026-48710 (BadHost) assessment**: confirmed Tradleware is not affected — no custom `BaseHTTPMiddleware` using `request.url.path` for access control; auth is route-level via `request.session`
- **Dependency upgrade**: Starlette 0.52.1 → 1.2.1, FastAPI 0.129.0 → 0.136.3 (Host header validation fix per RFC 9112/3986)
- **`src/requirement.txt`**: pinned `fastapi>=0.136.3`; added explicit `starlette>=1.0.1` constraint
- **Version bumped to v3.3.2b**
- **Multi-arch Docker image published**: `cslev/tradleware:v3.3.2b` and `latest` (amd64 + arm64)

### 23 May 2026 (session 15)
- **TradingView action normalization**: Webhook handler now accepts 'long'/'short' as 'buy'/'sell' and normalizes for all bots. No changes needed in trader classes.
- **Ticker documentation warning**: README and docs now strongly emphasize that the `ticker` field in webhook payloads must match the bot's `crypto_stablecoin_pair` (e.g., `BTC/USDT`), not a generic ticker or space-separated format. Added warning for TradingView users.
- **Version bumped to v3.3.1b**

### 16 May 2026 (session 14)
- **Binance exchange integration**: `BinanceTrader` subclassing `BaseCryptoTrader`; CCXT `binance` exchange; standard API key + secret key (HMAC-SHA256), no passphrase; registered in `EXCHANGE_TRADER_CLASSES`
- **Subaccount support**: Binance subaccounts use their own independent API keys; `subaccount_name` is a display-only label for the dashboard bot card
- **Hostname default**: resolves to `api.binance.com`; Binance.US users set `hostname: api.binance.us`
- **`createMarketBuyOrderWithCost`**: used for spend% market buys (Binance natively supports `quoteOrderQty`); ticker-based fallback retained
- **`binance.yaml.example`**: full setup docs with subaccount pattern and API key permission guide
- **Version bumped to v3.3.0b**

### 16 May 2026 (session 13)
- **Kraken Pro integration**: `KrakenTrader` subclassing `BaseCryptoTrader`; CCXT `kraken` exchange; API key + base64 private key auth (no passphrase); full 4-layer `create_order` with `createMarketBuyOrderWithCost` and ticker-based fallback; registered in `EXCHANGE_TRADER_CLASSES`; `bot_configs/crypto/kraken.yaml.example` added
- **Hostname made optional**: removed `hostname` from `_CRYPTO_REQUIRED` in `config_loader.py`; each trader now resolves its default (e.g. `api.kraken.com`) before constructing the CCXT exchange object; `base_crypto_trader.py` uses `.get('hostname', '')` instead of `config['hostname']`; hostname always populated and visible on bot card
- **Return type annotations**: added to `create_order`, `cancel_order`, `fetch_open_orders`, `list_fiat_markets`, `convert_fiat_to_stablecoin` across all five crypto traders
- **Version bumped to v3.2.0b**; tag pushed to GitHub; Docker images published (`amd64` + `arm64`) as `cslev/tradleware:latest` + `cslev/tradleware:v3.2.0b`

### 8 May 2026 (session 12)
- **Coinbase Advanced Trade (CDP) integration**: `CoinbaseTrader` subclassing `BaseCryptoTrader`; CCXT `coinbase` exchange; CDP keys (`organizations/...` format, JWT auto-handled by CCXT); registered in `EXCHANGE_TRADER_CLASSES`
- **`_get_maker_buy_price` override**: Coinbase USDC/SGD pair is in limit-only mode; base class uses `bid * 0.9999` (order never fills); overridden to use ask price for immediate fill
- **`convert_fiat_to_stablecoin` defaults to `maker_limit`**: market orders unsupported on USDC/SGD; convert endpoint no longer hardcodes `order_execution_strategy='market'`
- **Post-placement `fetch_order` call**: Coinbase order response lacks `amount`/`cost` fields; a follow-up `fetch_order` populates them for accurate logging
- **`coinbase.yaml.example`**: CDP key format documented; `subaccount_name` used as display label (not API field)
- **Coinbase logo asset added**: `src/ui/static/images/crypto_exchanges/coinbase.png`
- **Old CoinbasePro logo removed**: `src/ui/static/images/crypto_exchanges/coinbasepro.png` deleted
- **Bot ID label improved**: dark semi-transparent pill with blur on dashboard exchange cards for legibility against any logo background
- **Version hidden from login page**: `TRADLEWARE_VERSION` removed from `login.html` to prevent unauthenticated fingerprinting; version retained in authenticated dashboard footer
- **Version bumped to v3.1.0b**; tag pushed to GitHub; Docker images published (`amd64` + `arm64`) as `cslev/tradleware:latest` + `cslev/tradleware:v3.1.0b`

### 7 May 2026 (session 11)
- **Update availability indicator**: `_check_for_updates()` queries GitHub Tags API; `_update_check_loop()` background task runs at startup then every `UPDATE_CHECK_INTERVAL_S` seconds (default 6h); `update_available` and `latest_version` passed to dashboard template; footer shows pulsing neon-magenta badge when behind, static green "✔ Up to date!" when current
- **Dashboard green color unified**: all status-text greens normalised to `text-green-300` / `#86efac` across `index.html`
- **Version bumped to v3.0.7b**

### 25 Apr 2026 (session 10)
- **IBKR duplicate error handler fix**: `connect()` now does `self.ib.errorEvent -= self._on_error` before `+=`; prevents N handlers accumulating after N reconnects, eliminating the burst of N identical error log lines and Gotify notifications per IB event
- **Version bumped to v3.0.5b**

### 20 Apr 2026 (session 9)
- **IBKR error 2150 suppressed**: added to debug-only informational list — fires when IB cannot compute derived P&L (market price unavailable, e.g. outside market hours); no trading impact, no Gotify alert
- **IBKR 1101/1102 merged**: both "connection restored" codes now share one handler with a single `success` log + `is_connected = True`; previously 1102 also only logged a warning without updating the flag
- **IBKR 10349 consolidated**: folded into the shared informational debug-only `elif errorCode in [...]` list alongside 2103–2158 and 10167 — redundant separate branch removed

### 15 Apr 2026 (session 7)
- **IBKR false order failure fix (10349)**: polling loop now detects transient `Cancelled` caused by IB error 10349 (TIF preset) via `trade.log` inspection and continues polling instead of raising `RuntimeError`
- **IBKR error 10349 silenced**: downgraded from `WARNING` to `DEBUG` — fired on every order, purely informational
- **Order failure reason surfaced**: `_order_errors` dict stores last IB error per `reqId`; included in `RuntimeError` message when order is truly rejected/cancelled
- **IBKR auto-reconnect**: health-check loop now calls `trader.connect()` automatically when connection is down; Gotify success/error on reconnect outcome
- **fetch_positions filtered**: only logs position for the configured symbol; ignores all other account positions
- **Version bumped to v3.0.2**; Docker images published

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
