import ccxt
from ccxt import async_support as ccxt_async # Use an alias to avoid name collision
from typing import Optional, Dict, Any, List
import os

from src.traders.base_trader import BaseExchangeTrader  # Changed from traders.base_trader
from src.misc.logger import CustomLogger
import asyncio # Imported for asyncio.iscoroutinefunction for debugging

class OKXTrader(BaseExchangeTrader):
  """
  Concrete trader class for the OKX exchange.
  Handles OKX-specific initialization and API calls, including subaccount management.
  """
  def __init__(self, account_identifier: str, default_type: str = 'spot'):
    """
    Initializes the OKXTrader for a specific OKX subaccount.

    Args:
      account_identifier (str): A unique name for this OKX trading setup.
      default_type (str): The default market type for OKX (e.g., 'spot', 'future').
    """
    self.logger = CustomLogger(name=self.__class__.__name__,
                              gotify_url=os.getenv('GOTIFY_SERVER_URL'),
                              gotify_token=os.getenv('GOTIFY_APP_TOKEN'),
                              gotify_log_level=int(os.getenv('GOTIFY_LOG_LEVEL', 30)))

    super().__init__(account_identifier, 'OKX', default_type, self.logger)

    okx_options = {
      'defaultType': self.default_type,
      'subAccount': self.subaccount_name, # CRITICAL: This tells ccxt to target the subaccount

    }
    # self.logger.info(f"OKX options: {okx_options}")
    # self.logger.info(f"exchange details:\napiKey:{self.api_key}\nsecret:{self.secret_key}\npassword:{self.passphrase}\noptions:{okx_options}")
    # Initialize ccxt_async.okx with credentials and specific options
    self.exchange = ccxt_async.okx({
      'apiKey': self.api_key,
      'secret': self.secret_key,
      'password': self.passphrase, # OKX uses 'password' for the passphrase
      'hostname': self.hostname if self.hostname else "okx.com",
      'options': okx_options,
      'enableRateLimit': True, # Always good to enable rate limiting
    })

    self.logger.success(f"OKXTrader initialized for {self.account_identifier} (Subaccount: {self.subaccount_name})")



  async def fetch_balance(self) -> Dict[str, Any]:
    """
    Fetches and prints the balance for the initialized OKX subaccount.
    """
    self.logger.info(f"Fetching balance for OKX subaccount: {self.subaccount_name}...")

    ############ DEBUG - can remove after confirmed it's working #############
    # DEBUG: Print the type of the method before passing it to _safe_api_call
    self.logger.debug(f"Type of self.exchange.fetch_balance before _safe_api_call: {type(self.exchange.fetch_balance)}")
    # This check is useful for diagnostics, keep it if you want
    if not asyncio.iscoroutinefunction(self.exchange.fetch_balance):
        self.logger.error(f"ERROR: self.exchange.fetch_balance is not an async function! Type: {type(self.exchange.fetch_balance)}")
        # If it's not an async function, it might be a dictionary or None, leading to the await error.
        # This is very unusual for ccxt methods.
        return None
    ############################# DEBUG END ###################################
    balance = await self._safe_api_call(self.exchange.fetch_balance)
    if balance:
      self.logger.info(f"Balance for {self.subaccount_name}:\n")
      found_assets = False
      for currency, data in balance['total'].items():
        if data > 0:
          self.logger.info(f"  {currency}: {data}")
          found_assets = True
      if not found_assets:
        self.logger.warning("❌ No assets found in this subaccount.")
    return balance




  async def fetch_open_orders(self, symbol: str = None, since: int = None, limit: int = None, params: dict = {}):
    """
    Fetches and prints open orders for a given symbol on the OKX subaccount.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT'). If None, fetches all.
      since (int): Timestamp in milliseconds to fetch orders since.
      limit (int): Maximum number of orders to fetch.
      params (dict): Additional exchange-specific parameters.
    """
    self.logger.info(f"Fetching open orders for {symbol if symbol else 'all symbols'} on OKX subaccount: {self.subaccount_name}...")
    orders=None
    try:
      orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol, since, limit, params)

      if orders:
        self.logger.info(f"Open Orders ({symbol if symbol else 'all'}) for {self.subaccount_name}:")
        for order in orders:
          self.logger.info(f"  ID: {order['id']}, Symbol: {order['symbol']}, Type: {order['type']}, "
                f"Side: {order['side']}, Price: {order['price']}, Amount: {order['amount']}, "
                f"Filled: {order['filled']}, Status: {order['status']}")
      else:
        self.logger.info(f"❌ No open orders found for {symbol if symbol else 'all symbols'} on this subaccount.")
    except Exception as e:
      
      self.logger.warning(f"Are you sure you set the symbol correclty?")
      await self.list_fiat_markets()
    finally:
      return orders


  async def list_fiat_markets(self, fiat_currency:str="SGD"):
    """
    Fetches and lists all markets on OKX that involve fiat_currency.
    This helps in identifying the correct trading symbol if BTC/fiat_currency isn't directly available.
    Args:
      fiat_currency 
    """
    self.logger.info(f"Loading all markets for {self.exchange_id} to find {fiat_currency} pairs...")
    try:
      # Ensure markets are loaded/reloaded to get the latest list
      await self._safe_api_call(self.exchange.load_markets, True) # Set to True to force reload

      self.logger.info(f"Markets loaded. Filtering for {fiat_currency} related pairs...")
      fiat_markets = []
      for symbol, market in self.exchange.markets.items():
        # Check if {fiat_currency} is in the symbol name or if base/quote currency is {fiat_currency}
        if fiat_currency in symbol.upper() or market['base'] == fiat_currency or market['quote'] == fiat_currency:
          fiat_markets.append(market)

      if fiat_markets:
        self.logger.success(f"Found {len(fiat_markets)} {fiat_currency} related markets on {self.exchange_id}:")
        for market in fiat_markets:
          self.logger.info(
            f"  Symbol: {market['symbol']}, "
            f"Type: {market['type']}, "
            f"Base: {market['base']}, "
            f"Quote: {market['quote']}, "
            f"Active: {market['active']}"
          )
      else:
        self.logger.warning(f"No direct {fiat_currency} trading pairs found on {self.exchange_id} via CCXT for the '{self.default_type}' type.")
        self.logger.info("It's possible you need to convert SGD to a stablecoin (e.g., USDT) first, then trade via crypto/stablecoin pairs (e.g., BTC/USDT).")

    except Exception as e:
      self.logger.error(f"❌  Error listing {fiat_currency} markets for {self.exchange_id}: {e}")
    return fiat_markets



  async def convert_fiat_to_stablecoin( self, 
                                        spend_percentage: float = 1.0, 
                                        order_execution_strategy: str = 'market',
                                        max_slippage: float = 0.05):  # Keep and use it
    """
    Converts a percentage of available fiat currency (e.g., SGD) into a stablecoin (e.g., USDT).

    Args:
      spend_percentage (float): The percentage of available fiat funds to spend (0.0 to 1.0).
      order_execution_strategy (str): 'market' for immediate execution, 'maker_limit' for limit order.
      max_slippage (float): Maximum allowed slippage for market orders (0.0 to 1.0).
    """
    self.logger.info(f"\nAttempting to convert {spend_percentage*100}% of {self.fiat_currency} to {self.stablecoin_currency} via {self.fiat_stablecoin_pair}...")
    if not (0.0 < spend_percentage <= 1.0):
      self.logger.error("❌ spend_percentage must be between 0.0 (exclusive) and 1.0 (inclusive).")
      return 0.0
    
    # 1. Fetch current balance
    balance_info = await self.fetch_balance()
    if not balance_info:
      error_msg = "Could not fetch account balance from exchange"
      self.logger.error(f"❌ {error_msg} to determine fiat funds for conversion.")
      raise RuntimeError(error_msg)

    fiat_available = balance_info['free'].get(self.fiat_currency, 0.0)
    self.logger.info(f"Available {self.fiat_currency}: {fiat_available}")

    if fiat_available <= 0:
      error_msg = f"No {self.fiat_currency} available in the account (balance: {fiat_available})"
      self.logger.warning(f"⚠️ {error_msg}. Cannot proceed with conversion.")
      raise ValueError(error_msg)
    
    # Calculate the amount of fiat to spend
    fiat_spend_amount = fiat_available * spend_percentage
    self.logger.info(f"Calculated amount to spend: {fiat_spend_amount} {self.fiat_currency}")
    if fiat_spend_amount <= 0:
        error_msg = f"Calculated spend amount is zero or negative ({fiat_spend_amount:.2f} {self.fiat_currency})"
        self.logger.warning(f"⚠️ {error_msg}. Cannot place order.")
        raise ValueError(error_msg)

    # Check against exchange minimum limits by attempting the order
    # The create_order method will handle minimum amount validation using actual exchange limits
    
    # 2. Add slippage protection for market orders
    if order_execution_strategy == 'market':
      # Get current market price before placing order
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, self.fiat_stablecoin_pair)
      if not ticker:
        self.logger.error("Could not fetch ticker for slippage calculation")
        return 0.0
      
      expected_price = ticker['ask']  # Expected buy price
      self.logger.info(f"Current market price: {expected_price}")
    
    # 3. Place the order
    stablecoin_order = await self.create_order(
      symbol=self.fiat_stablecoin_pair,
      side='buy',
      spend_percentage=spend_percentage,
      order_execution_strategy=order_execution_strategy
    )
    
    if not stablecoin_order:
          self.logger.error(f"❌ Order to buy {self.stablecoin_currency} failed or returned no order object.")
          return 0.0

    # For market orders, the order might be executed immediately but status might not be populated
    # Let's fetch the order details to get accurate information
    order_id = stablecoin_order.get('id')
    if order_id and order_execution_strategy == 'market':
        # Wait a moment and fetch the order details
        await asyncio.sleep(1)
        try:
            updated_order = await self._safe_api_call(self.exchange.fetch_order, order_id, self.fiat_stablecoin_pair)
            if updated_order:
                stablecoin_order = updated_order
                self.logger.info(f"Updated order status: {stablecoin_order.get('status')}, filled: {stablecoin_order.get('filled', 0)}")
        except Exception as e:
            self.logger.warning(f"Could not fetch updated order details: {e}")

    # Check order status - for market orders, status might be 'closed' or 'filled'
    order_status = stablecoin_order.get('status')
    if order_status not in ['closed', 'filled'] and order_execution_strategy == 'market':
        self.logger.warning(f"⚠️ Market order to buy {self.stablecoin_currency} was not immediately executed. Current status: {order_status}")
        # For market orders, even if status is unclear, check if we have filled amount
        filled_amount = stablecoin_order.get('filled', 0)
        if filled_amount > 0:
            self.logger.info(f"✅ Order partially/fully filled: {filled_amount} {self.stablecoin_currency}")
        else:
            return 0.0
    elif order_execution_strategy == 'maker_limit' and order_status == 'open':
        self.logger.info("Limit order placed, monitoring for completion...")
        # Could add order monitoring logic here

    # 4. Check slippage for market orders
    if order_execution_strategy == 'market' and stablecoin_order:
      actual_price = stablecoin_order.get('average') or stablecoin_order.get('price', 0)
      if actual_price and expected_price:
        slippage = abs(actual_price - expected_price) / expected_price
        if slippage > max_slippage:
          self.logger.warning(f"⚠️ High slippage detected: {slippage:.2%} (limit: {max_slippage:.2%})")
          # Could implement cancellation logic here if slippage is too high
        else:
          self.logger.info(f"✅ Slippage within limits: {slippage:.2%}")
    
    # Get the filled amount
    filled_amount = stablecoin_order.get('filled', 0) or 0
    
    self.logger.success(f"✅ Successfully converted {fiat_spend_amount} {self.fiat_currency} to {filled_amount} {self.stablecoin_currency}!")
    return filled_amount



  async def create_order(self, 
                         symbol: str, 
                         side: str, 
                         spend_percentage: float = 1.0, 
                         order_execution_strategy: str = 'market', 
                         params: dict = {}):
    """
    Creates an order on the OKX subaccount with flexible execution and amount.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT').
      side (str): The order side ('buy' or 'sell').
      spend_percentage (float): The percentage of available funds/asset to spend/sell (0.0 to 1.0).
      order_execution_strategy (str): 'market' for immediate execution (taker fee),
                                      'maker_limit' for a limit order aiming for maker fee.
      params (dict): Additional exchange-specific parameters.
    """
    # 1. Input validation for spend_percentage
    if not (0.0 <= spend_percentage <= 1.0):
      self.logger.error("Error: spend_percentage must be between 0.0 and 1.0.")
      return None

    # 2. Determine base and quote currencies and market limits/precision
    market = None
    try:
      # Load market info if not already loaded, or refresh it
      await self.exchange.load_markets(reload=True)
      market = self.exchange.market(symbol)
      base_currency = market['base']
      quote_currency = market['quote']
    except ccxt.ExchangeError as e:
      self.logger.error(f"Exchange error loading market for {symbol}: {e}")
      return None
    except Exception as e:
      self.logger.error(f"Error loading market for {symbol}: {e}")
      return None

    if not market:
      self.logger.error(f"Could not load market data for {symbol}. Cannot proceed with order.")
      return None

    # Extract limits and precision for the symbol
    amount_limits = market['limits']['amount']
    cost_limits = market['limits']['cost'] # Often relevant for market orders

    # 3. Get current balance
    balance_info = await self.fetch_balance()
    if not balance_info:
      self.logger.error("Could not fetch balance to determine order amount.")
      return None

    amount_to_trade = 0.0
    price = None
    order_type = 'market'

    self.logger.info(f"\nAttempting to create a {side} order for {symbol} with {spend_percentage*100}% of available funds.")

    if side == 'buy':
      available_quote = balance_info['free'].get(quote_currency, 0.0)
      spend_cost = available_quote * spend_percentage

      if spend_cost <= 0:
        self.logger.error(f"Insufficient {quote_currency} balance ({available_quote}) to place buy order.")
        return None

      if order_execution_strategy == 'market':
        order_type = 'market'
        # For market buy, amount parameter in ccxt is usually the 'cost' (quote currency amount)
        amount_to_trade = spend_cost
        self.logger.info(f"Calculated market buy cost: {amount_to_trade} {quote_currency}")

        # Check against min/max cost limits
        min_cost = cost_limits.get('min') or 0
        max_cost = cost_limits.get('max') or float('inf')
        if amount_to_trade < min_cost:
          error_msg = f"Order amount {amount_to_trade:.2f} {quote_currency} is below exchange minimum {min_cost:.2f} {quote_currency}"
          self.logger.error(error_msg)
          raise ValueError(error_msg)
        if amount_to_trade > max_cost:
          error_msg = f"Order amount {amount_to_trade:.2f} {quote_currency} exceeds exchange maximum {max_cost:.2f} {quote_currency}"
          self.logger.error(error_msg)
          raise ValueError(error_msg)

      elif order_execution_strategy == 'maker_limit':
        order_type = 'limit'
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker or not ticker.get('bid'):
          self.logger.error(f"Could not fetch bid price for {symbol} to determine maker buy price.")
          return None

        # Set price slightly below current bid to try and ensure maker fee
        # This is a common strategy, but execution is NOT guaranteed immediately.
        # The goal is to be at the top of the buy order book.
        price = ticker['bid'] * 0.9999 # Try to be 0.01% below bid to be maker

        # Apply price precision first
        price = self.exchange.price_to_precision(symbol, price)

        # Calculate amount in base currency based on desired spend and maker price
        if price <= 0: # Avoid division by zero
          self.logger.error("Calculated maker buy price is zero or negative. Cannot place order.")
          return None
        amount_to_trade = spend_cost / price
        self.logger.info(f"Calculated maker limit buy amount: {amount_to_trade} {base_currency} at price {price}")

        # Check against min/max amount limits
        min_amount = amount_limits.get('min') or 0
        max_amount = amount_limits.get('max') or float('inf')
        if amount_to_trade < min_amount:
          error_msg = f"Order amount {amount_to_trade:.6f} {base_currency} is below exchange minimum {min_amount:.6f} {base_currency}"
          self.logger.error(error_msg)
          raise ValueError(error_msg)
        if amount_to_trade > max_amount:
          error_msg = f"Order amount {amount_to_trade:.6f} {base_currency} exceeds exchange maximum {max_amount:.6f} {base_currency}"
          self.logger.error(error_msg)
          raise ValueError(error_msg)

      else:
        self.logger.error(f"Unsupported order execution strategy: {order_execution_strategy}")
        return None

    elif side == 'sell':
      available_base = balance_info['free'].get(base_currency, 0.0)
      amount_to_trade = available_base * spend_percentage

      if amount_to_trade <= 0:
        self.logger.error(f"Insufficient {base_currency} balance ({available_base}) to place sell order.")
        return None

      # Check against min/max amount limits for sell orders
      min_amount = amount_limits.get('min') or 0
      max_amount = amount_limits.get('max') or float('inf')
      if amount_to_trade < min_amount:
        error_msg = f"Sell amount {amount_to_trade:.6f} {base_currency} is below exchange minimum {min_amount:.6f} {base_currency}"
        self.logger.error(error_msg)
        raise ValueError(error_msg)
      if amount_to_trade > max_amount:
        error_msg = f"Sell amount {amount_to_trade:.6f} {base_currency} exceeds exchange maximum {max_amount:.6f} {base_currency}"
        self.logger.error(error_msg)
        raise ValueError(error_msg)

      if order_execution_strategy == 'market':
        order_type = 'market'
        self.logger.info(f"Calculated market sell amount: {amount_to_trade} {base_currency}")
      elif order_execution_strategy == 'maker_limit':
        order_type = 'limit'
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker or not ticker.get('ask'):
          self.logger.error(f"Could not fetch ask price for {symbol} to determine maker sell price.")
          return None

        # Set price slightly above current ask to try and ensure maker fee
        # The goal is to be at the top of the sell order book.
        price = ticker['ask'] * 1.0001 # Try to be 0.01% above ask to be maker

        # Apply price precision
        price = self.exchange.price_to_precision(symbol, price)
        self.logger.info(f"Calculated maker limit sell amount: {amount_to_trade} {base_currency} at price {price}")
      else:
        self.logger.error(f"Unsupported order execution strategy: {order_execution_strategy}")
        return None
    else:
      self.logger.error(f"Invalid order side: {side}. Must be 'buy' or 'sell'.")
      return None

    # Final check for amount before applying precision and placing order
    if amount_to_trade <= 0:
      self.logger.error("Calculated amount to trade is zero or negative after adjustments. Order not placed.")
      return None

    # Apply amount precision as the final step
    amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)

    self.logger.info(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Amount={amount_to_trade}, Price={price}")

    # Place the order
    order = await self._safe_api_call(self.exchange.create_order, symbol, order_type, side, amount_to_trade, price, params)
    if order:
      self.logger.success(f"Order placed successfully! Order ID: {order['id']}")
      self.logger.success(f"  Status: {order['status']}, Price: {order['price']}, Amount: {order['amount']}")
    return order

  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = {}):
    """
    Cancels an order by its ID on the OKX subaccount.

    Args:
      order_id (str): The ID of the order to cancel.
      symbol (str): The trading pair symbol associated with the order.
      params (dict): Additional exchange-specific parameters.
    """
    self.logger.info(f"\nAttempting to cancel order ID: {order_id} for {symbol} on OKX subaccount: {self.subaccount_name}...")
    cancel_result = await self._safe_api_call(self.exchange.cancel_order, order_id, symbol, params)
    if cancel_result:
      self.logger.success(f"Order {order_id} cancelled successfully! Status: {cancel_result['status']}")
    return cancel_result
