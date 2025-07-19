import os
import asyncio
from dotenv import load_dotenv
from misc.logger import CustomLogger

# Import the specific trader classes
from traders.okx_trader import OKXTrader
# from traders.coinbasepro_trader import CoinbaseProTrader

# --- Load Environment Variables ---
# This line loads variables from the .env file into the script's environment.
# Make sure your .env file is in the same directory as this script.
load_dotenv()

logger = CustomLogger("MAIN")

async def main_app():
  """
  Main application orchestrator to manage multiple trading accounts across exchanges.
  """

  # Define a mapping from exchange_id string (from ENV) to its corresponding class
  EXCHANGE_TRADER_CLASSES = {
    'okx': OKXTrader,
    # 'coinbasepro': CoinbaseProTrader,
    # Add other exchanges here as you create their trader classes
    # 'binance': BinanceTrader,
  }


  # Get the list of active configurations from the environment variable
  # Example: ACTIVE_TRADING_CONFIGS="MYBOT_OKX,MANUAL_COINBASEPRO"
  active_configs_str = os.getenv('ACTIVE_TRADING_CONFIGS')
  if not active_configs_str:
    logger.error("Error: ACTIVE_TRADING_CONFIGS environment variable is not set. "
                  "Please define which accounts to load (e.g., 'MYBOT_OKX,MYBOT_COINBASEPRO').")
    return
  

  # Parse the comma-separated list of active configurations
  config_strings = [cfg.strip() for cfg in active_configs_str.split(',') if cfg.strip()]

  traders = {} # Dictionary to store instantiated trader objects {config_name: trader_instance}

  logger.info("\n--- Initializing Traders ---")
  for config_str in config_strings:
    # Expected format: ACCOUNT_IDENTIFIER_EXCHANGE_NAME (e.g., MYBOT_OKX)
    parts = config_str.split('_')
    if len(parts) < 2:
      logger.warning(f"Warning: Invalid config string format '{config_str}'. Expected format is'IDENTIFIER_EXCHANGE', e.g., MYBOT_OKX...Skipping.")
      continue

  # Reconstruct account_identifier (in case it contains underscores)
  account_identifier = "_".join(parts[:-1])
  exchange_id_str = parts[-1].lower()

  trader_class = EXCHANGE_TRADER_CLASSES.get(exchange_id_str)

  if trader_class:
    try:
      # Instantiate the correct trader class based on exchange_id
      traders[config_str] = trader_class(account_identifier=account_identifier)
    except ValueError as e:
      logger.warning(f"Skipping setup for {config_str}: {e}")
    except Exception as e:
      logger.error(f"An unexpected error during instantiation of {config_str}: {e}")
  else:
    logger.warning(f"Warning: No trader class found for exchange ID '{exchange_id_str}' (from config '{config_str}'). Skipping.")
  # Now iterate through instantiated traders and perform operations
  if not traders:
    logger.error("\nNo traders were successfully initialized. Exiting.")
    return

  logger.info("\n--- Performing Operations for Active Traders ---")
  for name, trader in traders.items():
    logger.info(f"\n--- Operations for {name} ({trader.exchange_id.upper()}) ---")

    # Fetch Balance
    balance = await trader.fetch_balance()

    if balance:
      
      # Fetch Open Orders (using a common symbol for demonstration)
      # You might want to customize symbols based on the exchange or account
      await trader.fetch_open_orders('BTC/SGD')


      # Place and Cancel a Dummy Order (for demonstration)
      # IMPORTANT: Adjust symbol, amount, and price for real-world testing.
      # Use extremely small amounts and unrealistic prices for safety!
      # dummy_order = await trader.create_order(
      #     symbol='DOGE/USDT',
      #     side='buy',
      #     spend_percentage=0.001, # Use a very small percentage for testing
      #     order_execution_strategy='market' # or 'maker_limit'
      # )
      # if dummy_order and dummy_order.get('id'):
      #   # For market orders, there's no order to cancel as they fill immediately.
      #   # This cancellation would only apply if order_execution_strategy was 'maker_limit'
      #   # and the order didn't fill immediately.
      #   if dummy_order.get('status') == 'open':
      #       await trader.cancel_order(dummy_order['id'], dummy_order['symbol'])
      #   else:
      #       logger.info(f"Dummy order {dummy_order['id']} was not open, no need to cancel.")
    else:
      logger.warning(f"Could not fetch balance for {name}, skipping further operations.")

  logger.info("\n--- Multi-Exchange Trading Application Finished ---")
  await trader.close()

if __name__ == '__main__':
  asyncio.run(main_app())