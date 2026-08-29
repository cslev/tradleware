"""
Tradleware Dashboard - FastAPI Web Interface

This module provides a web-based dashboard for managing cryptocurrency trading bots.
It handles authentication, trader initialization, webhook processing for TradingView
signals, and provides endpoints for balance monitoring and order management.
"""
# pylint: disable=too-many-lines

# Standard library imports
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import ipaddress
import json
import math
from pathlib import Path
import re
import secrets

# Third-party imports
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests as http_requests
from starlette.middleware.sessions import SessionMiddleware

# First-party imports
from src.misc.logger import CustomLogger, flush_gotify_queue
from src.misc.get_env import get_env
from src.misc.config_loader import get_bot_configs
from src.misc.failure_limiter import FailureLimiter
from src.misc.key_strength import assess_key, find_shared_keys
from src.misc.rejection_reporter import RejectionReporter
from src.misc.replay_guard import ReplayGuard, parse_signal_timestamp, signal_fingerprint
from src.traders.crypto.binance_trader import BinanceTrader
from src.traders.crypto.coinbase_trader import CoinbaseTrader
from src.traders.crypto.cryptocom_trader import CryptocomTrader
from src.traders.crypto.ir_trader import IRTrader
from src.traders.crypto.kraken_trader import KrakenTrader
from src.traders.crypto.okx_trader import OKXTrader
from src.traders.stock.ibkr_trader import IBKRTrader


# Application version
TRADLEWARE_VERSION = "v3.4.3b"

# You might need to adjust this import based on where your logger.py is relative to app.py
# If your logger is within src/misc, you might access it like this:
# Import centralized get_env helper
# Import our trader classes

# Trading configuration
EXCHANGE_TRADER_CLASSES = {
  'binance': BinanceTrader,
  'coinbase': CoinbaseTrader,
  'cryptocom': CryptocomTrader,
  'ir': IRTrader,
  'kraken': KrakenTrader,
  'okx': OKXTrader,
  # Add other exchanges here as you create their trader classes
}

# Stock broker configuration
BROKER_TRADER_CLASSES = {
  'ibkr': IBKRTrader,
  # 'alpaca': AlpacaTrader,
  # Add other stock brokers here as you create their trader classes
}

# Store active traders
traders = {}

# Update check state — populated by _update_check_loop() at startup and every 6 hours
_update_state = {
  "latest_version": None,
  "update_available": False,
}

#################### LIFESPAN FUNCTION ####################
# This function runs on app startup and shutdown to initialize and clean up traders.
# It uses FastAPI's lifespan context manager to handle startup and shutdown events.

GITHUB_TAGS_URL = "https://api.github.com/repos/cslev/tradleware/tags"
UPDATE_CHECK_INTERVAL = int(get_env('UPDATE_CHECK_INTERVAL_S', str(6 * 3600)))


def _check_for_updates() -> None:
  """
  Queries the GitHub Tags API for the latest Tradleware version.
  Updates _update_state in-place; never raises — failures are logged and silently ignored.
  """
  try:
    resp = http_requests.get(
      GITHUB_TAGS_URL,
      headers={"Accept": "application/vnd.github+json"},
      timeout=10
    )
    resp.raise_for_status()
    tags = resp.json()
    if not tags:
      logger.debug("GitHub tags API returned an empty list — skipping update check.")
      return
    latest = tags[0]["name"]
    _update_state["latest_version"] = latest
    def _ver(v):
      return tuple(int(x) for x in re.findall(r'\d+', v))
    _update_state["update_available"] = _ver(latest) > _ver(TRADLEWARE_VERSION)
    if _update_state["update_available"]:
      logger.warning(
        f"🆕 Tradleware update available: {latest} (running {TRADLEWARE_VERSION}). "
        "Visit https://github.com/cslev/tradleware to update."
      )
    else:
      logger.debug(f"Tradleware is up to date ({TRADLEWARE_VERSION}).")
  except Exception as exc:  # pylint: disable=broad-exception-caught
    logger.debug(f"Update check failed (will retry in {UPDATE_CHECK_INTERVAL}s): {exc}")


async def _update_check_loop() -> None:
  """Background task: checks for updates at startup then every UPDATE_CHECK_INTERVAL seconds."""
  _check_for_updates()
  while True:
    await asyncio.sleep(UPDATE_CHECK_INTERVAL)
    _check_for_updates()


async def _rejection_summary_loop() -> None:
  """
  Background task: totals up collapsed webhook rejections once per window.

  A timer rather than a check on the next request, because the tail of a flood matters
  most: if the attempts stop, nothing would trigger the final summary and the count
  would sit unreported.
  """
  while True:
    await asyncio.sleep(WEBHOOK_REJECTION_SUMMARY_S)
    rejection_reporter.flush()
async def _ibkr_health_loop():
  """
  Background task: periodically probe all IBKR trader connections.
  Automatically attempts to reconnect when a connection is lost. Sends Gotify
  notifications on loss, reconnect outcome, and restoration.
  Runs every IBKR_HEALTH_CHECK_INTERVAL seconds.
  """
  # Give traders time to fully initialise before the first probe
  await asyncio.sleep(IBKR_HEALTH_CHECK_INTERVAL)
  while True:
    for bot_id, trader in traders.items():
      if not isinstance(trader, IBKRTrader):
        continue
      previous = trader.is_connected
      alive = await trader.health_check()
      if previous and not alive:
        trader.logger.error(
          f"💀 IBKR gateway connection lost for bot '{bot_id}' ({trader.symbol}). "
          "Auto-reconnecting..."
        )
      elif not previous and alive:
        trader.logger.success(
          f"✅ IBKR gateway connection restored for bot '{bot_id}' ({trader.symbol})."
        )
      elif alive:
        trader.logger.debug(
          f"IBKR health check OK for bot '{bot_id}' ({trader.symbol}): connected=True"
        )
      else:
        trader.logger.error(
          f"💀 IBKR gateway still unreachable for bot '{bot_id}' ({trader.symbol}). "
          "Retrying connection..."
        )
      # Auto-reconnect whenever the connection is down
      if not alive:
        try:
          await trader.connect()
          trader.logger.success(
            f"✅ IBKR auto-reconnect succeeded for bot '{bot_id}' ({trader.symbol})."
          )
        except Exception as reconnect_err:  # pylint: disable=broad-exception-caught
          trader.logger.error(
            f"❌ IBKR auto-reconnect failed for bot '{bot_id}' ({trader.symbol}): "
            f"{reconnect_err}. Will retry in {IBKR_HEALTH_CHECK_INTERVAL}s."
          )
    await asyncio.sleep(IBKR_HEALTH_CHECK_INTERVAL)


def api_key_findings() -> dict:
  """
  Assess every loaded bot's webhook key, keyed by bot id.

  Shared keys are reported as a separate line on top of the individual assessment,
  because reusing one key across bots removes the isolation that per-bot keys exist to
  provide, however strong the key itself is.
  """
  findings = {}
  keys_by_bot = {bot_id: getattr(trader, 'tradleware_api_key', None)
                 for bot_id, trader in traders.items()}
  shared = find_shared_keys(keys_by_bot)

  for bot_id, key in keys_by_bot.items():
    assessment = assess_key(key)
    others = shared.get(bot_id)
    if others:
      reused = f"Shared with {', '.join(sorted(others))} — one leak exposes them all."
      level = 'critical' if assessment.level == 'critical' else 'weak'
      reason = f"{assessment.reason} {reused}" if assessment.level != 'ok' else reused
      assessment = assessment._replace(level=level, reason=reason)
    if assessment.level != 'ok':
      findings[bot_id] = assessment
  return findings


def _report_weak_api_keys() -> None:
  """Log the webhook key findings once at startup. Never refuses to start."""
  findings = api_key_findings()
  if not findings:
    if traders:
      logger.info(f"Webhook API keys look sound for all {len(traders)} bot(s).")
    return
  for bot_id, assessment in findings.items():
    message = f"Webhook API key for bot '{bot_id}' is {assessment.level}: {assessment.reason}"
    if assessment.level == 'critical':
      logger.error(message)
    else:
      logger.warning(message)
  logger.warning(
    "Generate a strong webhook key with: openssl rand -hex 32 — then set it as "
    "tradleware_api_key in the bot's YAML and update the alert that sends to it."
  )


@asynccontextmanager
async def lifespan(app: FastAPI):  # pylint: disable=redefined-outer-name
  """Lifespan context manager for startup/shutdown events"""
  # Startup
  logger.info("Initializing trading configurations...")
  logger.info(f"Webhook endpoint configured at: /{WEBHOOK_PATH}")

  bot_configs = get_bot_configs()
  if bot_configs:
    logger.info("\n--- Initializing Traders ---")
    for config in bot_configs:
      bot_id = config['id']

      if config['bot_type'] == 'crypto':
        trader_class = EXCHANGE_TRADER_CLASSES.get(config['exchange'])
        if not trader_class:
          logger.warning(f"Unknown exchange '{config['exchange']}' for bot '{bot_id}'. Skipping.")
          continue
        try:
          trader = trader_class(config)
          traders[bot_id] = trader
          logger.info(f"Initialized crypto trader: {bot_id}")
          try:
            await trader.post_init()
          except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(f"Could not check pair support for {bot_id}: {exc}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
          logger.error(f"Failed to initialize crypto trader {bot_id}: {str(exc)}")

      elif config['bot_type'] == 'stock':
        trader_class = BROKER_TRADER_CLASSES.get(config['broker'])
        if not trader_class:
          logger.warning(f"Unknown broker '{config['broker']}' for bot '{bot_id}'. Skipping.")
          continue
        try:
          trader = trader_class(config)
          traders[bot_id] = trader
          logger.info(f"Initialized stock trader: {bot_id} (Symbol: {config['symbol']})")
          try:
            await trader.connect()
            logger.success(f"Connected stock trader: {bot_id}")
          except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(f"Could not connect stock trader {bot_id}: {exc}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
          logger.error(f"Failed to initialize stock trader {bot_id}: {str(exc)}")

      else:
        logger.warning(f"Unknown bot_type '{config['bot_type']}' for bot '{bot_id}'. Skipping.")
  else:
    logger.error("No bot configurations found. Check the bot_configs/ directory.")

  _report_weak_api_keys()

  # Start IBKR health-check background task
  health_task = asyncio.create_task(_ibkr_health_loop())
  logger.info(f"IBKR health-check loop started (interval: {IBKR_HEALTH_CHECK_INTERVAL}s)")

  # Start update-check background task
  update_task = asyncio.create_task(_update_check_loop())
  logger.info(f"Update-check loop started (interval: {UPDATE_CHECK_INTERVAL}s)")

  # Start the webhook rejection summariser
  summary_task = asyncio.create_task(_rejection_summary_loop())
  logger.info(
    f"Webhook rejection summaries every {WEBHOOK_REJECTION_SUMMARY_S}s "
    "(the first of each distinct problem is always reported immediately)"
  )

  yield  # Server is running here

  # Shutdown
  health_task.cancel()
  update_task.cancel()
  summary_task.cancel()
  # Report anything collapsed since the last window rather than losing the count
  rejection_reporter.flush()
  logger.info("Shutting down traders...")
  for trader in traders.values():
    await trader.close()
  # Notifications are delivered by a daemon thread, so drain the backlog before the
  # process exits or the last few would be discarded
  if not flush_gotify_queue(timeout=5.0):
    logger.warning("Gotify notifications still pending at shutdown — some were not sent.")
##########################===================###################


# Initialize FastAPI app with lifespan
app = FastAPI(
  title="Tradleware Dashboard",
  description="Web interface for Tradleware",
  lifespan=lifespan
)

# Load environment variables from .env file
# Explicitly point to the .env file in the project root
env_path = Path(__file__).parent.parent.parent / '.env'
print(f"======> Loading .env from: {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

def _env_flag(key: str, default: bool) -> bool:
  """Read a boolean environment variable, accepting true/false, 1/0, yes/no, on/off."""
  raw = get_env(key, 'true' if default else 'false')
  return str(raw).strip().lower() in ('true', '1', 'yes', 'on')


# Add session middleware for authentication
# Generate a secure session key or use one from environment
SESSION_SECRET_KEY = get_env('SESSION_SECRET_KEY') or secrets.token_urlsafe(32)
SESSION_KEY_IS_EPHEMERAL = not get_env('SESSION_SECRET_KEY')
# Mark the session cookie Secure so a browser never transmits it over plain HTTP.
# The flag reflects the browser's view of the connection, so it works normally behind
# a TLS-terminating proxy even though Tradleware itself speaks HTTP.
SESSION_HTTPS_ONLY = _env_flag('SESSION_HTTPS_ONLY', True)
# Session lifetime. Starlette's own default is 14 days; these sessions are signed
# cookies with no server-side store, so a stolen cookie stays valid until it expires
# and logging out cannot revoke it. A shorter life is the only bound on that.
SESSION_MAX_AGE_S = int(get_env('SESSION_MAX_AGE_S', '43200'))
app.add_middleware(
  SessionMiddleware,
  secret_key=SESSION_SECRET_KEY,
  max_age=SESSION_MAX_AGE_S,
  https_only=SESSION_HTTPS_ONLY,
  same_site='lax'
)

# Get webhook path from environment (default to 'webhook')
WEBHOOK_PATH = get_env('WEBHOOK_PATH', 'webhook').strip('/')  # Strip leading/trailing slashes
# A predictable path invites automated scanners. Not enforced — the API key is what
# actually protects the endpoint — but surfaced on the dashboard so it is a choice
# rather than an oversight.
USING_DEFAULT_WEBHOOK_PATH = WEBHOOK_PATH == 'webhook'
IBKR_HEALTH_CHECK_INTERVAL = int(get_env('IBKR_HEALTH_CHECK_INTERVAL_S', '1800'))

# Webhook replay protection — how far the signal's own timestamp may be from now,
# in seconds, in either direction
WEBHOOK_MAX_AGE_DEFAULT_S = 300
# Floor for the window: below this, ordinary clock drift between the signal source
# and this host starts rejecting valid signals
WEBHOOK_MAX_AGE_FLOOR_S = 30


def _read_freshness_window() -> tuple:
  """
  Read WEBHOOK_MAX_AGE_S, clamped to a sane range.

  The window can be widened or narrowed, but never switched off: a webhook whose
  timestamp is not checked stays replayable forever, which is the whole point of the
  control. Returns (seconds, note) where note is a message for the logger, which does
  not exist yet at this point in startup, or None.
  """
  raw = get_env('WEBHOOK_MAX_AGE_S', str(WEBHOOK_MAX_AGE_DEFAULT_S))
  try:
    value = int(float(str(raw).strip()))
  except (TypeError, ValueError):
    return WEBHOOK_MAX_AGE_DEFAULT_S, (
      f"WEBHOOK_MAX_AGE_S='{raw}' is not a number — using "
      f"{WEBHOOK_MAX_AGE_DEFAULT_S}s."
    )
  if value < WEBHOOK_MAX_AGE_FLOOR_S:
    return WEBHOOK_MAX_AGE_FLOOR_S, (
      f"WEBHOOK_MAX_AGE_S={value} is below the {WEBHOOK_MAX_AGE_FLOOR_S}s floor and "
      "would reject valid signals on ordinary clock drift — using the floor."
    )
  return value, None


WEBHOOK_MAX_AGE_S, _WEBHOOK_MAX_AGE_NOTE = _read_freshness_window()


# Refuse webhooks that did not reach Tradleware over TLS. The bot's API key travels
# inside the request body, so a cleartext delivery hands it to anyone on the path,
# who can then place orders of their own — replay protection does not help against
# that, because they can mint a brand new signal. Tradleware does not terminate TLS
# itself, so in practice this requires a TLS-terminating proxy plus TRUSTED_PROXIES.
WEBHOOK_REQUIRE_HTTPS = _env_flag('WEBHOOK_REQUIRE_HTTPS', True)

# How often repeated webhook rejections are collapsed into a single summary line. The
# first of each distinct problem is always reported immediately; this only governs how
# often the repeats behind it are totalled up.
WEBHOOK_REJECTION_SUMMARY_S = int(get_env('WEBHOOK_REJECTION_SUMMARY_S', '300'))

# Failed webhook authentications tolerated from one address per window before it stops
# being answered. Far above anything a misconfigured alert produces, far below a useful
# guessing rate: 20 per minute turns a million-word list from hours into weeks.
WEBHOOK_FAILURE_LIMIT = int(get_env('WEBHOOK_FAILURE_LIMIT', '20'))
WEBHOOK_FAILURE_WINDOW_S = int(get_env('WEBHOOK_FAILURE_WINDOW_S', '60'))

# How long a request will wait for another operation on the same bot to finish before
# giving up with 503. Needs to comfortably exceed a normal order round-trip — IBKR
# polls for fills, so it is the slow case — without letting a stuck call queue requests
# up forever.
TRADER_LOCK_TIMEOUT_S = int(get_env('TRADER_LOCK_TIMEOUT_S', '60'))
# Where accepted signal fingerprints are remembered across restarts. Defaults into
# the logs directory because that is the only path persisted by docker-compose.
WEBHOOK_REPLAY_DB = get_env(
  'WEBHOOK_REPLAY_DB',
  str(Path(__file__).resolve().parent.parent / 'logs' / 'webhook_replay.json')
)

# Authentication configuration
DASHBOARD_USERNAME = get_env('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASSWORD = get_env('DASHBOARD_PASSWORD', 'changeme')
# True when the shipped defaults are still in place or either field is empty.
# Surfaced as a startup warning and as the banner on the login page.
USING_DEFAULT_CREDENTIALS = (
  (DASHBOARD_USERNAME == 'admin' and DASHBOARD_PASSWORD == 'changeme')
  or not DASHBOARD_USERNAME
  or not DASHBOARD_PASSWORD
)

# Fetch the server's public IP once at startup and cache it
def _fetch_public_ip() -> str:
  """Query a lightweight public-IP echo service to determine outbound IP."""
  for url in ('https://api.ipify.org', 'https://icanhazip.com'):
    try:
      resp = http_requests.get(url, timeout=5)
      return resp.text.strip()
    except Exception:  # pylint: disable=broad-exception-caught
      continue
  return 'unavailable'

SERVER_PUBLIC_IP = _fetch_public_ip()
TRUSTED_IPS = [ip.strip() for ip in get_env('TRUSTED_IPS', '').split(',') if ip.strip()]


def _parse_ip_networks(raw: str) -> tuple:
  """
  Parse a comma-separated list of IP addresses and/or CIDR blocks.

  Bare addresses are widened to single-host networks (/32, /128) so membership
  tests are uniform. Never raises: malformed entries are returned to the caller
  instead of being logged, because this runs before the logger is constructed.

  Returns:
    tuple: (list of ip_network objects, list of rejected raw entries)
  """
  networks = []
  invalid = []
  for entry in raw.split(','):
    entry = entry.strip()
    if not entry:
      continue
    try:
      networks.append(ipaddress.ip_network(entry, strict=False))
    except ValueError:
      invalid.append(entry)
  return networks, invalid


# Reverse proxies that are allowed to speak for their clients.
# Forwarded headers (X-Forwarded-For, X-Real-IP, X-Forwarded-Proto) are set by
# whoever sends the request, so they are only honoured when the direct TCP peer
# matches one of these addresses or CIDR blocks. With no trusted proxy
# configured (the default) the connection address is always authoritative —
# otherwise anyone could grant themselves a trusted IP by sending a header.
TRUSTED_PROXIES, _INVALID_PROXIES = _parse_ip_networks(get_env('TRUSTED_PROXIES', ''))

# Initialize a logger for the FastAPI app
# Ensure CustomLogger is correctly imported from src.misc.logger
logger = CustomLogger(name='Tradleware',
                      gotify_url=get_env('GOTIFY_SERVER_URL'),
                      gotify_token=get_env('GOTIFY_APP_TOKEN'),
                      gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30')))

# Log authentication configuration at startup.
# The password is never logged: this goes to the console, to tradleware_data/logs,
# and is pushed to the Gotify server when GOTIFY_LOG_LEVEL is set to INFO or lower.
logger.info(f"Dashboard credentials loaded - Username: '{DASHBOARD_USERNAME}' (password hidden)")
if USING_DEFAULT_CREDENTIALS:
  logger.warning(
    "Dashboard is using default or empty credentials — set DASHBOARD_USERNAME and "
    "DASHBOARD_PASSWORD in .env before exposing Tradleware to any network."
  )
if TRUSTED_IPS:
  logger.info(f"Trusted IPs configured: {', '.join(TRUSTED_IPS)}")
else:
  logger.warning("No trusted IPs configured. All access requires authentication.")

# Log trusted-proxy configuration — this decides whether forwarded headers count
if _INVALID_PROXIES:
  logger.error(
    "Ignoring invalid TRUSTED_PROXIES entries (expected an IP address or CIDR block): "
    f"{', '.join(_INVALID_PROXIES)}"
  )
if TRUSTED_PROXIES:
  logger.info(
    f"Trusted proxies configured: {', '.join(str(net) for net in TRUSTED_PROXIES)} — "
    "forwarded headers are honoured only from these peers."
  )
elif TRUSTED_IPS:
  logger.info(
    "No trusted proxies configured — X-Forwarded-For / X-Real-IP are ignored and "
    "TRUSTED_IPS is matched against the direct connection address. Set TRUSTED_PROXIES "
    "if Tradleware runs behind a reverse proxy or tunnel."
  )

# Webhook replay protection — a signal must be recent, and is accepted only once
if _WEBHOOK_MAX_AGE_NOTE:
  logger.warning(_WEBHOOK_MAX_AGE_NOTE)
logger.info(
  f"Webhook freshness window: {WEBHOOK_MAX_AGE_S}s. Signals must carry a current "
  "timestamp — TradingView alerts must send {{timenow}}, not {{time}} (bar time)."
)
if WEBHOOK_MAX_AGE_S > 3600:
  logger.warning(
    f"Webhook freshness window is {WEBHOOK_MAX_AGE_S}s — a captured signal stays "
    "replayable for that long. Keep it as short as your signal source allows."
  )
# A fingerprint has to stay cached for as long as its signal could still pass the
# freshness check, otherwise the signal becomes replayable again the moment the
# fingerprint expires. A signal may be dated up to WEBHOOK_MAX_AGE_S in the future and
# still be accepted, and it goes on passing the freshness check until
# WEBHOOK_MAX_AGE_S past that timestamp — so in the worst case it must be remembered
# for twice the window, counted from when it was first accepted.
replay_guard = ReplayGuard(WEBHOOK_REPLAY_DB, WEBHOOK_MAX_AGE_S * 2, logger)
# Collapses repeated webhook rejections so a flood cannot bury real alerts or push the
# useful history out of the rotating log file
rejection_reporter = RejectionReporter(logger, WEBHOOK_REJECTION_SUMMARY_S)
# Stops one address guessing keys at full speed. Never applied to loopback or to
# TRUSTED_IPS, so a local script or the kiosk cannot lock itself out.
failure_limiter = FailureLimiter(WEBHOOK_FAILURE_LIMIT, WEBHOOK_FAILURE_WINDOW_S)

# Session cookie configuration
logger.info(
  f"Session cookie: Secure={SESSION_HTTPS_ONLY}, HttpOnly=True, SameSite=lax, "
  f"lifetime {SESSION_MAX_AGE_S}s."
)
if not SESSION_HTTPS_ONLY:
  logger.warning(
    "⚠️  Session cookie is not marked Secure (SESSION_HTTPS_ONLY=false) — it will be "
    "sent over plain HTTP, where anyone on the path can copy it and take over the "
    "dashboard session. Only acceptable on a trusted LAN."
  )
elif not TRUSTED_PROXIES:
  logger.info(
    "Session cookie is marked Secure. Signing in requires an HTTPS connection, so "
    "either reach Tradleware through a TLS-terminating proxy with TRUSTED_PROXIES set, "
    "or use TRUSTED_IPS for local access."
  )
if SESSION_KEY_IS_EPHEMERAL:
  logger.info(
    "No SESSION_SECRET_KEY set — a random one was generated, so everyone is signed out "
    "whenever Tradleware restarts. Set it in .env to keep sessions across restarts."
  )

# Webhook transport security
if WEBHOOK_REQUIRE_HTTPS:
  logger.info("Webhooks must arrive over HTTPS (WEBHOOK_REQUIRE_HTTPS=true).")
  if not TRUSTED_PROXIES:
    logger.warning(
      "⚠️  Webhooks require HTTPS but no TRUSTED_PROXIES are configured. Tradleware "
      "does not terminate TLS itself, so every webhook will be rejected unless uvicorn "
      "was started with --ssl-keyfile/--ssl-certfile. Put Tradleware behind a "
      "TLS-terminating proxy and set TRUSTED_PROXIES to the address it connects from."
    )
else:
  logger.warning(
    "⚠️  Webhook HTTPS enforcement is DISABLED — the bot API key crosses the network in "
    "cleartext, and anyone who can observe one request can place their own orders. "
    "Only acceptable when the signal source is on this host or a trusted LAN."
  )


# Mount static files (for CSS, JS, images). Paths are now relative to /src/ui/
# BUT, FastAPI needs the path relative to the app's *startup directory*
# The 'directory="static"' refers to the '/static' folder.
app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")

# Configure Jinja2 templates. Similarly, path is relative to root.
templates = Jinja2Templates(directory="src/ui/templates")


def mask_secret(value, visible: int = 4) -> str:
  """
  Render a live secret as a fixed-width mask plus at most `visible` trailing characters.

  Enough to tell two keys apart on the dashboard, not enough to be useful in a
  screenshot, a screen share, or over someone's shoulder. The mask is a fixed width
  so it does not leak the length of the secret, and secrets too short to mask
  meaningfully are hidden entirely.
  """
  if not value:
    return 'Not configured'
  text = str(value)
  suffix = text[-visible:] if len(text) > visible * 2 else ''
  return f"{'•' * 8}{suffix}"


templates.env.filters['mask_secret'] = mask_secret


# Separators that carry no meaning in a trading pair. Venues spell the same pair
# several ways — CCXT wants 'BTC/USDC', TradingView's {{ticker}} expands to the
# venue-native 'BTCUSDC', Coinbase writes 'BTC-USDC'.
#
# ':' is deliberately NOT in this set. CCXT writes perpetuals as 'BTC/USDT:USDT',
# where the suffix names the settlement currency and is part of the instrument's
# identity, not punctuation. Keeping it significant also lets a venue-native perp
# spelling ('BTCUSDT:USDT') still match its configured form.
_TICKER_SEPARATORS = re.compile(r'[/\-_\s]+')


def canonical_ticker(value) -> str:
  """
  Strip separators and case from a ticker so the same pair spelled different ways compares equal.

  Used only to decide whether an incoming ticker refers to the bot's configured
  pair — never to build the symbol sent to an exchange. The configured spelling
  stays authoritative, because 'BTCUSDC' cannot be split back into base/quote
  without a currency table.
  """
  if not value:
    return ''
  return _TICKER_SEPARATORS.sub('', str(value)).upper()


def resolve_ticker(received, expected, bot_logger) -> bool:
  """
  Decide whether `received` names the bot's `expected` pair, tolerating spelling differences.

  Returns True on an exact match, True with a warning when the two agree only after
  normalisation, and False when they name different instruments. Callers must replace
  the received value with `expected` on a True result — downstream code splits the
  symbol on '/' and passes it to the exchange, so a separator-less form would raise
  IndexError after the order had already been placed.
  """
  if received == expected:
    return True
  if canonical_ticker(received) == canonical_ticker(expected):
    bot_logger.warning(
      f"Ticker '{received}' does not exactly match the configured '{expected}'; "
      f"accepted on a normalised match and treated as '{expected}'. Set the alert to "
      f"send '{expected}' verbatim — normalisation is a fallback, not a contract."
    )
    return True
  return False


#################### AUTHENTICATION HELPERS ####################

def _parse_ip(value: str):
  """Return an ip_address for `value`, or None when it is not a valid IP literal."""
  try:
    parsed = ipaddress.ip_address(value.strip())
  except (ValueError, AttributeError):
    return None
  # Normalise IPv4-mapped IPv6 (e.g. '::ffff:192.168.1.5') to plain IPv4
  return parsed.ipv4_mapped if getattr(parsed, 'ipv4_mapped', None) else parsed

def is_trusted_proxy(address: str) -> bool:
  """Check if an address belongs to one of the configured trusted reverse proxies"""
  if not TRUSTED_PROXIES:
    return False
  parsed = _parse_ip(address)
  if parsed is None:
    return False
  return any(parsed in network for network in TRUSTED_PROXIES)

def get_client_ip(request: Request) -> str:
  """
  Get the real client IP address, accounting for proxies.

  Forwarded headers are supplied by the sender and can claim anything, so they are
  only honoured when the direct TCP peer is a configured trusted proxy. For every
  other peer the connection address is authoritative — otherwise a plain
  `curl -H 'X-Forwarded-For: <trusted ip>'` would be enough to pass as trusted.
  """
  peer = request.client.host if request.client else "unknown"
  if not is_trusted_proxy(peer):
    return peer

  # The peer is a trusted proxy: walk X-Forwarded-For right to left and take the
  # rightmost hop that is not itself a trusted proxy. Entries further left were
  # appended by an upstream we do not control and may be client-supplied.
  forwarded = request.headers.get("X-Forwarded-For", "")
  for hop in reversed(forwarded.split(",")):
    hop = hop.strip()
    if not hop:
      continue
    if _parse_ip(hop) is None:
      break  # malformed chain — stop trusting the rest of it
    if not is_trusted_proxy(hop):
      return hop

  # No usable X-Forwarded-For — fall back to X-Real-IP from that same trusted hop
  real_ip = request.headers.get("X-Real-IP", "").strip()
  if real_ip and _parse_ip(real_ip) is not None:
    return real_ip

  # Only trusted proxies in the chain, or no forwarded headers at all
  return peer

#################### PER-TRADER EXECUTION LOCKS ####################

# One lock per bot. Anything that reads a balance and then acts on it must hold the
# bot's lock for the whole read-then-order sequence, otherwise two overlapping
# requests both size their order from the same pre-trade balance and overspend.
# Keyed by trader_id, so different bots never wait for each other.
_TRADER_LOCKS = {}


def get_trader_lock(trader_id: str) -> asyncio.Lock:
  """
  Return the execution lock for one bot, creating it on first use.

  Safe without a lock of its own: there is no await between the lookup and the
  insert, so the event loop cannot interleave another coroutine in between.
  """
  lock = _TRADER_LOCKS.get(trader_id)
  if lock is None:
    lock = _TRADER_LOCKS[trader_id] = asyncio.Lock()
  return lock


@asynccontextmanager
async def trader_execution_lock(trader_id: str):
  """
  Serialise order execution for one bot across every entry point.

  Held by the webhook handler and by the dashboard's convert endpoint, so a signal
  arriving while a manual conversion is in flight queues behind it instead of sizing
  its order from a balance that is about to change. Waiting is the correct behaviour —
  the second signal then acts on the settled balance, which is what sequential
  execution means — but the wait is bounded so a wedged exchange call cannot stack
  requests up indefinitely.
  """
  lock = get_trader_lock(trader_id)
  if lock.locked():
    logger.info(f"Another operation is in flight for '{trader_id}' — queueing behind it.")
  try:
    await asyncio.wait_for(lock.acquire(), timeout=TRADER_LOCK_TIMEOUT_S)
  except asyncio.TimeoutError as exc:
    logger.error(
      f"Timed out after {TRADER_LOCK_TIMEOUT_S}s waiting for '{trader_id}' to finish its "
      "current operation — refusing rather than acting on a stale balance."
    )
    raise HTTPException(
      status_code=503,
      detail=f"Trader '{trader_id}' is busy; try again."
    ) from exc
  try:
    yield
  finally:
    lock.release()


def is_loopback(address: str) -> bool:
  """
  Check if an address is a loopback address.

  Requests from loopback are the container's own health check — `curl /` every 30
  seconds — so they are left out of the access logs, which they would otherwise drown.
  This is a logging filter only and never grants access: reaching Tradleware over
  loopback still requires a session or an entry in TRUSTED_IPS.
  """
  parsed = _parse_ip(address)
  return parsed is not None and parsed.is_loopback

def is_trusted_ip(client_ip: str) -> bool:
  """Check if the client IP is in the trusted IPs list"""
  if not TRUSTED_IPS:
    return False
  return client_ip in TRUSTED_IPS

def is_authenticated(request: Request) -> bool:
  """Check if user is authenticated (either by session or trusted IP)"""
  client_ip = get_client_ip(request)

  # Check if IP is trusted first (bypass authentication)
  if is_trusted_ip(client_ip):
    if not is_loopback(client_ip):
      logger.debug(f"Access granted from trusted IP: {client_ip}")
    return True

  # Check session authentication
  return request.session.get("authenticated", False)

def require_auth(request: Request):
  """Dependency that requires authentication. Raises HTTPException if not authenticated."""
  if not is_authenticated(request):
    client_ip = get_client_ip(request)
    logger.warning(f"Unauthorized access attempt from IP: {client_ip}")
    raise HTTPException(status_code=401, detail="Authentication required")

# Header the dashboard sets on requests that change something. A page on another site
# cannot set a custom header without the browser first sending a CORS preflight, which
# Tradleware does not answer, so the real request is never sent. The session cookie is
# already covered by SameSite=lax, but the TRUSTED_IPS path has no cookie for SameSite
# to govern: without this, any page loaded in a browser on a trusted address could fire
# an authenticated POST at Tradleware and spend the balance.
#
# Do not add a permissive CORS middleware. Answering preflights with
# Access-Control-Allow-Origin: * (or echoing the caller's origin) tells the browser the
# custom header is allowed cross-site, and this protection disappears without any code
# here changing. tests/test_csrf.py fails if that happens.
CSRF_HEADER = 'X-Tradleware-Request'


def require_dashboard_request(request: Request) -> None:
  """
  Require that a state-changing request came from the Tradleware dashboard itself.

  Only guards actions with side effects. Read-only endpoints need no equivalent: a
  cross-site page can send the request but the same-origin policy stops it reading the
  reply, so nothing leaks.
  """
  if request.headers.get(CSRF_HEADER) != '1':
    client_ip = get_client_ip(request)
    origin = request.headers.get('Origin') or request.headers.get('Referer') or 'none'
    logger.warning(
      f"Refused a state-changing request from {client_ip} that did not come from the "
      f"dashboard (origin: {origin[:60]}). This is what a cross-site request forgery "
      "attempt looks like; it is also what an outdated cached copy of the dashboard "
      "JavaScript looks like, so try a hard refresh if you triggered it yourself."
    )
    raise HTTPException(
      status_code=403,
      detail="This action must be triggered from the Tradleware dashboard."
    )

def is_request_secure(request: Request) -> bool:
  """Check if the client reached Tradleware over HTTPS"""
  # Check X-Forwarded-Proto header (used by most proxies/tunnels), but only when it
  # comes from a trusted proxy — a direct client could otherwise claim HTTPS on a
  # plain HTTP connection and hide the insecure-connection warning.
  peer = request.client.host if request.client else ""
  if is_trusted_proxy(peer):
    xf_proto = request.headers.get("X-Forwarded-Proto")
    if xf_proto:
      # Comma-separated when proxies are chained; the first entry is the scheme
      # the browser actually used.
      return xf_proto.split(",")[0].strip().lower() == "https"
  # Fallback to scheme (for direct access)
  return request.url.scheme == "https"

#################### AUTHENTICATION ROUTES ####################

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
  """Display the login page"""
  # Get client IP address
  client_ip = get_client_ip(request)
  logger.debug(f"Login page accessed from IP: {client_ip}")

  # If already authenticated, redirect to dashboard
  if is_authenticated(request):
    return RedirectResponse(url="/", status_code=303)

  # Get error message from query params if login failed
  error = request.query_params.get("error")

  # Check if connection is secure (HTTPS)
  is_secure = is_request_secure(request)

  return templates.TemplateResponse(
    request,
    "login.html",
    {
      "error": error,
      "using_defaults": USING_DEFAULT_CREDENTIALS,
      "is_secure": is_secure,
      "version": TRADLEWARE_VERSION  # Pass application version
    }
  )

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
  """Handle login form submission"""
  client_ip = get_client_ip(request)

  # Log login attempt
  logger.info(f"Login attempt from IP: {client_ip}, Username: '{username}'")

  # Verify credentials using constant-time comparison to prevent timing attacks.
  # Compared as bytes, not str: compare_digest rejects non-ASCII strings with a
  # TypeError, so a password containing an accent would crash the request instead of
  # signing the user in — and any client could raise a 500 here with a non-ASCII name.
  username_match = secrets.compare_digest(username.encode('utf-8'),
                                          str(DASHBOARD_USERNAME).encode('utf-8'))
  password_match = secrets.compare_digest(password.encode('utf-8'),
                                          str(DASHBOARD_PASSWORD).encode('utf-8'))

  if username_match and password_match:
    # A Secure cookie is discarded by the browser on a plain HTTP page, so the login
    # would appear to succeed and then bounce straight back here forever. Say so
    # instead of looping.
    if SESSION_HTTPS_ONLY and not is_request_secure(request):
      logger.error(
        f"Login by '{username}' from {client_ip} succeeded but the session cookie "
        "cannot be set: it is marked Secure and this connection is plain HTTP. Reach "
        "the dashboard over HTTPS, or set SESSION_HTTPS_ONLY=false for LAN-only use."
      )
      return RedirectResponse(
        url="/login?error=HTTPS+is+required+to+sign+in.+Reach+the+dashboard+over+HTTPS"
            "+or+set+SESSION_HTTPS_ONLY%3Dfalse",
        status_code=303
      )
    # Set session as authenticated
    request.session["authenticated"] = True
    logger.debug(f"✓ Successful login from IP: {client_ip}")
    return RedirectResponse(url="/", status_code=303)

  # Log failed attempt
  logger.warning(f"✗ Failed login attempt from IP: {client_ip}, Username: '{username}'")
  logger.warning(f"Failed login attempt from IP: {client_ip} with username: {username}")
  return RedirectResponse(url="/login?error=Invalid+credentials", status_code=303)

@app.get("/logout")
async def logout(request: Request):
  """Handle logout"""
  client_ip = get_client_ip(request)
  request.session.clear()
  logger.debug(f"User logged out from IP: {client_ip}")
  return RedirectResponse(url="/login", status_code=303)

#################### DASHBOARD ROUTES ####################

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
  """Renders the main index.html page with trader cards."""
  # Get client IP address
  client_ip = get_client_ip(request)

  # Check authentication
  if not is_authenticated(request):
    if not is_loopback(client_ip):
      logger.warning(f"Unauthenticated access attempt to dashboard from IP: {client_ip}")
    return RedirectResponse(url="/login", status_code=303)

  if not is_loopback(client_ip):
    logger.debug(f"Dashboard accessed from IP: {client_ip}")

  # Get log refresh interval from environment (default to 5000ms = 5 seconds)
  log_refresh_interval = int(get_env('LOG_REFRESH_INTERVAL_MS', '5000'))

  # Check if connection is secure (HTTPS)
  is_secure = is_request_secure(request)

  # Check if accessing from trusted IP
  from_trusted_ip = is_trusted_ip(client_ip)

  return templates.TemplateResponse(
    request,
    "index.html",
    {
      "title": "Tradleware Dashboard",
      "traders": traders,  # Add the traders dictionary we defined globally
      "log_refresh_interval": log_refresh_interval,  # Pass the refresh interval to template
      "webhook_path": WEBHOOK_PATH,  # Pass the configured webhook path to template
      "using_default_webhook_path": USING_DEFAULT_WEBHOOK_PATH,  # Nudge to randomize it
      "api_key_findings": api_key_findings(),  # Weak or placeholder webhook keys, per bot
      "is_secure": is_secure,  # Pass connection security status
      "is_trusted_ip": from_trusted_ip,  # Pass trusted IP status
      "client_ip": client_ip,  # Pass client IP for display
      "server_public_ip": SERVER_PUBLIC_IP,  # Pass server public IP for display
      "version": TRADLEWARE_VERSION,  # Pass application version
      "update_available": _update_state["update_available"],
      "latest_version": _update_state["latest_version"],
    }
  )

# Add the balance endpoint - this will be called from the UI to fetch trader balances
# by clicking on the refresh button in the UI.
@app.get("/balance/{trader_id}")
async def get_balance(request: Request, trader_id: str):
  """Fetch balance for a specific trader"""
  # Check authentication
  if not is_authenticated(request):
    return JSONResponse(status_code=401, content={"error": "Authentication required"})

  if trader_id not in traders:
    return JSONResponse(
      status_code=404,
      content={"error": f"Trader {trader_id} not found"}
    )

  trader = traders[trader_id]

  # Check if it's a stock trader
  if trader.bot_type == "stock":
    return JSONResponse(
      status_code=400,
      content={"error": f"Use /position/{trader_id} endpoint for stock traders"}
    )

  traders[trader_id].logger.debug(f"asking for balance of {trader_id}")
  try:
    raw_balance = await traders[trader_id].fetch_balance()
    fiat = traders[trader_id].stablecoin_fiat_pair.split('/')[1]  # Extract fiat currency from the pair
    stablecoin = traders[trader_id].stablecoin_fiat_pair.split('/')[0]  # Extract stablecoin from the pair
    crypto = traders[trader_id].crypto_stablecoin_pair.split('/')[0]  # Extract crypto symbol from the trader

    # logger.debug(f"Raw balance for {trader_id}: {raw_balance}")

    # Parse balances from the 'total' section
    total_balances = raw_balance.get('total', {})

    # free_balances = raw_balance.get('free', {})

    # Format balance into the expected structure
    balance = {
      "fiat": f"{total_balances.get(fiat, 0.0):.2f}",
      "fiat_unit": fiat,
      "stablecoin": f"{total_balances.get(stablecoin, 0.0):.2f}",
      "stablecoin_unit": stablecoin,
      "crypto": f"{total_balances.get(crypto, 0.0):.8f}",  # More decimal places for crypto
      "crypto_unit": crypto,
    }

    logger.debug(balance)
    return {"balance": balance}
  except Exception as exc:
    error_msg = str(exc)
    # Log to both main logger and trader's logger so it appears in Recent Logs
    logger.error(f"Error for {trader_id}: {error_msg}")
    traders[trader_id].logger.error(f"❌ Error fetching balance: {error_msg}")
    return JSONResponse(
      status_code=500,
      content={"error": error_msg}
    )

@app.get("/price/{trader_id}")
async def get_price(request: Request, trader_id: str):
  """Fetch current market price for a crypto trader's configured trading pair."""
  if not is_authenticated(request):
    return JSONResponse(status_code=401, content={"error": "Authentication required"})

  if trader_id not in traders:
    return JSONResponse(status_code=404, content={"error": f"Trader {trader_id} not found"})

  trader = traders[trader_id]
  if trader.bot_type != "crypto":
    return JSONResponse(status_code=400, content={"error": "Price endpoint is for crypto traders only"})

  pair = trader.crypto_stablecoin_pair
  quote_currency = pair.split('/')[1] if '/' in pair else ''

  try:
    ticker = await trader._safe_api_call(trader.exchange.fetch_ticker, pair)  # pylint: disable=protected-access
    if not ticker:
      trader.logger.warning(f"fetch_ticker returned None for {pair}")
      return JSONResponse(status_code=502, content={"error": f"No ticker data returned for {pair}"})

    # Try fields in order of preference
    price = ticker.get('last') or ticker.get('close') or ticker.get('bid') or ticker.get('ask')
    if not price or float(price) <= 0:
      trader.logger.warning(f"Ticker for {pair} returned no usable price: {ticker}")
      return JSONResponse(status_code=502, content={"error": f"Ticker returned no usable price for {pair}"})

    return {"price": float(price), "quote_currency": quote_currency, "pair": pair}

  except Exception as exc:
    error_msg = str(exc)
    trader.logger.error(f"Error fetching price for {pair}: {error_msg}")
    return JSONResponse(status_code=500, content={"error": error_msg})


@app.get("/position/{trader_id}")
async def get_position(request: Request, trader_id: str):
  """Fetch position and market data for a stock trader"""
  # Check authentication
  if not is_authenticated(request):
    return JSONResponse(status_code=401, content={"error": "Authentication required"})

  if trader_id not in traders:
    return JSONResponse(
      status_code=404,
      content={"error": f"Trader {trader_id} not found"}
    )

  trader = traders[trader_id]

  # Check if it's a stock trader
  if trader.bot_type != "stock":
    return JSONResponse(
      status_code=400,
      content={"error": f"Use /balance/{trader_id} endpoint for crypto traders"}
    )

  try:
    # Fetch position data
    position = await trader.fetch_positions()

    # Fetch current price
    current_price = await trader.get_market_price()

    # Get market status
    market_status = trader.get_market_status()
    can_trade = trader.can_trade_now()
    time_until_open = trader.get_time_until_market_opens()

    # Ensure JSON-safe values (handle None, NaN, Infinity)
    def make_json_safe(value):
      if value is None:
        return 0.0
      if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
          return 0.0
      return value

    return {
      "position": {
        "symbol": position['symbol'],
        "quantity": position['quantity'],
        "unrealized_pnl": make_json_safe(position['unrealized_pnl']),
        "unrealized_pnl_pct": make_json_safe(position['unrealized_pnl_pct']),
        "cash": make_json_safe(position.get('cash', 0.0))
      },
      "current_price": make_json_safe(current_price),
      "market": {
        "status": market_status,
        "can_trade": can_trade,
        "time_until_open": time_until_open
      }
    }
  except Exception as exc:
    error_msg = str(exc)
    logger.error(f"Error fetching position for {trader_id}: {error_msg}")
    trader.logger.error(f"❌ Error fetching position: {error_msg}")

    # Check if it's a connection error
    is_connection_error = "connection failed" in error_msg.lower() or "cannot connect" in error_msg.lower() or not trader.is_connected

    return JSONResponse(
      status_code=500,
      content={
        "error": error_msg,
        "is_connection_error": is_connection_error,
        "is_connected": trader.is_connected
      }
    )

@app.post(f"/{WEBHOOK_PATH}")
async def handle_webhook(request: Request):

  """
  Handles incoming webhooks with per-bot API key authentication.
  Expects JSON with: api_key, trader_id, ticker, action, timestamp, alert_name.

  Note: The webhook path is configurable via WEBHOOK_PATH environment variable.
  Default: /webhook, but can be set to any random string for security (e.g., /x7f9k2m4p8)
  """
  ########################################################################
  ## TRANSPORT SECURITY
  ## The bot's API key is carried in the body, so a cleartext delivery leaks a
  ## working trading credential to anyone on the path. Checked before the body is
  ## read: if the transport is wrong, the credential has already been exposed.
  ########################################################################
  if WEBHOOK_REQUIRE_HTTPS and not is_request_secure(request):
    peer = request.client.host if request.client else "unknown"
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    if forwarded_proto and not is_trusted_proxy(peer):
      reason = (
        f"it arrived from {peer}, which is not in TRUSTED_PROXIES, so its "
        "X-Forwarded-Proto header cannot be believed. Set TRUSTED_PROXIES to the "
        "address your TLS-terminating proxy connects from"
      )
    elif forwarded_proto:
      reason = (
        f"the proxy reported X-Forwarded-Proto: {forwarded_proto[:20]} — the client "
        "reached the proxy over plain HTTP"
      )
    else:
      reason = (
        "it arrived over plain HTTP. Tradleware does not serve HTTPS itself: put it "
        "behind a TLS-terminating proxy and set TRUSTED_PROXIES"
      )
    rejection_reporter.record("delivered without TLS", peer, reason)
    raise HTTPException(
      status_code=403,
      detail="Webhooks must be delivered over HTTPS."
    )

  client_ip = get_client_ip(request)

  ########################################################################
  ## GUESSING THROTTLE
  ## Checked before the body is read, so a source that is already guessing costs
  ## nothing to turn away. Loopback and trusted addresses are never throttled: a local
  ## script or the kiosk must not be able to lock itself out. A genuine signal source
  ## never lands here, because it does not send wrong keys — and one success clears the
  ## count, so a briefly misconfigured source recovers as soon as it is fixed.
  ########################################################################
  throttled = (not is_loopback(client_ip) and not is_trusted_ip(client_ip)
               and failure_limiter.is_blocked(client_ip))
  if throttled:
    retry_after = failure_limiter.seconds_until_clear(client_ip)
    rejection_reporter.record(
      f"too many failed attempts, not answering for {retry_after}s", client_ip)
    raise HTTPException(
      status_code=429,
      detail="Too many failed webhook attempts. Try again shortly.",
      headers={"Retry-After": str(retry_after)})

  ## reading JSON body with error handling
  try:
    body = await request.body()
    data = json.loads(body.decode('utf-8'))
  except json.JSONDecodeError as exc:
    # Only the parser's own complaint is logged. The body itself is attacker-supplied,
    # and echoing hundreds of its characters into a size-rotated log lets anyone choose
    # what fills it — and how fast the real history is pushed out.
    rejection_reporter.record("malformed JSON body", client_ip, str(exc)[:100])
    raise HTTPException(
      status_code=400,
      detail=f"Malformed JSON in webhook request: {str(exc)[:200]}") from exc
  except Exception as exc:
    rejection_reporter.record("unreadable request body", client_ip, str(exc)[:100])
    raise HTTPException(
      status_code=400,
      detail=f"Error reading webhook request body: {str(exc)[:200]}") from exc

  # Valid JSON is not necessarily an object — every field lookup below assumes one
  if not isinstance(data, dict):
    rejection_reporter.record(f"body was a JSON {type(data).__name__}, not an object",
                              client_ip)
    raise HTTPException(status_code=400, detail="Webhook body must be a JSON object.")

  ########################################################################
  ## Check if the trader_id is set properly and we indeed have such a BOT
  ########################################################################
  trader_id = data.get("trader_id")
  if not trader_id:
    rejection_reporter.record("no trader_id in the payload", client_ip)
    raise HTTPException(status_code=400, detail="Missing field: trader_id")
  if trader_id not in traders:
    # The submitted id is not echoed: it is attacker-chosen text going into the log
    rejection_reporter.record("unknown trader_id", client_ip)
    raise HTTPException(status_code=404, detail=f"Trader ID '{trader_id}' not found")

  ############################
  ## API KEY VALIDATION
  ############################
  api_key = data.get("api_key")
  trader = traders[trader_id]
  expected_api_key = trader.tradleware_api_key
  if not expected_api_key:
    trader.logger.error(f"No Tradleware API key configured for trader {trader_id}")
    raise HTTPException(status_code=500, detail="Trader is not configured with a Tradleware API key")
  # Constant-time comparison: a plain `!=` returns as soon as two bytes differ, so the
  # response time reveals how much of a guess was correct and the key can be recovered
  # one character at a time. Compared as bytes so a non-ASCII or non-string value is
  # rejected rather than raising, the same way the login form does it.
  provided_key = str(api_key).encode('utf-8') if api_key else b''
  if not secrets.compare_digest(provided_key, str(expected_api_key).encode('utf-8')):
    # The submitted key is never logged: it is attacker-controlled (log injection),
    # a near-miss value is likely a real secret, and ERROR is pushed to Gotify.
    failure_limiter.record_failure(client_ip)
    rejection_reporter.record(f"invalid API key for bot '{trader_id}'", client_ip,
                              logger=trader.logger)
    raise HTTPException(status_code=401, detail="Invalid API key.")

  # Authenticated: forget any earlier failures from this address, so a source that was
  # misconfigured and is now correct is never left waiting out a window
  failure_limiter.clear(client_ip)

  ########################################################################
  ## Log the incoming webhook for visibility.
  ## Deliberately after authentication: before it, this line was written for every
  ## request from anyone who knew the path, with three attacker-chosen fields in it —
  ## the bulk of what a flood wrote to a size-rotated log.
  ########################################################################
  logger.info(
    f"📥 Webhook received — trader_id: '{trader_id}', "
    f"action: '{data.get('action', '<not set>')}', "
    f"ticker: '{data.get('ticker', '<not set>')}'"
  )

  ## Ok bot exists and API key is valid, let's extract other fields
  # Extract and validate required fields
  ticker = data.get("ticker")
  action = data.get("action")
  timestamp_raw = data.get("timestamp")
  alert_name = data.get("alert_name")
  if not alert_name and hasattr(request, "query_params"):
    alert_name = request.query_params.get("alert_name")


  ########################################
  # Convert timestamp to datetime (timezone-aware, UTC)
  ########################################
  timestamp_dt = parse_signal_timestamp(timestamp_raw)

  ###############################################
  ##### CHECK IF ALL FIELDS WERE SENT PROPERLY
  ###############################################
  missing = [k for k in ["ticker", "action", "timestamp", "order_size", "order_size_type"] if not data.get(k)]
  if missing:
    trader.logger.error(f"Missing fields: {', '.join(missing)}")
    raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

  ###############################################
  ##### REPLAY PROTECTION
  ###############################################
  # The API key travels inside the body, so a captured request is a reusable trading
  # capability unless the signal is required to be recent and accepted only once.
  if timestamp_dt is None:
    trader.logger.error(
      f"Rejected signal for {trader_id}: timestamp '{timestamp_raw}' could not be read. "
      "Send unix seconds/ms or ISO 8601 — TradingView alerts must send "
      "{{timenow}}."
    )
    raise HTTPException(status_code=400, detail="Invalid timestamp — send unix seconds/ms or ISO 8601.")

  # Both checks below are reachable by replaying one captured request — the key is in
  # the body — so they are floodable and go through the summariser.
  age_seconds = (datetime.now(timezone.utc) - timestamp_dt).total_seconds()
  if abs(age_seconds) > WEBHOOK_MAX_AGE_S:
    when = "old" if age_seconds > 0 else "in the future"
    rejection_reporter.record(
      f"stale timestamp for bot '{trader_id}'", client_ip,
      f"{abs(age_seconds):.0f}s {when}, limit is {WEBHOOK_MAX_AGE_S}s. Cause is a "
      "replayed request, a clock that is out of sync, or a bar timestamp — "
      "TradingView alerts must send " "{{timenow}}, not {{time}}.",
      logger=trader.logger)
    raise HTTPException(status_code=400, detail="Signal timestamp is outside the freshness window.")

  if not await replay_guard.register(signal_fingerprint(trader_id, body)):
    rejection_reporter.record(
      f"duplicate signal for bot '{trader_id}'", client_ip,
      "Identical request already processed — a replay, or the signal source sent the "
      "same alert twice.", logger=trader.logger)
    raise HTTPException(status_code=409, detail="Duplicate signal — this request was already processed.")

  # Format timestamp for logging
  timestamp_str = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
  # The payload carries the bot's API key — redact it before it reaches the log file
  trader.logger.info(
    f"Webhook received payload: {json.dumps({**data, 'api_key': '***'}, indent=2)}"
  )

  ###############################################
  ##### CHECK IF ORDER SIZE is SENT
  ###############################################
  # Parse dry_run parameter (default False for backward compatibility)
  dry_run = data.get("dry_run", False)
  if isinstance(dry_run, str):
    dry_run = dry_run.lower() in ['true', '1', 'yes']
  elif not isinstance(dry_run, bool):
    dry_run = False

  if dry_run:
    trader.logger.warning("🧪 DRY RUN MODE: Order will be simulated, not executed")

  # Parse order_size_type — presence already guaranteed by missing fields check above
  order_size_type = str(data.get("order_size_type")).lower()
  if order_size_type not in ["percentage", "quantity"]:
    trader.logger.error(f"Invalid order_size_type: '{order_size_type}'. Must be 'percentage' or 'quantity'.")
    raise HTTPException(status_code=400, detail=f"Invalid order_size_type: '{order_size_type}'. Must be 'percentage' or 'quantity'.")

  # Parse order_size value — presence already guaranteed by missing fields check above
  order_size_raw = data.get("order_size")
  if isinstance(order_size_raw, (int, float)):
    order_size = float(order_size_raw)
  elif isinstance(order_size_raw, str):
    try:
      order_size = float(order_size_raw.strip())
    except ValueError as exc:
      trader.logger.error(f"Invalid order_size value: '{order_size_raw}'. Must be a number.")
      raise HTTPException(status_code=400, detail=f"Invalid order_size value: '{order_size_raw}'. Must be a number.") from exc
  else:
    trader.logger.error(f"Invalid order_size type: {type(order_size_raw).__name__}. Must be a number.")
    raise HTTPException(status_code=400, detail=f"order_size must be a number, got: {type(order_size_raw).__name__}.")

  # Validate based on order_size_type
  if order_size_type == "percentage":
    if not 0.0 < order_size <= 100.0:
      trader.logger.error(f"order_size out of range for percentage mode: {order_size}. Must be between 0 (exclusive) and 100 (inclusive).")
      raise HTTPException(status_code=400, detail=f"order_size out of range: {order_size}. Must be between 0 and 100 for percentage mode.")
    spend_percentage = order_size / 100.0
    quantity = None
    trader.logger.info(f"Order mode: PERCENTAGE ({order_size}%)")
  else:  # quantity mode
    if order_size <= 0:
      trader.logger.error(f"order_size must be positive in quantity mode: {order_size}")
      raise HTTPException(status_code=400, detail=f"order_size must be positive: {order_size}")
    spend_percentage = None
    quantity = order_size
    trader.logger.info(f"Order mode: QUANTITY ({quantity})")

  ################################################
  #### CHECK IF ACTION SIGNAL IS BUY/SELL/LONG/SHORT
  ################################################
  action_raw = data.get("action", "").lower()
  # Normalize TradingView/standard signals
  if action_raw in ["buy", "long"]:
    action = "buy"
  elif action_raw in ["sell", "short"]:
    action = "sell"
  else:
    trader.logger.error(f"INVALID action: {action_raw}")
    raise HTTPException(status_code=400, detail="Invalid action. Must be one of: 'buy', 'sell', 'long', 'short'.")

  trader.logger.info(f"VALID Action {action} (raw: {action_raw})")

  ######################################################
  ## BRANCH BASED ON TRADER TYPE (CRYPTO vs STOCK)
  ######################################################
  # pylint: disable=too-many-nested-blocks
  ######################################################
  ## SERIALISE EXECUTION FOR THIS BOT
  ## Everything from here reads a balance and then trades on it. Held across the
  ## whole sequence so an overlapping signal or a dashboard conversion cannot size
  ## its order from a balance this request is about to change.
  ######################################################
  async with trader_execution_lock(trader_id):
    if trader.bot_type == "crypto":
      ######################################################
      ## CRYPTO TRADER: CHECK IF TICKER MATCHES PAIR
      ######################################################
      expected_ticker = trader.crypto_stablecoin_pair
      if not resolve_ticker(ticker, expected_ticker, trader.logger):
        trader.logger.error(f"Invalid ticker: {ticker}, expected: {expected_ticker}")
        raise HTTPException(
          status_code=400,
          detail=f"Invalid ticker symbol. Expected: {expected_ticker}, Received: {ticker}"
        )
      # Adopt the configured spelling: everything below splits on '/' and hands the
      # symbol to the exchange.
      ticker = expected_ticker
      trader.logger.info(f"VALID ticker: {ticker}")

      ######################################################
      # CRYPTO: Check balance and execute order
      ######################################################
      if action == "buy":
        try:
          # Fetch current balance to check stablecoin availability
          raw_balance = await trader.fetch_balance()
          free_balances = raw_balance.get('free', {})
          stablecoin_symbol = trader.crypto_stablecoin_pair.split('/')[1]  # Extract stablecoin (e.g., USDT from BTC/USDT)
          available_stablecoin = free_balances.get(stablecoin_symbol, 0.0)

          if available_stablecoin <= 0:
            trader.logger.warning(f"Buy signal received for {ticker} but no {stablecoin_symbol} balance available. Available: {available_stablecoin}")
            return {
              "status": "warning",
              "message": f"Buy signal received but insufficient {stablecoin_symbol} balance",
              "available_balance": available_stablecoin,
              "processed_at": timestamp_str
            }

          trader.logger.info(f"Buy signal validation passed. Available {stablecoin_symbol} balance: {available_stablecoin}")
          # Execute the buy order
          try:
            if order_size_type == "percentage":
              trader.logger.info(f"Executing BUY order for {ticker} with {order_size}% of available {stablecoin_symbol} ({available_stablecoin*spend_percentage:.2f})")
            else:
              trader.logger.info(f"Executing BUY order for {ticker}: {quantity} {ticker.split('/')[0]}")

            order_result = await trader.create_order(
              symbol=ticker,
              side='buy',
              spend_percentage=spend_percentage,
              quantity=quantity,
              order_execution_strategy='market',  # Market order for immediate execution
              dry_run=dry_run
            )

            if order_result:
              trader.logger.info(f"BUY order executed successfully! Order ID: {order_result.get('id')}")

              # Get updated balance to show meaningful success message
              try:
                updated_balance = await trader.fetch_balance()
                free_balances = updated_balance.get('free', {})
                crypto_symbol = ticker.split('/')[0]
                stablecoin_symbol = ticker.split('/')[1]
                crypto_balance = free_balances.get(crypto_symbol, 0.0)
                stablecoin_balance = free_balances.get(stablecoin_symbol, 0.0)

                trader.logger.success(f"🚀 BUY Complete! Portfolio: {crypto_balance:.8f} {crypto_symbol} + {stablecoin_balance:.2f} {stablecoin_symbol}")
              except Exception as balance_e:
                trader.logger.warning(f"🚀 BUY order completed but couldn't fetch updated balance: {order_result.get('id')}\n{balance_e}")

              return {
                "status": "success",
                "message": "BUY order executed successfully",
                "order_id": order_result.get('id'),
                "symbol": ticker,
                "side": "buy",
                "amount": order_result.get('amount'),
                "price": order_result.get('price'),
                "processed_at": timestamp_str
              }

            trader.logger.error("BUY order execution failed - no order result returned")
            return {
              "status": "error",
              "message": "BUY order execution failed",
              "processed_at": timestamp_str
            }
          except Exception as order_e:
            error_msg = str(order_e)
            trader.logger.error(f"Error executing BUY order: {error_msg}")
            return {
              "status": "error",
              "message": f"BUY order execution failed: {error_msg}",
              "processed_at": timestamp_str
            }

        except Exception as exc:
          trader.logger.error(f"Failed to fetch balance for buy validation: {str(exc)}")
          raise HTTPException(status_code=500, detail=f"Failed to validate balance: {str(exc)}") from exc

      elif action == "sell":
        try:
          # Fetch current balance to check crypto availability
          raw_balance = await trader.fetch_balance()
          free_balances = raw_balance.get('free', {})
          crypto_symbol = trader.crypto_stablecoin_pair.split('/')[0]  # Extract crypto (e.g., BTC from BTC/USDT)
          available_crypto = free_balances.get(crypto_symbol, 0.0)

          if available_crypto <= 0:
            trader.logger.warning(f"Sell signal received for {ticker} but no {crypto_symbol} balance available. Available: {available_crypto}")
            return {
              "status": "warning",
              "message": f"Sell signal received but insufficient {crypto_symbol} balance",
              "available_balance": available_crypto,
              "processed_at": timestamp_str
            }

          trader.logger.info(f"Sell signal validation passed. Available {crypto_symbol} balance: {available_crypto}")

          # Execute the sell order
          try:
            if order_size_type == "percentage":
              trader.logger.info(f"Executing SELL order for {ticker} with {spend_percentage*100:.2f}% of available {crypto_symbol}")
            else:
              trader.logger.info(f"Executing SELL order for {ticker}: {quantity} {ticker.split('/')[0]}")

            order_result = await trader.create_order(
              symbol=ticker,
              side='sell',
              spend_percentage=spend_percentage,
              quantity=quantity,
              order_execution_strategy='market',  # Market order for immediate execution
              dry_run=dry_run
            )

            if order_result:
              trader.logger.info(f"SELL order executed successfully! Order ID: {order_result.get('id')}")

              # Get updated balance to show meaningful success message
              try:
                updated_balance = await trader.fetch_balance()
                free_balances = updated_balance.get('free', {})
                crypto_symbol = ticker.split('/')[0]
                stablecoin_symbol = ticker.split('/')[1]
                crypto_balance = free_balances.get(crypto_symbol, 0.0)
                stablecoin_balance = free_balances.get(stablecoin_symbol, 0.0)

                trader.logger.success(f"💰 SELL Complete! Portfolio: {crypto_balance:.8f} {crypto_symbol} + {stablecoin_balance:.2f} {stablecoin_symbol}")
              except Exception as balance_e:
                trader.logger.warning(f"💰 SELL order completed but couldn't fetch updated balance: {order_result.get('id')} - Error: {str(balance_e)}")

              return {
                "status": "success",
                "message": "SELL order executed successfully",
                "order_id": order_result.get('id'),
                "symbol": ticker,
                "side": "sell",
                "amount": order_result.get('amount'),
                "price": order_result.get('price'),
                "processed_at": timestamp_str
              }

            trader.logger.error("SELL order execution failed - no order result returned")
            return {
              "status": "error",
              "message": "SELL order execution failed",
              "processed_at": timestamp_str
            }
          except Exception as order_e:
            error_msg = str(order_e)
            trader.logger.error(f"Error executing SELL order: {error_msg}")
            return {
              "status": "error",
              "message": f"SELL order execution failed: {error_msg}",
              "processed_at": timestamp_str
            }

        except Exception as exc:
          trader.logger.error(f"Failed to fetch balance for sell validation: {str(exc)}")
          raise HTTPException(status_code=500, detail=f"Failed to validate balance: {str(exc)}") from exc

    elif trader.bot_type == "stock":
      ######################################################
      ## STOCK TRADER: CHECK IF TICKER MATCHES SYMBOL
      ######################################################
      expected_ticker = trader.symbol
      if not resolve_ticker(ticker, expected_ticker, trader.logger):
        trader.logger.error(f"Invalid ticker: {ticker}, expected: {expected_ticker}")
        raise HTTPException(
          status_code=400,
          detail=f"Invalid ticker symbol. Expected: {expected_ticker}, Received: {ticker}"
        )
      ticker = expected_ticker
      trader.logger.info(f"VALID ticker: {ticker}")

      ######################################################
      # STOCK: Check if market allows trading (skipped for dry_run)
      ######################################################
      if not dry_run and not trader.can_trade_now():
        market_status = trader.get_market_status()
        time_until_open = trader.get_time_until_market_opens()
        error_msg = f"Market is {market_status}. "
        if market_status in ['pre-market', 'after-hours']:
          error_msg += "Extended hours trading is disabled."
        elif time_until_open:
          error_msg += f"Market opens in {time_until_open}."
        trader.logger.warning(f"Cannot trade now: {error_msg}")
        return {
          "status": "warning",
          "message": error_msg,
          "processed_at": timestamp_str
        }

      ######################################################
      # STOCK: Execute order (balance checks done internally)
      ######################################################
      try:
        if order_size_type == "percentage":
          trader.logger.info(f"Executing {action.upper()} order for {ticker} with {order_size}% position size")
        else:
          # Keep quantity as float for fractional-shares bots, otherwise truncate to int
          quantity_to_use = quantity if trader.fractional_shares else int(quantity)
          trader.logger.info(f"Executing {action.upper()} order for {ticker}: {quantity_to_use} shares")

        order_result = await trader.create_order(
          side=action,
          spend_percentage=spend_percentage,
          quantity=(quantity if trader.fractional_shares else int(quantity)) if quantity is not None else None,
          order_execution_strategy='market',
          params={'dry_run': dry_run}
        )

        if order_result:
          trader.logger.success(
            f"{'🚀' if action == 'buy' else '💰'} {action.upper()} order executed! "
            f"Order ID: {order_result.get('order_id')} - "
            f"{order_result.get('quantity')} shares @ ${order_result.get('price', 0):.2f}"
          )
          return {
            "status": "success",
            "message": f"{action.upper()} order executed successfully",
            "order_id": order_result.get('order_id'),
            "symbol": ticker,
            "side": action,
            "quantity": order_result.get('quantity'),
            "price": order_result.get('price'),
            "processed_at": timestamp_str
          }

        trader.logger.error(f"{action.upper()} order execution failed - no order result returned")
        return {
          "status": "error",
          "message": f"{action.upper()} order execution failed",
          "processed_at": timestamp_str
        }
      except Exception as order_e:
        error_msg = str(order_e)
        trader.logger.error(f"Error executing {action.upper()} order: {error_msg}")
        return {
          "status": "error",
          "message": f"{action.upper()} order execution failed: {error_msg}",
          "processed_at": timestamp_str
        }

    else:
      trader.logger.error(f"Unknown bot_type: {trader.bot_type}")
      raise HTTPException(status_code=500, detail=f"Unknown bot type: {trader.bot_type}")

@app.get("/logs/{trader_id}")
async def get_trader_logs(request: Request, trader_id: str):
  """Get recent log messages for a specific trader"""
  # Check authentication
  if not is_authenticated(request):
    return JSONResponse(status_code=401, content={"error": "Authentication required"})

  if trader_id not in traders:
    return JSONResponse(
      status_code=404,
      content={"error": f"Trader {trader_id} not found"}
    )

  try:
    logs = traders[trader_id].get_recent_logs()
    return {"logs": logs}
  except Exception as exc:
    logger.error(f"Error getting logs for {trader_id}: {str(exc)}")
    return JSONResponse(
      status_code=500,
      content={"error": str(exc)}
    )

@app.post("/convert/{trader_id}")
async def convert_fiat_to_stablecoin(request: Request, trader_id: str):
  """Convert fiat currency to stablecoin for a specific trader"""
  # Check authentication
  if not is_authenticated(request):
    return JSONResponse(status_code=401, content={"error": "Authentication required"})

  # Spends the whole fiat balance, so it must have come from the dashboard rather than
  # from a page on another site that happens to be open in an authenticated browser
  require_dashboard_request(request)

  if trader_id not in traders:
    return JSONResponse(
      status_code=404,
      content={"error": f"Trader {trader_id} not found"}
    )

  trader = traders[trader_id]
  trader.logger.info(f"Convert fiat to stablecoin requested for {trader_id}")

  try:
    # Same lock the webhook takes: this spends 100% of the fiat balance, so a signal
    # landing mid-conversion would otherwise size its order from fiat about to be spent
    async with trader_execution_lock(trader_id):
      # Call the trader's convert function with 100% of available fiat
      stablecoin_acquired = await trader.convert_fiat_to_stablecoin(
        spend_percentage=1.0  # Convert 100% of available fiat
      )

    if stablecoin_acquired > 0:
      trader.logger.success(f"Successfully converted fiat to {stablecoin_acquired} stablecoin")
      return {
        "status": "success",
        "message": "Successfully converted fiat to stablecoin",
        "stablecoin_acquired": stablecoin_acquired
      }

    trader.logger.warning("Conversion completed but no stablecoin acquired")
    return {
      "status": "warning",
      "message": "Conversion completed but no stablecoin acquired"
    }

  except (ValueError, RuntimeError) as exc:
    # These are expected errors with user-friendly messages
    error_msg = str(exc)
    trader.logger.warning(f"Conversion failed: {error_msg}")
    return {
      "status": "warning",
      "message": error_msg
    }
  except Exception as exc:
    original_error_msg = str(exc)
    user_error_msg = _parse_exchange_error(original_error_msg, trader)

    # But return the cleaned user-friendly message to the frontend
    return JSONResponse(
      status_code=500,
      content={"error": user_error_msg}
    )

def _parse_exchange_error(error_msg: str, trader) -> str:
  """Parse exchange-specific error messages from JSON responses."""
  # Check if the error message contains JSON
  if '{' not in error_msg or '}' not in error_msg:
    return error_msg

  try:
    # Extract JSON part from the error message
    json_start = error_msg.find('{')
    json_part = error_msg[json_start:]
    error_data = json.loads(json_part)

    # Parse based on trader/exchange type
    if trader.__class__.__name__ == 'OKXTrader':
      return _parse_okx_error(error_data)

    ########### ADD HERE MORE EXCHANGE SPECIFIC RESPONSE HANDLING
    # elif trader.__class__.__name__ == 'BinanceTrader':
    #   return _parse_binance_error(error_data)
    # elif trader.__class__.__name__ == 'CoinbaseTrader':
    #   return _parse_coinbase_error(error_data)

  except (json.JSONDecodeError, KeyError, IndexError) as parse_error:
    trader.logger.debug(f"JSON parsing failed: {parse_error}")

  return error_msg

def _parse_okx_error(error_data) -> str:
  """Parse OKX-specific error messages."""
  # OKX uses 'sMsg' field for error messages, can be at top level or nested in data array
  if isinstance(error_data, dict):
    # Check top level first
    if 'sMsg' in error_data and error_data['sMsg']:
      return error_data['sMsg']
    # Check in data array
    if 'data' in error_data and isinstance(error_data['data'], list) and error_data['data']:
      if 'sMsg' in error_data['data'][0] and error_data['data'][0]['sMsg']:
        return error_data['data'][0]['sMsg']
  elif isinstance(error_data, list) and error_data and 'sMsg' in error_data[0]:
    return error_data[0]['sMsg']

  return str(error_data)

# This __main__ block is mostly for quick local testing if you 'python src/ui/app.py'
# For robust running, you'll use 'uvicorn src.ui.app:app' from the BOLEHTRADE root.
if __name__ == "__main__":
  import uvicorn
  logger.info("Starting Uvicorn server for UI...")
  # Note: If running this directly, StaticFiles/Jinja2Templates paths
  # would need to be "static" and "templates" (relative to current file).
  # Running with 'uvicorn src.ui.app:app' from BOLEHTRADE root is preferred
  # as it simplifies path management.
  uvicorn.run(app, host="0.0.0.0", port=8080, reload=True, log_config=None, access_log=False)
