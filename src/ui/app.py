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
from datetime import datetime
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
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env
from src.misc.config_loader import get_bot_configs
from src.traders.crypto.okx_trader import OKXTrader
from src.traders.crypto.ir_trader import IRTrader
from src.traders.crypto.cryptocom_trader import CryptocomTrader
from src.traders.stock.ibkr_trader import IBKRTrader


# Application version
TRADLEWARE_VERSION = "v3.0.7b"

# You might need to adjust this import based on where your logger.py is relative to app.py
# If your logger is within src/misc, you might access it like this:
# Import centralized get_env helper
# Import our trader classes

# Trading configuration
EXCHANGE_TRADER_CLASSES = {
  'okx': OKXTrader,
  'ir': IRTrader,
  'cryptocom': CryptocomTrader,
  # 'coinbasepro': CoinbaseProTrader,
  # Add other exchanges here as you create their trader classes
  # 'binance': BinanceTrader,
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

  # Start IBKR health-check background task
  health_task = asyncio.create_task(_ibkr_health_loop())
  logger.info(f"IBKR health-check loop started (interval: {IBKR_HEALTH_CHECK_INTERVAL}s)")

  # Start update-check background task
  update_task = asyncio.create_task(_update_check_loop())
  logger.info(f"Update-check loop started (interval: {UPDATE_CHECK_INTERVAL}s)")

  yield  # Server is running here

  # Shutdown
  health_task.cancel()
  update_task.cancel()
  logger.info("Shutting down traders...")
  for trader in traders.values():
    await trader.close()
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

# Add session middleware for authentication
# Generate a secure session key or use one from environment
SESSION_SECRET_KEY = get_env('SESSION_SECRET_KEY') or secrets.token_urlsafe(32)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

# Get webhook path from environment (default to 'webhook')
WEBHOOK_PATH = get_env('WEBHOOK_PATH', 'webhook').strip('/')  # Strip leading/trailing slashes
IBKR_HEALTH_CHECK_INTERVAL = int(get_env('IBKR_HEALTH_CHECK_INTERVAL_S', '1800'))

# Authentication configuration
DASHBOARD_USERNAME = get_env('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASSWORD = get_env('DASHBOARD_PASSWORD', 'changeme')

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

# Initialize a logger for the FastAPI app
# Ensure CustomLogger is correctly imported from src.misc.logger
logger = CustomLogger(name='Tradleware',
                      gotify_url=get_env('GOTIFY_SERVER_URL'),
                      gotify_token=get_env('GOTIFY_APP_TOKEN'),
                      gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30')))

# Log authentication configuration at startup
logger.info(f"Dashboard credentials loaded - Username: '{DASHBOARD_USERNAME}', Password: '{DASHBOARD_PASSWORD}'")
if TRUSTED_IPS:
  logger.info(f"Trusted IPs configured: {', '.join(TRUSTED_IPS)}")
else:
  logger.warning("No trusted IPs configured. All access requires authentication.")


# Mount static files (for CSS, JS, images). Paths are now relative to /src/ui/
# BUT, FastAPI needs the path relative to the app's *startup directory*
# The 'directory="static"' refers to the '/static' folder.
app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")

# Configure Jinja2 templates. Similarly, path is relative to root.
templates = Jinja2Templates(directory="src/ui/templates")


#################### AUTHENTICATION HELPERS ####################

def get_client_ip(request: Request) -> str:
  """Get the real client IP address, accounting for proxies"""
  # Check X-Forwarded-For header (set by reverse proxies)
  forwarded = request.headers.get("X-Forwarded-For")
  if forwarded:
    # X-Forwarded-For can contain multiple IPs, take the first one
    return forwarded.split(",")[0].strip()

  # Check X-Real-IP header (alternative proxy header)
  real_ip = request.headers.get("X-Real-IP")
  if real_ip:
    return real_ip.strip()

  # Fallback to direct client IP
  return request.client.host if request.client else "unknown"

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
    if client_ip != "127.0.0.1":
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

def is_request_secure(request: Request) -> bool:
  # Check X-Forwarded-Proto header (used by most proxies/tunnels)
  xf_proto = request.headers.get("X-Forwarded-Proto")
  if xf_proto:
    return xf_proto.lower() == "https"
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

  # Check if default credentials are still in use
  using_defaults = (
    DASHBOARD_USERNAME == "admin" and DASHBOARD_PASSWORD == "changeme"
  ) or not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD

  # Check if connection is secure (HTTPS)
  is_secure = is_request_secure(request)

  return templates.TemplateResponse(
    request,
    "login.html",
    {
      "error": error,
      "using_defaults": using_defaults,
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

  # Verify credentials using constant-time comparison to prevent timing attacks
  username_match = secrets.compare_digest(username, DASHBOARD_USERNAME)
  password_match = secrets.compare_digest(password, DASHBOARD_PASSWORD)

  if username_match and password_match:
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
    if client_ip != "127.0.0.1":
      logger.warning(f"Unauthenticated access attempt to dashboard from IP: {client_ip}")
    return RedirectResponse(url="/login", status_code=303)

  if client_ip != "127.0.0.1":
    logger.debug(f"Dashboard accessed from IP: {client_ip}")

  # Get log refresh interval from environment (default to 5000ms = 5 seconds)
  log_refresh_interval = int(get_env('LOG_REFRESH_INTERVAL_MS', '5000'))

  # Check if connection is secure (HTTPS)
  is_secure = is_request_secure(request)

  # Check if accessing from trusted IP
  from_trusted_ip = client_ip in TRUSTED_IPS if TRUSTED_IPS else False

  return templates.TemplateResponse(
    request,
    "index.html",
    {
      "title": "Tradleware Dashboard",
      "traders": traders,  # Add the traders dictionary we defined globally
      "log_refresh_interval": log_refresh_interval,  # Pass the refresh interval to template
      "webhook_path": WEBHOOK_PATH,  # Pass the configured webhook path to template
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
  ## reading JSON body with error handling
  try:
    body = await request.body()
    data = json.loads(body.decode('utf-8'))
  except json.JSONDecodeError as exc:
    # Log the raw body for debugging (limit to 500 chars to avoid log spam)
    body_preview = body.decode('utf-8', errors='replace')[:500] if body else "Empty body"
    error_msg = f"Malformed JSON in webhook request: {str(exc)} | Request body: {body_preview}"
    logger.error(error_msg)
    raise HTTPException(status_code=400, detail=error_msg) from exc
  except Exception as exc:
    error_msg = f"Error reading webhook request body: {str(exc)}"
    logger.error(error_msg)
    raise HTTPException(status_code=400, detail=error_msg) from exc

  ########################################################################
  ## Log the incoming webhook for visibility before any validation
  ########################################################################
  incoming_trader_id = data.get("trader_id", "<not set>")
  incoming_action = data.get("action", "<not set>")
  incoming_ticker = data.get("ticker", "<not set>")
  logger.info(
    f"📥 Webhook received — trader_id: '{incoming_trader_id}', "
    f"action: '{incoming_action}', ticker: '{incoming_ticker}'"
  )

  ########################################################################
  ## Check if the trader_id is set properly and we indeed have such a BOT
  ########################################################################
  trader_id = data.get("trader_id")
  if not trader_id:
    logger.error("trader_id not sent")
    raise HTTPException(status_code=400, detail="Missing field: trader_id")
  if trader_id not in traders:
    logger.error("Trader ID not found in traders")
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
  if not api_key or api_key != expected_api_key:
    trader.logger.error(f"Unauthorized webhook attempt for trader {trader_id} - wrong API KEY: {api_key}.")
    raise HTTPException(status_code=401, detail="Invalid API key.")

  ## Ok bot exists and API key is valid, let's extract other fields
  # Extract and validate required fields
  ticker = data.get("ticker")
  action = data.get("action")
  timestamp_raw = data.get("timestamp")
  alert_name = data.get("alert_name")
  if not alert_name and hasattr(request, "query_params"):
    alert_name = request.query_params.get("alert_name")


  ########################################
  # Convert timestamp to datetime
  ########################################
  timestamp_dt = None
  if timestamp_raw:
    try:
      # Handle both unix timestamp (seconds) and milliseconds
      if isinstance(timestamp_raw, (int, float)):
        # If timestamp is larger than 10 digits, it's likely in milliseconds
        if timestamp_raw > 9999999999:  # Greater than year 2286 in seconds
          timestamp_dt = datetime.fromtimestamp(timestamp_raw / 1000)
        else:
          timestamp_dt = datetime.fromtimestamp(timestamp_raw)
      elif isinstance(timestamp_raw, str):
        # Try to parse as integer first
        try:
          timestamp_num = int(float(timestamp_raw))
          if timestamp_num > 9999999999:
            timestamp_dt = datetime.fromtimestamp(timestamp_num / 1000)
          else:
            timestamp_dt = datetime.fromtimestamp(timestamp_num)
        except ValueError:
          # If not a number, try to parse as ISO format
          timestamp_dt = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00'))
    except (ValueError, OSError) as exc:
      trader.logger.warning(f"Invalid timestamp format: {timestamp_raw}, error: {exc}")
      timestamp_dt = datetime.now()  # Fallback to current time
  else:
    timestamp_dt = datetime.now()  # Fallback if no timestamp provided

  ###############################################
  ##### CHECK IF ALL FIELDS WERE SENT PROPERLY
  ###############################################
  missing = [k for k in ["ticker", "action", "timestamp", "order_size", "order_size_type"] if not data.get(k)]
  if missing:
    trader.logger.error(f"Missing fields: {', '.join(missing)}")
    raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

  # Format timestamp for logging
  timestamp_str = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S")
  trader.logger.info(f"Webhook received payload: {json.dumps(data, indent=2)}")

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
  #### CHECK IF ACTION SIGNAL IS BUY OR SELL
  ################################################
  action = data.get("action", "").lower()
  if action not in ["buy", "sell"]:
    trader.logger.error(f"INVALID action: {action}")
    raise HTTPException(status_code=400, detail="Invalid action. Must be 'buy' or 'sell'.")

  trader.logger.info(f"VALID Action {action} ")

  ######################################################
  ## BRANCH BASED ON TRADER TYPE (CRYPTO vs STOCK)
  ######################################################
  # pylint: disable=too-many-nested-blocks
  if trader.bot_type == "crypto":
    ######################################################
    ## CRYPTO TRADER: CHECK IF TICKER MATCHES PAIR
    ######################################################
    expected_ticker = trader.crypto_stablecoin_pair
    if ticker != expected_ticker:
      trader.logger.error(f"Invalid ticker: {ticker}, expected: {expected_ticker}")
      raise HTTPException(
        status_code=400,
        detail=f"Invalid ticker symbol. Expected: {expected_ticker}, Received: {ticker}"
      )
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
    if ticker != expected_ticker:
      trader.logger.error(f"Invalid ticker: {ticker}, expected: {expected_ticker}")
      raise HTTPException(
        status_code=400,
        detail=f"Invalid ticker symbol. Expected: {expected_ticker}, Received: {ticker}"
      )
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

  if trader_id not in traders:
    return JSONResponse(
      status_code=404,
      content={"error": f"Trader {trader_id} not found"}
    )

  trader = traders[trader_id]
  trader.logger.info(f"Convert fiat to stablecoin requested for {trader_id}")

  try:
    # Call the trader's convert function with 100% of available fiat
    stablecoin_acquired = await trader.convert_fiat_to_stablecoin(
      spend_percentage=1.0,  # Convert 100% of available fiat
      order_execution_strategy='market'
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
