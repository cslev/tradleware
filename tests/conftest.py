"""
Shared test configuration and fixtures.

Importing ``src.ui.app`` has side effects that make it hostile to testing: it reads the
developer's real ``.env`` with ``override=True``, and it looks up the server's public IP
over the network at module scope. Both are neutralised here, before the import, so the
suite is deterministic and offline on any machine.

Everything the app reads at import time is pinned below. Anything a test changes is
restored afterwards by the ``isolate_app_state`` fixture, so tests cannot leak state
into each other despite the app being a single shared module.
"""

# Standard library imports
import contextlib
import io
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Third-party imports
import httpx
import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
# The app mounts StaticFiles(directory="src/ui/static"), resolved against the process
# working directory, so the suite has to run from the repository root
os.chdir(REPO_ROOT)

# --- 1. Stop the app from loading the developer's real .env -------------------------
# app.py calls load_dotenv(..., override=True), which would overwrite everything pinned
# below with whatever happens to be on this machine.
import dotenv  # noqa: E402  pylint: disable=wrong-import-position

dotenv.load_dotenv = lambda *args, **kwargs: False

# --- 2. Stop the import-time public-IP lookup from touching the network -------------
# Only requests.get is blocked: _fetch_public_ip() and the update check use it, while
# Gotify delivery uses requests.post, which the logging tests exercise against a local
# server.
import requests  # noqa: E402  pylint: disable=wrong-import-position


def _blocked_get(*args, **kwargs):
  raise requests.exceptions.ConnectionError("outbound HTTP is disabled during tests")


requests.get = _blocked_get

# --- 3. Pin every setting the app reads at import time ------------------------------
WEBHOOK_PATH = "test-webhook"
DASHBOARD_USERNAME = "testadmin"
DASHBOARD_PASSWORD = "test-password-123"
REPLAY_DB = REPO_ROOT / "src" / "logs" / "test_webhook_replay.json"

os.environ.update({
  "WEBHOOK_PATH": WEBHOOK_PATH,
  "WEBHOOK_REPLAY_DB": str(REPLAY_DB),
  "WEBHOOK_REQUIRE_HTTPS": "true",
  "WEBHOOK_MAX_AGE_S": "300",
  "TRADER_LOCK_TIMEOUT_S": "60",
  "DASHBOARD_USERNAME": DASHBOARD_USERNAME,
  "DASHBOARD_PASSWORD": DASHBOARD_PASSWORD,
  "SESSION_SECRET_KEY": "test-session-key-not-a-real-secret",
  "SESSION_HTTPS_ONLY": "true",
  "SESSION_MAX_AGE_S": "43200",
  "TRUSTED_IPS": "",
  "TRUSTED_PROXIES": "",
  "GOTIFY_SERVER_URL": "",
  "GOTIFY_APP_TOKEN": "",
  "GOTIFY_LOG_LEVEL": "30",
  "LOG_LEVEL": "20",
  "IBKR_HEALTH_CHECK_INTERVAL_S": "1800",
  "UPDATE_CHECK_INTERVAL_S": "21600",
  "LOG_REFRESH_INTERVAL_MS": "5000",
})

# --- 4. Import the app, capturing what it prints at startup -------------------------
# test_secret_exposure asserts that no credential appears in this output.
_startup_buffer = io.StringIO()
with contextlib.redirect_stdout(_startup_buffer):
  import src.ui.app as tradleware  # noqa: E402  pylint: disable=wrong-import-position

STARTUP_OUTPUT = _startup_buffer.getvalue()

# The console handlers captured the redirected buffer as their stream when they were
# built. Point them back at the real stdout so pytest can show log output on failure.
for _logger_name in list(logging.root.manager.loggerDict):
  for _handler in logging.getLogger(_logger_name).handlers:
    if isinstance(_handler, logging.StreamHandler) and not isinstance(
        _handler, logging.FileHandler):
      _handler.setStream(sys.__stdout__)

from src.misc import logger as tradleware_logger  # noqa: E402  pylint: disable=wrong-import-position
from src.misc.failure_limiter import FailureLimiter  # noqa: E402  pylint: disable=wrong-import-position
from src.misc.rejection_reporter import RejectionReporter  # noqa: E402  pylint: disable=wrong-import-position
from src.misc.replay_guard import ReplayGuard  # noqa: E402  pylint: disable=wrong-import-position

# Module attributes a test may reasonably change, restored after every test
_MUTABLE_SETTINGS = (
  "TRUSTED_IPS", "TRUSTED_PROXIES", "WEBHOOK_REQUIRE_HTTPS", "WEBHOOK_MAX_AGE_S",
  "WEBHOOK_PATH", "USING_DEFAULT_WEBHOOK_PATH", "SESSION_HTTPS_ONLY",
  "SESSION_MAX_AGE_S", "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD",
  "USING_DEFAULT_CREDENTIALS", "TRADER_LOCK_TIMEOUT_S", "replay_guard",
  "rejection_reporter", "failure_limiter",
)


@pytest.fixture(autouse=True)
def isolate_app_state(tmp_path):
  """
  Give every test the module in a known state and undo whatever it changes.

  The app keeps its configuration in module globals and its traders in a module dict,
  so without this a test that flips WEBHOOK_REQUIRE_HTTPS or registers a bot would
  quietly change the meaning of every test that runs after it. Each test also gets its
  own replay cache, so accepted signals do not carry over.
  """
  saved = {name: getattr(tradleware, name) for name in _MUTABLE_SETTINGS}
  saved_traders = dict(tradleware.traders)
  saved_gotify = (tradleware.logger.gotify_url, tradleware.logger.gotify_token)

  tradleware.traders.clear()
  tradleware.get_trader_lock.__globals__["_TRADER_LOCKS"].clear()
  tradleware.logger.gotify_url = None
  tradleware.logger.gotify_token = None
  # Reuse the TTL the application itself configured rather than recomputing it here.
  # Hardcoding the formula would make any test that asserts it check this fixture's
  # arithmetic instead of the app's, and a regression in app.py would go unnoticed.
  tradleware.replay_guard = ReplayGuard(
    tmp_path / "replay.json", saved["replay_guard"].ttl_seconds, tradleware.logger
  )
  # The reporter starts suppressing after a handful of distinct rejections, so a shared
  # one would silence log assertions in whichever tests happened to run later.
  tradleware.rejection_reporter = RejectionReporter(
    tradleware.logger, saved["rejection_reporter"].summary_interval_s
  )
  # Likewise: failures accumulate per source address, and most tests share a default
  # peer, so a shared limiter would start returning 429 partway through the suite.
  # Sized from the app's own configuration, never from a literal repeated here.
  tradleware.failure_limiter = FailureLimiter(
    saved["failure_limiter"].max_failures, saved["failure_limiter"].window_s
  )

  yield

  for name, value in saved.items():
    setattr(tradleware, name, value)
  tradleware.traders.clear()
  tradleware.traders.update(saved_traders)
  tradleware.get_trader_lock.__globals__["_TRADER_LOCKS"].clear()
  tradleware.logger.gotify_url, tradleware.logger.gotify_token = saved_gotify


@pytest.fixture
def app():
  """The Tradleware app module, for tests that poke at its functions or settings."""
  return tradleware


@pytest.fixture
def webhook_url():
  """Path of the webhook route. Baked in at import, so it cannot change per test."""
  return f"/{WEBHOOK_PATH}"


@pytest_asyncio.fixture
async def client_factory():
  """
  Build HTTP clients bound to a chosen peer address and scheme.

  The peer address is what the trusted-IP and trusted-proxy logic reads, and the scheme
  is what the HTTPS enforcement reads, so most tests differ only in those two values.
  Defaults to HTTPS because webhooks and logins now require it.
  """
  created = []

  def make(peer="203.0.113.9", scheme="https", follow_redirects=False):
    client = httpx.AsyncClient(
      transport=httpx.ASGITransport(app=tradleware.app, client=(peer, 51000)),
      base_url=f"{scheme}://testserver",
      follow_redirects=follow_redirects,
    )
    created.append(client)
    return client

  yield make

  for client in created:
    await client.aclose()


@pytest.fixture
def reconfigure_session():
  """
  Rebuild the session middleware with different cookie settings.

  SessionMiddleware is constructed once at import, so flipping
  ``app.SESSION_HTTPS_ONLY`` only moves the login handler's guard — the cookie flags
  themselves come from the middleware instance and need the stack rebuilt.
  """
  saved = {}

  def _session_middleware():
    return [mw for mw in tradleware.app.user_middleware if "Session" in str(mw.cls)]

  def apply(**settings):
    for middleware in _session_middleware():
      saved.update({key: middleware.kwargs.get(key) for key in settings
                    if key not in saved})
      middleware.kwargs.update(settings)
    if "https_only" in settings:
      tradleware.SESSION_HTTPS_ONLY = settings["https_only"]
    tradleware.app.middleware_stack = tradleware.app.build_middleware_stack()

  yield apply

  if saved:
    for middleware in _session_middleware():
      middleware.kwargs.update(saved)
    tradleware.app.middleware_stack = tradleware.app.build_middleware_stack()


class FakeCryptoTrader:
  """
  Stand-in for a CCXT-backed trader.

  Records orders, tracks a settling balance, and reports the greatest number of
  overlapping exchange calls it saw — which is how the concurrency tests detect two
  requests sizing orders from the same pre-trade balance.
  """

  bot_type = "crypto"
  exchange_id = "okx"
  hostname = "okx.com"
  subaccount_name = None
  stablecoin_fiat_pair = "USDT/SGD"
  crypto_stablecoin_pair = "BTC/USDT"
  trading_pair_valid = True

  def __init__(self, api_key="EXCHANGEKEY0123456789abcdefXYZ",
               tradleware_api_key="tw_live_9f2b7c4e", balance=1000.0, latency=0.0):
    self.account_identifier = "fakebot"
    self.api_key = api_key
    self.tradleware_api_key = tradleware_api_key
    self.balance = balance
    self.latency = latency
    self.orders = []
    self.events = []
    self.converted = []
    self._in_flight = 0
    self.max_in_flight = 0
    self.logger = logging.getLogger("FakeCryptoTrader")
    self.logger.success = lambda *a, **k: None  # the app calls the custom SUCCESS level

  async def _work(self):
    """Simulate an exchange round trip, tracking overlap."""
    self._in_flight += 1
    self.max_in_flight = max(self.max_in_flight, self._in_flight)
    if self.latency:
      import asyncio
      await asyncio.sleep(self.latency)
    self._in_flight -= 1

  async def fetch_balance(self):
    await self._work()
    self.events.append(f"read {self.balance:.0f}")
    return {"free": {"USDT": self.balance, "BTC": 0.5}}

  async def create_order(self, **kwargs):
    spend = self.balance * (kwargs.get("spend_percentage") or 0)
    await self._work()
    self.orders.append(kwargs)
    self.events.append(f"spend {spend:.0f}")
    self.balance -= spend
    return {"id": f"order-{len(self.orders)}", "status": "closed", "amount": 1,
            "price": 1, "cost": spend, "symbol": self.crypto_stablecoin_pair,
            "side": kwargs.get("side")}

  async def convert_fiat_to_stablecoin(self, spend_percentage=1.0, **_kwargs):
    await self._work()
    taken = self.balance * spend_percentage
    self.converted.append(taken)
    self.events.append(f"convert {taken:.0f}")
    self.balance -= taken
    return taken


class FakeStockTrader:
  """Stand-in for the IBKR trader, enough for template rendering and auth paths."""

  bot_type = "stock"
  broker_id = "ibkr"
  symbol = "AAPL"
  account_id = "U1234567"
  account_currency = "USD"   # BaseStockTrader sets this; the stub must mirror it
  extended_hours = False
  fractional_shares = True
  gateway_host = "ib_gateway"
  gateway_port = 8888
  is_connected = True

  def __init__(self, tradleware_api_key="tw_live_stock_key"):
    self.tradleware_api_key = tradleware_api_key
    self.orders = []
    self.logger = logging.getLogger("FakeStockTrader")
    self.logger.success = lambda *a, **k: None

  def get_market_status(self):
    return "closed"

  def can_trade_now(self):
    return True

  def get_time_until_market_opens(self):
    return "0m"

  async def create_order(self, **kwargs):
    """Record the sizing arguments the handler chose, the way the crypto stub does."""
    self.orders.append(kwargs)
    return {
      "order_id": f"stock-{len(self.orders)}",
      "quantity": kwargs.get("quantity") or 0,
      "price": 100.0,
      "status": "simulated",
      "side": kwargs.get("side"),
    }


@pytest.fixture
def crypto_trader():
  """Register a single crypto bot as 'fakebot' and hand it back."""
  trader = FakeCryptoTrader()
  tradleware.traders["fakebot"] = trader
  return trader


@pytest.fixture
def stock_trader():
  """Register a single stock bot as 'fakestock' and hand it back."""
  trader = FakeStockTrader()
  tradleware.traders["fakestock"] = trader
  return trader


# What the dashboard's own JavaScript sends on state-changing requests. A cross-site
# page cannot set a custom header without a CORS preflight, which is what actually
# blocks the forgery; tests simulating the dashboard have to include it.
DASHBOARD_HEADERS = {"X-Tradleware-Request": "1"}

UNSET = object()   # lets a test pass timestamp=None and mean it


def signal_payload(api_key="tw_live_9f2b7c4e", trader_id="fakebot", timestamp=UNSET,
                   **overrides):
  """
  Build a valid webhook payload.

  `alert_name` carries a counter so two calls never produce identical bytes, which
  would otherwise trip replay protection in tests that are not about replay.
  """
  signal_payload.counter += 1
  payload = {
    "api_key": api_key,
    "trader_id": trader_id,
    "ticker": "BTC/USDT",
    "action": "buy",
    "timestamp": int(time.time()) if timestamp is UNSET else timestamp,
    "alert_name": f"test-signal-{signal_payload.counter}",
    "order_size": 100,
    "order_size_type": "percentage",
    "dry_run": True,
  }
  payload.update(overrides)
  return payload


signal_payload.counter = 0


class _GotifyHandler(BaseHTTPRequestHandler):
  """Records posted notifications, optionally slowly."""

  def do_POST(self):  # noqa: N802  (name fixed by BaseHTTPRequestHandler)
    length = int(self.headers.get("content-length", 0))
    body = json.loads(self.rfile.read(length) or b"{}")
    delay = self.server.response_delay
    if delay:
      time.sleep(delay)
    self.server.received.append(body)
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"{}")

  def log_message(self, *args):
    pass  # keep the test output clean


@pytest.fixture
def gotify_server():
  """
  A local stand-in for a Gotify server.

  `.received` collects payloads; `.response_delay` makes it slow, which is how the
  tests prove notification delivery never blocks the event loop.
  """
  server = HTTPServer(("127.0.0.1", 0), _GotifyHandler)
  server.received = []
  server.response_delay = 0.0
  server.url = f"http://127.0.0.1:{server.server_port}"
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  yield server
  server.shutdown()
  server.server_close()


@pytest.fixture
def use_gotify(gotify_server):
  """Point the application logger at the local Gotify stand-in."""
  tradleware.logger.gotify_url = gotify_server.url
  tradleware.logger.gotify_token = "test-token"
  yield gotify_server
  tradleware_logger.flush_gotify_queue(timeout=5)
