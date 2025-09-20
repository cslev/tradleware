from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse  # Import JSONResponse for JSON responses
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv #for env variables
import os
from datetime import datetime


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
          logger.info(f"Initialized trader: {config_str}")
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

load_dotenv() # Load environment variables from .env file

# Initialize a logger for the FastAPI app
# Ensure CustomLogger is correctly imported from src.misc.logger
logger = CustomLogger(name='Tradleware',
                      gotify_url=os.getenv('GOTIFY_SERVER_URL'),
                      gotify_token=os.getenv('GOTIFY_APP_TOKEN'),
                      gotify_log_level=int(os.getenv('GOTIFY_LOG_LEVEL', 30)))


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
    traders[trader_id].logger.debug(f"asking for balance of {trader_id}")
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
        except (ValueError, OSError) as e:
            trader.logger.warning(f"Invalid timestamp format: {timestamp_raw}, error: {e}")
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
    trader.logger.info(f"Webhook received: \ntrader_id={trader_id}, \nticker={ticker}, \naction={action}, \ntimestamp={timestamp_str}, \nalert_name={alert_name}")

    ################################################
    #### CHECK IF ACTION SIGNAL IS BUY OR SELL 
    ################################################
    action = data.get("action", "").lower()
    if action not in ["buy", "sell"]:
      trader.logger.error(f"INVALID action: {action}")
      raise HTTPException(status_code=400, detail="Invalid action. Must be 'buy' or 'sell'.")
    else:
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
    else:
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
        else:
          trader.logger.info(f"Buy signal validation passed. Available {stablecoin_symbol} balance: {available_stablecoin}")
              
      except Exception as e:
        trader.logger.error(f"Failed to fetch balance for buy validation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to validate balance: {str(e)}")
    
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
        else:
          trader.logger.info(f"Sell signal validation passed. Available {crypto_symbol} balance: {available_crypto}")
              
      except Exception as e:
        trader.logger.error(f"Failed to fetch balance for sell validation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to validate balance: {str(e)}")
    
    trader.logger.success("Webhook processed successfully")
    return {"status": "success", "message": "Webhook processed", "processed_at": timestamp_str}

@app.get("/logs/{trader_id}")
async def get_trader_logs(trader_id: str):
    """Get recent log messages for a specific trader"""
    if trader_id not in traders:
        return JSONResponse(
            status_code=404,
            content={"error": f"Trader {trader_id} not found"}
        )
    
    try:
        logs = traders[trader_id].get_recent_logs()
        return {"logs": logs}
    except Exception as e:
        logger.error(f"Error getting logs for {trader_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.post("/convert/{trader_id}")
async def convert_fiat_to_stablecoin(trader_id: str):
  """Convert fiat currency to stablecoin for a specific trader"""
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
        "message": f"Successfully converted fiat to stablecoin",
        "stablecoin_acquired": stablecoin_acquired
      }
    else:
      trader.logger.warning("Conversion completed but no stablecoin acquired")
      return {
        "status": "warning",
        "message": "Conversion completed but no stablecoin acquired"
      }
      
  except (ValueError, RuntimeError) as e:
    # These are expected errors with user-friendly messages
    error_msg = str(e)
    trader.logger.warning(f"Conversion failed: {error_msg}")
    return {
      "status": "warning",
      "message": error_msg
    }
  except Exception as e:
    original_error_msg = str(e)  # Keep the full original error for logging
    user_error_msg = original_error_msg  # This will be sent to the user (may be cleaned up)
    
    # Parse exchange-specific error messages based on trader type
    try:
      import json
      # Check if the error message contains JSON
      if '{' in original_error_msg and '}' in original_error_msg:
        # Extract JSON part from the error message
        json_start = original_error_msg.find('{')
        json_part = original_error_msg[json_start:]
        
        error_data = json.loads(json_part)
        
        # Parse based on trader/exchange type
        if trader.__class__.__name__ == 'OKXTrader':
          # OKX uses 'sMsg' field for error messages, can be at top level or nested in data array
          if isinstance(error_data, dict):
            # Check top level first
            if 'sMsg' in error_data and error_data['sMsg']:
              user_error_msg = error_data['sMsg']
            # Check in data array
            elif 'data' in error_data and isinstance(error_data['data'], list) and len(error_data['data']) > 0:
              if 'sMsg' in error_data['data'][0] and error_data['data'][0]['sMsg']:
                user_error_msg = error_data['data'][0]['sMsg']
          elif isinstance(error_data, list) and len(error_data) > 0 and 'sMsg' in error_data[0]:
            user_error_msg = error_data[0]['sMsg']
       
        ########### ADD HERE MORE EXCHANGE SPECIFIC RESPONSE HANDLING
        # elif trader.__class__.__name__ == 'BinanceTrader':
        #   # Binance typically uses 'msg' field
        #   if isinstance(error_data, dict) and 'msg' in error_data:
        #     user_error_msg = error_data['msg']
        # elif trader.__class__.__name__ == 'CoinbaseTrader':
        #   # Coinbase typically uses 'message' field
        #   if isinstance(error_data, dict) and 'message' in error_data:
        #     user_error_msg = error_data['message']
        # Add more exchange-specific parsing as needed
        # For unknown exchanges, we'll fall back to the original error message
            
    except (json.JSONDecodeError, KeyError, IndexError) as parse_error:
      # If JSON parsing fails, use the original error message
      trader.logger.debug(f"JSON parsing failed: {parse_error}")
      pass
    
    # Always log the full original error message for debugging
    # trader.logger.error(f"Error during fiat conversion for {trader_id}: {original_error_msg}")
    
    # But return the cleaned user-friendly message to the frontend
    return JSONResponse(
      status_code=500,
      content={"error": user_error_msg}
    )

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
