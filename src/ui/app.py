from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse  # Import JSONResponse for JSON responses
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv #for env variables
import os


# You might need to adjust this import based on where your logger.py is relative to app.py
# If your logger is within src/misc, you might access it like this:
from src.misc.logger import CustomLogger
# Import our trader classes
from src.traders.okx_trader import OKXTrader

# Trading configuration
EXCHANGE_TRADER_CLASSES = {
  'okx': OKXTrader,
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
async def lifespan(app: FastAPI):
  """Lifespan context manager for startup/shutdown events"""
  # Startup
  logger.info("Initializing trading configurations...")
  
  active_configs_str = os.getenv('ACTIVE_TRADING_CONFIGS')
  if active_configs_str:
    config_strings = [cfg.strip() for cfg in active_configs_str.split(',') if cfg.strip()]
    
    logger.info("\n--- Initializing Traders ---")
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
          traders[config_str] = trader_class(account_identifier=account_identifier)
          logger.success(f"Initialized trader: {config_str}")
        except Exception as e:
          logger.error(f"Failed to initialize {config_str}: {str(e)}")
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
  title="Tradleware Web UI",
  description="Web interface for the Tradleware trading bot middleware.",
  lifespan=lifespan
)

# Initialize a logger for the FastAPI app
# Ensure CustomLogger is correctly imported from src.misc.logger
logger = CustomLogger('Tradleware')
load_dotenv() # Load environment variables from .env file

# Mount static files (for CSS, JS, images). Paths are now relative to /src/ui/
# BUT, FastAPI needs the path relative to the app's *startup directory*
# The 'directory="static"' refers to the '/static' folder.

app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")

# Configure Jinja2 templates. Similarly, path is relative to root.
templates = Jinja2Templates(directory="src/ui/templates")



@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
  """Renders the main index.html page with trader cards."""
  return templates.TemplateResponse(
    "index.html",
    {
      "request": request,
      "title": "Tradleware Dashboard",
      "traders": traders  # Add the traders dictionary we defined globally
    }
  )

# Add the balance endpoint - this will be called from the UI to fetch trader balances
# by clicking on the refresh button in the UI.
@app.get("/balance/{trader_id}")
async def get_balance(trader_id: str):
    """Fetch balance for a specific trader"""
    if trader_id not in traders:
        return JSONResponse(
            status_code=404,
            content={"error": f"Trader {trader_id} not found"}
        )
    logger.debug(f"asking for balance of {trader_id}")
    try:
        raw_balance = await traders[trader_id].fetch_balance()
        fiat = traders[trader_id].fiat_stablecoin_pair.split('/')[1]  # Extract fiat currency from the pair
        stablecoin = traders[trader_id].fiat_stablecoin_pair.split('/')[0]  # Extract stablecoin from the pair  
        crypto = traders[trader_id].crypto_stablecoin_pair.split('/')[0]  # Extract crypto symbol from the trader
        
        # logger.debug(f"Raw balance for {trader_id}: {raw_balance}")
        
        # Parse balances from the 'total' section
        total_balances = raw_balance.get('total', {})

        free_balances = raw_balance.get('free', {})
        
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
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error for {trader_id}: {error_msg}")
        return JSONResponse(
            status_code=500,
            content={"error": error_msg}
        )

@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Handles incoming webhooks with per-bot API key authentication.
    Expects JSON with: api_key, trader_id, ticker, action, timestamp, alert_name.
    """
    ## reading JSON body
    data = await request.json()
    
    ## Check if the trader_id is set properly and we indeed have such a BOT
    trader_id = data.get("trader_id")
    if not trader_id:
      logger.error("trader_id not sent")
      raise HTTPException(status_code=400, detail="Missing field: trader_id")
    if trader_id not in traders:
      logger.error("Trader ID not found in traders")
      raise HTTPException(status_code=404, detail=f"Trader ID '{trader_id}' not found")

    
    ## we have such a bot, let's check the API key
    api_key = data.get("api_key")
    trader = traders[trader_id]
    expected_api_key = trader.tradleware_api_key
    if not expected_api_key:
        logger.error(f"No Tradleware API key configured for trader {trader_id}")
        raise HTTPException(status_code=500, detail="Trader is not configured with a Tradleware API key")
    if not api_key or api_key != expected_api_key:
        logger.error(f"Unauthorized webhook attempt for trader {trader_id} - wrong API KEY: {api_key}.")
        raise HTTPException(status_code=401, detail="Invalid API key.")

    ## Ok bot exists and API key is valid, let's extract other fields
    # Extract and validate required fields
    ticker = data.get("ticker")
    action = data.get("action")
    timestamp = data.get("timestamp")
    alert_name = data.get("alert_name")
    if not alert_name and hasattr(request, "query_params"):
      alert_name = request.query_params.get("alert_name")
    #check if all required fields are present
    missing = [k for k in ["ticker", "action", "timestamp"] if not data.get(k)]
    if missing:
      raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    logger.info(f"Webhook received: trader_id={trader_id}, ticker={ticker}, action={action}, timestamp={timestamp}, alert_name={alert_name}")

    ## check action if buy or sell
    action = data.get("action", "").lower()
    if action not in ["buy", "sell"]:
      logger.error(f"Invalid action received: {action}")
      raise HTTPException(status_code=400, detail="Invalid action. Must be 'buy' or 'sell'.")
    
    return {"status": "success", "message": "Webhook processed"}

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
