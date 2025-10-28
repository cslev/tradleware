"""
Tradleware Dashboard - FastAPI Web Interface

This module provides a web-based dashboard for managing cryptocurrency trading bots.
It handles authentication, trader initialization, webhook processing for TradingView
signals, and provides endpoints for balance monitoring and order management.
"""

# Standard library imports
from contextlib import asynccontextmanager
from datetime import datetime
import json
from pathlib import Path
import secrets

# Third-party imports
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# First-party imports
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env
from src.traders.okx_trader import OKXTrader
from src.traders.ir_trader import IRTrader


# Application version
TRADLEWARE_VERSION = "v2.0"

# You might need to adjust this import based on where your logger.py is relative to app.py
# If your logger is within src/misc, you might access it like this:
# Import centralized get_env helper
# Import our trader classes

# Trading configuration
EXCHANGE_TRADER_CLASSES = {
  'okx': OKXTrader,
  'ir': IRTrader,
  # 'coinbasepro': CoinbaseProTrader,
  # Add other exchanges here as you create their trader classes
  # 'binance': BinanceTrader,
}

# Store active traders
traders = {}

#################### LIFESPAN FUNCTION ####################
# This function runs on app startup and shutdown to initialize and clean up traders.
# It uses FastAPI's lifespan context manager to handle startup and shutdown events.
@asynccontextmanager
async def lifespan(app: FastAPI):  # pylint: disable=redefined-outer-name
  """Lifespan context manager for startup/shutdown events"""
  # Startup
  logger.info("Initializing trading configurations...")
  logger.info(f"Webhook endpoint configured at: /{WEBHOOK_PATH}")

  active_configs_str = get_env('ACTIVE_TRADING_CONFIGS')
  if active_configs_str:
    config_strings = [cfg.strip() for cfg in active_configs_str.split(',') if cfg.strip()]

    logger.info("\n--- Initializing Traders ---")
    logger.debug("Active trading configurations: " + ", ".join(config_strings))
    for config_str in config_strings:
      parts = config_str.split('_')
      if len(parts) < 2:
        logger.warning(f"Invalid config string format: {config_str}. Expected format is'IDENTIFIER_EXCHANGE', e.g., MYBOT_OKX...Skipping.")
        continue

      account_identifier = "_".join(parts[:-1])
      exchange_id_str = parts[-1].lower()

      trader_class = EXCHANGE_TRADER_CLASSES.get(exchange_id_str)
      if trader_class:
        try:
          trader = trader_class(account_identifier=account_identifier)
          traders[config_str] = trader
          logger.info(f"Initialized trader: {config_str}")
          try:
            await trader.post_init()
          except Exception as exc:
            logger.error(f"Could not check pair support for {config_str}: {exc}")
        except Exception as exc:
          logger.error(f"Failed to initialize {config_str}: {str(exc)}")
  else:
    logger.error("Error: ACTIVE_TRADING_CONFIGS environment variable is not set. "
          "Please define which accounts to load (e.g., 'MYBOT_OKX,MYBOT_COINBASEPRO').")

  yield  # Server is running here

  # Shutdown
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

# Authentication configuration
DASHBOARD_USERNAME = get_env('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASSWORD = get_env('DASHBOARD_PASSWORD', 'changeme')
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
  client_ip = request.client.host if request.client else "unknown"
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
    "login.html",
    {
      "request": request,
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
  client_ip = request.client.host if request.client else "unknown"

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
    "index.html",
    {
      "request": request,
      "title": "Tradleware Dashboard",
      "traders": traders,  # Add the traders dictionary we defined globally
      "log_refresh_interval": log_refresh_interval,  # Pass the refresh interval to template
      "webhook_path": WEBHOOK_PATH,  # Pass the configured webhook path to template
      "is_secure": is_secure,  # Pass connection security status
      "is_trusted_ip": from_trusted_ip,  # Pass trusted IP status
      "client_ip": client_ip,  # Pass client IP for display
      "version": TRADLEWARE_VERSION  # Pass application version
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
    logger.error(f"Error for {trader_id}: {error_msg}")
    return JSONResponse(
      status_code=500,
      content={"error": error_msg}
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
  missing = [k for k in ["ticker", "action", "timestamp"] if not data.get(k)]
  if missing:
    trader.logger.error(f"Missing fields: {', '.join(missing)}")
    raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

  # Format timestamp for logging
  timestamp_str = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S")
  trader.logger.info(f"Webhook received payload: {json.dumps(data, indent=2)}")

  ###############################################
  ##### CHECK IF ORDER SIZE is SENT
  ###############################################
  # Parse spend_percentage from webhook, default to 1.0 if not provided
  order_size_raw = data.get("order_size")
  if order_size_raw is None:
    order_size = 100.0
  elif isinstance(order_size_raw, (int, float)):
    order_size = float(order_size_raw)
  elif isinstance(order_size_raw, str):
    try:
      order_size = float(order_size_raw.strip())
    except ValueError:
      trader.logger.warning(f"Invalid order_size string: {order_size_raw}, defaulting to 100")
      order_size = 100.0
  else:
    trader.logger.warning(f"Unrecognized order_size type: {type(order_size_raw)}, defaulting to 100")
    order_size = 100.0
  if not 0.0 < order_size <= 100.0:
    trader.logger.warning(f"order_size out of range: {order_size}, defaulting to 100")
    order_size = 100.0
  spend_percentage = order_size / 100.0

  ################################################
  #### CHECK IF ACTION SIGNAL IS BUY OR SELL
  ################################################
  action = data.get("action", "").lower()
  if action not in ["buy", "sell"]:
    trader.logger.error(f"INVALID action: {action}")
    raise HTTPException(status_code=400, detail="Invalid action. Must be 'buy' or 'sell'.")

  trader.logger.info(f"VALID Action {action} ")

  ######################################################
  ## CHECK IF TICKER MATCHES THE BOT'S CONFIGURED PAIR
  ######################################################
  # Validate ticker symbol matches the bot's configured crypto/stablecoin pair
  expected_ticker = trader.crypto_stablecoin_pair
  if ticker != expected_ticker:
    trader.logger.error(f"Invalid ticker: {ticker}")
    raise HTTPException(
      status_code=400,
      detail=f"Invalid ticker symbol. Expected: {expected_ticker}, Received: {ticker}"
    )

  trader.logger.info(f"VALID ticker: {ticker}")

  ######################################################
  # Check if we have sufficient balance for the requested action
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
        trader.logger.info(f"Executing BUY order for {ticker} with {order_size}% of available {stablecoin_symbol} ({available_stablecoin*spend_percentage:.2f})")
        order_result = await trader.create_order(
          symbol=ticker,
          side='buy',
          spend_percentage=spend_percentage,  # Use 100% of available stablecoin
          order_execution_strategy='market'  # Market order for immediate execution
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
        trader.logger.info(f"Executing SELL order for {ticker} with {spend_percentage*100:.2f}% of available {crypto_symbol}")
        order_result = await trader.create_order(
          symbol=ticker,
          side='sell',
          spend_percentage=spend_percentage,  # Use 100% of available crypto
          order_execution_strategy='market'  # Market order for immediate execution
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
  else:
    trader.logger.warning(f"Invalid action signal received: {action}! Modify your strategy script to send only 'buy' or 'sell' actions.")
    return {
      "status": "warning",
      "message": f"Invalid action signal: {action}",
      "processed_at": timestamp_str
    }
  # return {"status": "success", "message": "Webhook processed", "processed_at": timestamp_str}

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
