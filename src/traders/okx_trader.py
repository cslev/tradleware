import ccxt
from traders.base_trader import BaseExchangeTrader

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
    super().__init__(account_identifier, 'OKX', default_type)

    okx_options = {
      'defaultType': self.default_type,
      'subAccount': self.subaccount_name, # CRITICAL: This tells ccxt to target the subaccount
    }

    # Initialize ccxt.okx with credentials and specific options
    self.exchange = ccxt.okx({
      'apiKey': self.api_key,
      'secret': self.secret_key,
      'password': self.passphrase, # OKX uses 'password' for the passphrase
      'options': okx_options,
      'enableRateLimit': True, # Always good to enable rate limiting
    })
    print(f"OKXTrader initialized for {self.account_identifier} (Subaccount: {self.subaccount_name})")

  async def fetch_balance(self):
    """
    Fetches and prints the balance for the initialized OKX subaccount.
    """
    print(f"\nFetching balance for OKX subaccount: {self.subaccount_name}...")
    balance = await self._safe_api_call(self.exchange.fetch_balance)
    if balance:
      print(f"Balance for {self.subaccount_name}:")
      found_assets = False
      for currency, data in balance['total'].items():
        if data > 0:
          print(f"  {currency}: {data}")
          found_assets = True
      if not found_assets:
        print("  No assets found in this subaccount.")
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
    print(f"\nFetching open orders for {symbol if symbol else 'all symbols'} on OKX subaccount: {self.subaccount_name}...")
    orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol, since, limit, params)
    if orders:
      print(f"Open Orders ({symbol if symbol else 'all'}) for {self.subaccount_name}:")
      for order in orders:
        print(f"  ID: {order['id']}, Symbol: {order['symbol']}, Type: {order['type']}, "
              f"Side: {order['side']}, Price: {order['price']}, Amount: {order['amount']}, "
              f"Filled: {order['filled']}, Status: {order['status']}")
    else:
      print(f"  No open orders found for {symbol if symbol else 'all symbols'} on this subaccount.")
    return orders

  async def create_order(self, symbol: str, side: str, spend_percentage: float = 1.0, order_execution_strategy: str = 'market', params: dict = {}):
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
      print("Error: spend_percentage must be between 0.0 and 1.0.")
      return None

    # 2. Determine base and quote currencies and market limits/precision
    market = None
    try:
      # Load market info if not already loaded, or refresh it
      self.exchange.load_markets(reload=True)
      market = self.exchange.market(symbol)
      base_currency = market['base']
      quote_currency = market['quote']
    except ccxt.ExchangeError as e:
      print(f"Exchange error loading market for {symbol}: {e}")
      return None
    except Exception as e:
      print(f"Error loading market for {symbol}: {e}")
      return None

    if not market:
      print(f"Could not load market data for {symbol}. Cannot proceed with order.")
      return None

    # Extract limits and precision for the symbol
    amount_limits = market['limits']['amount']
    price_limits = market['limits']['price']
    cost_limits = market['limits']['cost'] # Often relevant for market orders

    amount_precision = market['precision']['amount']
    price_precision = market['precision']['price']

    # 3. Get current balance
    balance_info = await self.fetch_balance()
    if not balance_info:
      print("Could not fetch balance to determine order amount.")
      return None

    amount_to_trade = 0.0
    price = None
    order_type = 'market'

    print(f"\nAttempting to create a {side} order for {symbol} with {spend_percentage*100}% of available funds.")

    if side == 'buy':
      available_quote = balance_info['free'].get(quote_currency, 0.0)
      spend_cost = available_quote * spend_percentage

      if spend_cost <= 0:
        print(f"Insufficient {quote_currency} balance ({available_quote}) to place buy order.")
        return None

      if order_execution_strategy == 'market':
        order_type = 'market'
        # For market buy, amount parameter in ccxt is usually the 'cost' (quote currency amount)
        amount_to_trade = spend_cost
        print(f"Calculated market buy cost: {amount_to_trade} {quote_currency}")

        # Check against min/max cost limits
        min_cost = cost_limits.get('min', 0)
        max_cost = cost_limits.get('max', float('inf'))
        if amount_to_trade < min_cost:
          print(f"Adjusting buy cost: {amount_to_trade} is less than min_cost {min_cost}. Setting to min_cost.")
          amount_to_trade = min_cost
        if amount_to_trade > max_cost:
          print(f"Adjusting buy cost: {amount_to_trade} is greater than max_cost {max_cost}. Setting to max_cost.")
          amount_to_trade = max_cost

      elif order_execution_strategy == 'maker_limit':
        order_type = 'limit'
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker or not ticker.get('bid'):
          print(f"Could not fetch bid price for {symbol} to determine maker buy price.")
          return None

        # Set price slightly below current bid to try and ensure maker fee
        # This is a common strategy, but execution is NOT guaranteed immediately.
        # The goal is to be at the top of the buy order book.
        price = ticker['bid'] * 0.9999 # Try to be 0.01% below bid to be maker

        # Apply price precision first
        price = self.exchange.price_to_precision(symbol, price)

        # Calculate amount in base currency based on desired spend and maker price
        if price <= 0: # Avoid division by zero
          print("Calculated maker buy price is zero or negative. Cannot place order.")
          return None
        amount_to_trade = spend_cost / price
        print(f"Calculated maker limit buy amount: {amount_to_trade} {base_currency} at price {price}")

        # Check against min/max amount limits
        min_amount = amount_limits.get('min', 0)
        max_amount = amount_limits.get('max', float('inf'))
        if amount_to_trade < min_amount:
          print(f"Adjusting buy amount: {amount_to_trade} is less than min_amount {min_amount}. Setting to min_amount.")
          amount_to_trade = min_amount
        if amount_to_trade > max_amount:
          print(f"Adjusting buy amount: {amount_to_trade} is greater than max_amount {max_amount}. Setting to max_amount.")
          amount_to_trade = max_amount

      else:
        print(f"Unsupported order execution strategy: {order_execution_strategy}")
        return None

    elif side == 'sell':
      available_base = balance_info['free'].get(base_currency, 0.0)
      amount_to_trade = available_base * spend_percentage

      if amount_to_trade <= 0:
        print(f"Insufficient {base_currency} balance ({available_base}) to place sell order.")
        return None

      # Check against min/max amount limits for sell orders
      min_amount = amount_limits.get('min', 0)
      max_amount = amount_limits.get('max', float('inf'))
      if amount_to_trade < min_amount:
        print(f"Adjusting sell amount: {amount_to_trade} is less than min_amount {min_amount}. Setting to min_amount.")
        amount_to_trade = min_amount
      if amount_to_trade > max_amount:
        print(f"Adjusting sell amount: {amount_to_trade} is greater than max_amount {max_amount}. Setting to max_amount.")
        amount_to_trade = max_amount

      if order_execution_strategy == 'market':
        order_type = 'market'
        print(f"Calculated market sell amount: {amount_to_trade} {base_currency}")
      elif order_execution_strategy == 'maker_limit':
        order_type = 'limit'
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker or not ticker.get('ask'):
          print(f"Could not fetch ask price for {symbol} to determine maker sell price.")
          return None

        # Set price slightly above current ask to try and ensure maker fee
        # The goal is to be at the top of the sell order book.
        price = ticker['ask'] * 1.0001 # Try to be 0.01% above ask to be maker

        # Apply price precision
        price = self.exchange.price_to_precision(symbol, price)
        print(f"Calculated maker limit sell amount: {amount_to_trade} {base_currency} at price {price}")
      else:
        print(f"Unsupported order execution strategy: {order_execution_strategy}")
        return None
    else:
      print(f"Invalid order side: {side}. Must be 'buy' or 'sell'.")
      return None

    # Final check for amount before applying precision and placing order
    if amount_to_trade <= 0:
      print("Calculated amount to trade is zero or negative after adjustments. Order not placed.")
      return None

    # Apply amount precision as the final step
    amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)

    print(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Amount={amount_to_trade}, Price={price}")

    # Place the order
    order = await self._safe_api_call(self.exchange.create_order, symbol, order_type, side, amount_to_trade, price, params)
    if order:
      print(f"Order placed successfully! Order ID: {order['id']}")
      print(f"  Status: {order['status']}, Price: {order['price']}, Amount: {order['amount']}")
    return order

  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = {}):
    """
    Cancels an order by its ID on the OKX subaccount.

    Args:
      order_id (str): The ID of the order to cancel.
      symbol (str): The trading pair symbol associated with the order.
      params (dict): Additional exchange-specific parameters.
    """
    print(f"\nAttempting to cancel order ID: {order_id} for {symbol} on OKX subaccount: {self.subaccount_name}...")
    cancel_result = await self._safe_api_call(self.exchange.cancel_order, order_id, symbol, params)
    if cancel_result:
      print(f"Order {order_id} cancelled successfully! Status: {cancel_result['status']}")
    return cancel_result
