import asyncio # Imported for asyncio.sleep

from pathlib import Path
from typing import Dict, Any
from typing import Optional, List
import traceback

import ccxt
from ccxt import async_support as ccxt_async # Use an alias to avoid name collision

from dotenv import load_dotenv

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader  # Changed from traders.base_trader
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env  # Import centralized get_env helper

class CryptocomTrader(BaseCryptoTrader):
  """
  Concrete trader class for the Crypto.com exchange.
  Handles Crypto.com-specific initialization and API calls.
  """
  def __init__(self, account_identifier: str, default_type: str = 'spot'):
    """
    Initializes the CryptocomTrader for Crypto.com exchange.

    Args:
      account_identifier (str): A unique name for this Crypto.com trading setup.
      default_type (str): The default market type for Crypto.com (e.g., 'spot', 'margin').
    """
    self.logger = CustomLogger(name=self.__class__.__name__,
                              gotify_url=get_env('GOTIFY_SERVER_URL'),
                              gotify_token=get_env('GOTIFY_APP_TOKEN'),
                              gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30')))

    super().__init__(account_identifier, 'CRYPTOCOM', default_type, self.logger)

    cryptocom_options = {
      'defaultType': self.default_type,
    }

    # Add subaccount support if configured
    if self.subaccount_name:
      cryptocom_options['subAccount'] = self.subaccount_name

    # Initialize ccxt_async.cryptocom with credentials and specific options
    self.exchange = ccxt_async.cryptocom({
      'apiKey': self.api_key,
      'secret': self.secret_key,
      'hostname': self.hostname if self.hostname else "api.crypto.com",
      'options': cryptocom_options,
      'enableRateLimit': True, # Always good to enable rate limiting
    })

    subaccount_info = f" (Subaccount: {self.subaccount_name})" if self.subaccount_name else ""
    self.logger.success(f"CryptocomTrader initialized for {self.account_identifier}{subaccount_info}")

  async def fetch_balance(self) -> Dict[str, Any]:
    """
    Fetches and prints the balance for the initialized Crypto.com account.
    """
    self.logger.info(f"Fetching balance for Crypto.com account: {self.account_identifier}...")

    # Call via _safe_api_call to keep exception handling consistent
    balance = await self._safe_api_call(self.exchange.fetch_balance)
    if balance:
      self.logger.info(f"Balance for {self.account_identifier}:\n")
      found_assets = False
      total_balances = (balance.get('total') or {})
      for currency, data in total_balances.items():
        try:
          if data and float(data) > 0:
            self.logger.info(f"  {currency}: {data}")
            found_assets = True
        except Exception:
          # skip non-numeric entries gracefully
          continue
      if not found_assets:
        self.logger.warning("❌ No assets found in this account.")
    return balance

  async def fetch_open_orders(self, symbol: str = None, since: int = None, limit: int = None, params: dict = None) -> Optional[List[Dict[str, Any]]]:
    """
    Fetches all open orders for the Crypto.com account.

    Args:
      symbol (str): The trading pair symbol to filter orders by (e.g., 'BTC/USDT'). If None, fetches orders for all symbols.
      since (int): Filter orders created after this timestamp (in milliseconds).
      limit (int): Maximum number of orders to fetch.
      params (dict): Additional exchange-specific parameters.

    Returns:
      list: A list of open orders for the specified symbol or all symbols.
    """
    if params is None:
      params = {}
    self.logger.info(f"\nFetching open orders for {symbol} on Crypto.com account: {self.account_identifier}...")
    orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol, since, limit, params)
    return orders

  async def list_fiat_markets(self, fiat_currency:str="SGD"):
    """
    Fetches and lists all markets on Crypto.com that involve fiat_currency.
    This helps in identifying the correct trading symbol if BTC/fiat_currency isn't directly available.
    Args:
      fiat_currency
    """
    self.logger.info(f"Loading all markets for {self.exchange_id} to find {fiat_currency} pairs...")
    fiat_markets = []  # Initialize before try block to ensure it's always defined
    try:
      # Ensure markets are loaded/reloaded to get the latest list
      await self._safe_api_call(self.exchange.load_markets, True) # Set to True to force reload

      self.logger.info(f"Markets loaded. Filtering for {fiat_currency} related pairs...")
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
        self.logger.info("It's possible you need to convert fiat to a stablecoin (e.g., USDT) first, then trade via crypto/stablecoin pairs (e.g., BTC/USDT).")

    except Exception as exc:
      self.logger.error(f"❌  Error listing {fiat_currency} markets for {self.exchange_id}: {exc}")
    return fiat_markets

  async def convert_fiat_to_stablecoin(self,
                                        spend_percentage: float = 1.0,
                                        order_execution_strategy: str = 'market',
                                        max_slippage: float = 0.05):
    """
    Converts a percentage of available fiat currency into a stablecoin.

    Args:
      spend_percentage (float): The percentage of available fiat funds to spend (0.0 to 1.0).
      order_execution_strategy (str): 'market' for immediate execution, 'maker_limit' for limit order.
      max_slippage (float): Maximum allowed slippage for market orders (0.0 to 1.0).
    """
    self.logger.info(f"\nAttempting to convert {spend_percentage*100}% of {self.fiat_currency} to {self.stablecoin_currency} via {self.stablecoin_fiat_pair}...")
    if not 0.0 < spend_percentage <= 1.0:
      self.logger.error("❌ spend_percentage must be between 0.0 (exclusive) and 1.0 (inclusive).")
      return 0.0

    # 1. Fetch current balance
    balance_info = await self.fetch_balance()
    if not balance_info:
      error_msg = "Could not fetch account balance from exchange"
      self.logger.error(f"❌ {error_msg} to determine fiat funds for conversion.")
      raise RuntimeError(error_msg)

    # Defensive balance access: prefer 'free' if present, otherwise fall back to 'total'
    total_balances = (balance_info.get('total') or {})
    free_balances = (balance_info.get('free') or {})
    fiat_available = free_balances.get(self.fiat_currency, total_balances.get(self.fiat_currency, 0.0))
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

    # 2. Add slippage protection for market orders
    if order_execution_strategy == 'market':
      # Get current market price before placing order
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, self.stablecoin_fiat_pair)
      if not ticker:
        self.logger.error("Could not fetch ticker for slippage calculation")
        return 0.0

      expected_price = ticker['ask']  # Expected buy price
      self.logger.info(f"Current market price: {expected_price}")

    # 3. Place the order
    stablecoin_order = await self.create_order(
      symbol=self.stablecoin_fiat_pair,
      side='buy',
      spend_percentage=spend_percentage,
      order_execution_strategy=order_execution_strategy
    )

    if not stablecoin_order:
      self.logger.error(f"❌ Order to buy {self.stablecoin_currency} failed or returned no order object.")
      return 0.0

    # For market orders, the order might be executed immediately but status might not be populated
    order_id = stablecoin_order.get('id')
    if order_id and order_execution_strategy == 'market':
      # Wait a moment and fetch the order details
      await asyncio.sleep(1)
      try:
        updated_order = await self._safe_api_call(self.exchange.fetch_order, order_id, self.stablecoin_fiat_pair)
        if updated_order:
          stablecoin_order = updated_order
          self.logger.info(f"Updated order status: {stablecoin_order.get('status')}, filled: {stablecoin_order.get('filled', 0)}")
      except Exception as exc:
        self.logger.warning(f"Could not fetch updated order details: {exc}")

    # Check order status
    order_status = stablecoin_order.get('status')
    if order_status not in ['closed', 'filled'] and order_execution_strategy == 'market':
      self.logger.warning(f"⚠️ Market order to buy {self.stablecoin_currency} was not immediately executed. Current status: {order_status}")
      filled_amount = stablecoin_order.get('filled', 0)
      if filled_amount > 0:
        self.logger.info(f"✅ Order partially/fully filled: {filled_amount} {self.stablecoin_currency}")
      else:
        return 0.0
    elif order_execution_strategy == 'maker_limit' and order_status == 'open':
      self.logger.info("Limit order placed, monitoring for completion...")

    # 4. Check slippage for market orders
    if order_execution_strategy == 'market' and stablecoin_order:
      actual_price = stablecoin_order.get('average') or stablecoin_order.get('price', 0)
      if actual_price and expected_price:
        slippage = abs(actual_price - expected_price) / expected_price
        if slippage > max_slippage:
          self.logger.warning(f"⚠️ High slippage detected: {slippage:.2%} (limit: {max_slippage:.2%})")
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
                         params: dict = None):
    """
    Creates an order on the Crypto.com account with flexible execution and amount.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT').
      side (str): The order side ('buy' or 'sell').
      spend_percentage (float): The percentage of available funds/asset to spend/sell (0.0 to 1.0).
      order_execution_strategy (str): 'market' for immediate execution (taker fee),
                                      'maker_limit' for a limit order aiming for maker fee.
      params (dict): Additional exchange-specific parameters.
    """
    if params is None:
      params = {}

    # 1. Input validation for spend_percentage
    if not 0.0 <= spend_percentage <= 1.0:
      self.logger.error("Error: spend_percentage must be between 0.0 and 1.0.")
      return None

    # 2. Determine base and quote currencies and market limits/precision
    market = None
    try:
      load_result = await self._safe_api_call(self.exchange.load_markets, True)
      if load_result is None and not getattr(self.exchange, "markets", None):
        self.logger.error(f"Failed to (re)load markets for {symbol}. Aborting order creation.")
        return None
      market = self.exchange.market(symbol)
      base_currency = market['base']
      quote_currency = market['quote']
    except ccxt.ExchangeError as exc:
      self.logger.error(f"Exchange error loading market for {symbol}: {exc}")
      return None
    except Exception as exc:
      self.logger.error(f"Error loading market for {symbol}: {exc}")
      return None

    if not market:
      self.logger.error(f"Could not load market data for {symbol}. Cannot proceed with order.")
      return None

    # Extract limits and precision for the symbol
    limits = market.get('limits', {}) or {}
    amount_limits = limits.get('amount', {}) or {}
    cost_limits = limits.get('cost', {}) or {}

    # 3. Get current balance
    balance_info = await self.fetch_balance()
    if not balance_info:
      self.logger.error("Could not fetch balance to determine order amount.")
      return None

    # Defensive balance access: prefer 'free' then 'total'
    total_balances = (balance_info.get('total') or {})
    free_balances = (balance_info.get('free') or {})

    amount_to_trade = 0.0
    price = None
    order_type = 'market'

    self.logger.info(f"\nAttempting to create a {side} order for {symbol} with {spend_percentage*100}% of available funds.")

    if side == 'buy':
      available_quote = free_balances.get(quote_currency, total_balances.get(quote_currency, 0.0))
      spend_cost = available_quote * spend_percentage

      if spend_cost <= 0:
        self.logger.error(f"Insufficient {quote_currency} balance ({available_quote}) to place buy order.")
        return None

      if order_execution_strategy == 'market':
        order_type = 'market'
        # Use full spend_cost - let exchange handle fees
        amount_to_trade = spend_cost
        self.logger.info(f"Calculated market buy cost: {amount_to_trade} {quote_currency}")

        # Check against min/max cost limits
        min_cost = cost_limits.get('min', None)
        max_cost = cost_limits.get('max', None)
        if min_cost is not None and amount_to_trade < min_cost:
          error_msg = f"Order amount {amount_to_trade:.2f} {quote_currency} is below exchange minimum {min_cost:.2f} {quote_currency}"
          self.logger.error(error_msg)
          raise ValueError(error_msg)
        if max_cost is not None and amount_to_trade > max_cost:
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
        price = ticker['bid'] * 0.9999

        # Apply price precision first
        price = self.exchange.price_to_precision(symbol, price)

        # Calculate amount in base currency based on desired spend and maker price
        if price <= 0:
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
      available_base = free_balances.get(base_currency, total_balances.get(base_currency, 0.0))
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
        price = ticker['ask'] * 1.0001

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
    if order_type == 'market' and side == 'buy':
      # For Crypto.com market buy orders, check if exchange supports cost-based ordering
      try:
        # Check if exchange supports createMarketBuyOrderWithCost
        if hasattr(self.exchange, 'createMarketBuyOrderWithCost'):
          self.logger.info(f"Using createMarketBuyOrderWithCost to spend exact {amount_to_trade} {quote_currency}")
          # Set the required parameter to avoid KeyError
          if 'createMarketBuyOrderRequiresPrice' not in self.exchange.options:
            self.exchange.options['createMarketBuyOrderRequiresPrice'] = False

          order = await self._safe_api_call(self.exchange.createMarketBuyOrderWithCost, symbol, amount_to_trade, params)
          if order:
            if isinstance(order, dict):
              order_id = order.get('id', 'unknown')
              status = order.get('status', 'unknown')
              filled_amount = order.get('filled', None)
            else:
              order_id = getattr(order, 'id', 'unknown')
              status = getattr(order, 'status', 'unknown')
              filled_amount = getattr(order, 'filled', None)

            filled_str = f"{filled_amount}" if filled_amount is not None else "N/A"
            self.logger.success(f"Order placed successfully! Order ID: {order_id}")
            self.logger.success(f"  Status: {status}, Filled: {filled_str} {base_currency}")
          else:
            self.logger.error(f"❌ Failed to place order for {symbol}.")
          return order
      except (AttributeError, ccxt.NotSupported, KeyError) as e:
        self.logger.warning(f"createMarketBuyOrderWithCost not supported or failed ({e}), converting to base amount")

      # Fallback: Convert to base amount using ticker price
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
      if ticker is None:
        self.logger.error(f"Could not fetch market price for {symbol} to calculate buy amount.")
        return None
      if not isinstance(ticker, dict):
        try:
          ticker = dict(ticker)
        except Exception:
          try:
            ticker = {k: getattr(ticker, k) for k in ('ask', 'bid', 'last') if hasattr(ticker, k)}
          except Exception:
            ticker = {}

      expected_price = ticker.get('ask') or ticker.get('last') or ticker.get('bid')
      if expected_price is None:
        self.logger.error(f"Could not determine price from ticker for {symbol}: {ticker}")
        return None

      # Store original cost for reference
      original_cost = amount_to_trade

      # Only apply buffer for crypto/stablecoin pairs, not for fiat/stablecoin conversions
      # Fiat conversions are automatically calculated by exchange, but crypto trades need dust for fees
      is_crypto_pair = symbol == self.crypto_stablecoin_pair

      if is_crypto_pair:
        # Apply a 0.5% buffer to account for exchange fees (leaves dust for fees)
        # This prevents "insufficient balance" errors when exchange deducts fees from quote currency
        adjusted_cost = amount_to_trade * 0.995
        base_amount = adjusted_cost / expected_price
        amount_to_trade = base_amount
        self.logger.info(f"Fallback: Calculated {amount_to_trade:.8f} {base_currency} at market price {expected_price:.8f} (spending ~{adjusted_cost:.2f} {quote_currency} of available {original_cost:.2f} {quote_currency}, leaving {original_cost - adjusted_cost:.4f} {quote_currency} for fees)")
      else:
        # For fiat/stablecoin conversions, use full amount (exchange handles fees automatically)
        base_amount = amount_to_trade / expected_price
        amount_to_trade = base_amount
        self.logger.info(f"Fallback: Calculated {amount_to_trade:.8f} {base_currency} at market price {expected_price:.8f} for {original_cost:.2f} {quote_currency} (fiat conversion - no buffer)")

      price = None

    # Apply amount precision - this should only be applied once, to the base currency amount
    if order_type == 'market' and side == 'buy':
      # For market buy, we already converted to base amount in the fallback above
      amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)
    elif order_type != 'market' or side != 'buy':
      # For all other order types, apply precision normally
      amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)

    # Consistent logging
    if price is not None:
      self.logger.info(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Amount={amount_to_trade}, Price={price}")
    else:
      self.logger.info(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Amount={amount_to_trade} (Market Order)")

    # Place the order
    order = await self._safe_api_call(self.exchange.create_order, symbol, order_type, side, amount_to_trade, price, params)
    if order:
      if isinstance(order, dict):
        order_id = order.get('id', 'unknown')
        status = order.get('status', 'unknown')
        filled = order.get('filled', 0)
        average = order.get('average', None)
        cost = order.get('cost', None)
      else:
        order_id = getattr(order, 'id', 'unknown')
        status = getattr(order, 'status', 'unknown')
        filled = getattr(order, 'filled', 0)
        average = getattr(order, 'average', None)
        cost = getattr(order, 'cost', None)

      self.logger.success(f"Order placed successfully! Order ID: {order_id}")

      # Show meaningful trade information based on buy/sell
      if side == 'buy':
        if filled and average:
          self.logger.success(f"  ✅ Bought {filled:.8f} {base_currency} for {filled * average:.2f} {quote_currency} @ avg price {average:.8f}")
        elif filled and cost:
          self.logger.success(f"  ✅ Bought {filled:.8f} {base_currency} for {cost:.2f} {quote_currency}")
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base_currency}")
      else:  # sell
        if filled and average:
          self.logger.success(f"  ✅ Sold {filled:.8f} {base_currency} for {filled * average:.2f} {quote_currency} @ avg price {average:.8f}")
        elif filled and cost:
          self.logger.success(f"  ✅ Sold {filled:.8f} {base_currency} for {cost:.2f} {quote_currency}")
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base_currency}")
    else:
      self.logger.error(f"❌ Failed to place order for {symbol}. The exchange API call returned None (check logs above for details).")
    return order

  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = None):
    """
    Cancels an order by its ID on the Crypto.com account.

    Args:
      order_id (str): The ID of the order to cancel.
      symbol (str): The trading pair symbol associated with the order.
      params (dict): Additional exchange-specific parameters.
    """
    if params is None:
      params = {}
    self.logger.info(f"\nAttempting to cancel order ID: {order_id} for {symbol} on Crypto.com account: {self.account_identifier}...")
    cancel_result = await self._safe_api_call(self.exchange.cancel_order, order_id, symbol, params)
    if cancel_result:
      self.logger.success(f"Order {order_id} cancelled successfully! Status: {cancel_result['status']}")
    return cancel_result


if __name__ == "__main__":
  # Test script for CryptocomTrader - Run this file directly to test basic functionality.
  # Requires environment variables to be set in .env file.

  # Load environment variables from .env file
  env_path = Path(__file__).parent.parent.parent / '.env'
  print(f"Loading .env from: {env_path}")
  load_dotenv(dotenv_path=env_path, override=True)

  async def test_cryptocom_trader():
    """Test basic Crypto.com trader functionality"""
    # Get the first active CRYPTOCOM config from environment
    active_configs = get_env('ACTIVE_TRADING_CONFIGS', '')
    cryptocom_configs = [c.strip() for c in active_configs.split(',') if 'CRYPTOCOM' in c.upper()]

    if not cryptocom_configs:
      print("❌ No CRYPTOCOM configurations found in ACTIVE_TRADING_CONFIGS")
      print("   Please add a CRYPTOCOM config to your .env file")
      return

    account_identifier = cryptocom_configs[0].rsplit('_', 1)[0]
    print(f"\n{'='*60}")
    print(f"Testing CryptocomTrader with account: {account_identifier}")
    print(f"{'='*60}\n")

    trader = None
    try:
      # Initialize trader
      trader = CryptocomTrader(account_identifier=account_identifier)
      await trader.post_init()

      # Test 1: Fetch balance
      print("\n--- Test 1: Fetching Balance ---")
      balance = await trader.fetch_balance()
      print(f"Balance fetched successfully: {balance}")

      # Test 2: Check trading pair validity
      print("\n--- Test 2: Trading Pair Validity ---")
      print(f"Configured pair: {trader.crypto_stablecoin_pair}")
      print(f"Pair valid: {trader.trading_pair_valid}")

      # Test 3: Fetch open orders
      print("\n--- Test 3: Fetching Open Orders ---")
      orders = await trader.fetch_open_orders(trader.crypto_stablecoin_pair)
      if orders:
        print(f"Found {len(orders)} open orders")
        for order in orders[:3]:  # Show first 3
          print(f"  Order {order.get('id')}: {order.get('side')} {order.get('amount')} @ {order.get('price')}")
      else:
        print("No open orders found")

      # Test 4: List fiat markets (optional)
      if trader.fiat_currency:
        print(f"\n--- Test 4: Listing {trader.fiat_currency} Markets ---")
        fiat_markets = await trader.list_fiat_markets(trader.fiat_currency)
        if fiat_markets:
          print(f"Found {len(fiat_markets)} {trader.fiat_currency} markets")

      print(f"\n{'='*60}")
      print("✅ All tests completed successfully!")
      print(f"{'='*60}\n")

    except Exception as e:
      print(f"\n❌ Error during testing: {e}")
      traceback.print_exc()

    finally:
      # Clean up
      if trader:
        await trader.close()
        print("Connection closed.")

  # Run the test
  asyncio.run(test_cryptocom_trader())
