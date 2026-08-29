import asyncio # Imported for asyncio.sleep

from datetime import datetime
from typing import Dict, Any
from typing import Optional, List
import traceback

import ccxt
from ccxt import async_support as ccxt_async # Use an alias to avoid name collision

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env  # Import centralized get_env helper

class OKXTrader(BaseCryptoTrader):
  """
  Concrete trader class for the OKX exchange.
  Handles OKX-specific initialization and API calls, including subaccount management.
  """
  def __init__(self, config: dict, default_type: str = 'spot'):
    """
    Initializes the OKXTrader for a specific OKX subaccount.

    Args:
      config (dict): Bot configuration dict from config_loader.
      default_type (str): The default market type for OKX (e.g., 'spot', 'future').
    """
    self.logger = CustomLogger(name=self.__class__.__name__,
                              gotify_url=get_env('GOTIFY_SERVER_URL'),
                              gotify_token=get_env('GOTIFY_APP_TOKEN'),
                              gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30')))

    super().__init__(config, default_type, self.logger)

    okx_options = {
      'defaultType': self.default_type,
      'subAccount': self.subaccount_name, # CRITICAL: This tells ccxt to target the subaccount
    }

    if not self.hostname:
      self.hostname = 'my.okx.com'

    # Initialize ccxt_async.okx with credentials and specific options
    self.exchange = ccxt_async.okx({
      'apiKey': self.api_key,
      'secret': self.secret_key,
      'password': self.passphrase, # OKX uses 'password' for the passphrase
      'hostname': self.hostname,
      'options': okx_options,
      'enableRateLimit': True, # Always good to enable rate limiting
    })

    self.logger.success(f"OKXTrader initialized for {self.account_identifier} (Subaccount: {self.subaccount_name})")

  async def fetch_balance(self) -> Dict[str, Any]:
    """
    Fetches and prints the balance for the initialized OKX subaccount.
    """
    self.logger.info(f"Fetching balance for OKX subaccount: {self.subaccount_name}...")

    # Call via _safe_api_call to keep exception handling consistent
    balance = await self._safe_api_call(self.exchange.fetch_balance)
    if balance:
      self.logger.info(f"Balance for {self.subaccount_name}:\n")
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
        self.logger.warning("❌ No assets found in this subaccount.")
    return balance




  async def fetch_open_orders(self,
                              symbol: str = None,
                              since: int = None,
                              limit: int = None,
                              params: dict = None
                              ) -> Optional[List[Dict[str, Any]]]:
    """
    Fetches all open orders for the OKX subaccount.

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
    self.logger.info(f"\nFetching open orders for {symbol} on OKX subaccount: {self.subaccount_name}...")
    orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol, since, limit, params)
    return orders


  async def list_fiat_markets(self, fiat_currency:str="SGD") -> List[Dict[str, Any]]:
    """
    Fetches and lists all markets on OKX that involve fiat_currency.
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
        self.logger.info("It's possible you need to convert SGD to a stablecoin (e.g., USDT) first, then trade via crypto/stablecoin pairs (e.g., BTC/USDT).")

    except Exception as exc:
      self.logger.error(f"❌  Error listing {fiat_currency} markets for {self.exchange_id}: {exc}")
    return fiat_markets


  async def convert_fiat_to_stablecoin( self,
                                        spend_percentage: float = 1.0,
                                        order_execution_strategy: str = 'market',
                                        max_slippage: float = 0.05) -> float:
    """
    Converts a percentage of available fiat currency (e.g., SGD) into a stablecoin (e.g., USDT).

    Args:
      spend_percentage (float): The percentage of available fiat funds to spend (0.0 to 1.0).
      order_execution_strategy (str): 'market' for immediate execution, 'maker_limit' for limit order.
      max_slippage (float): Maximum allowed slippage for market orders (0.0 to 1.0).
    """
    self.logger.info(f"\nAttempting to convert {spend_percentage*100}% of {self.fiat_currency} to {self.stablecoin_currency} via {self.stablecoin_fiat_pair}...")
    if not 0.0 < spend_percentage <= 1.0:
      self.logger.error("❌ spend_percentage must be between 0.0 (exclusive) and 1.0 (inclusive).")
      return 0.0

    # 1. Add slippage protection for market orders
    expected_price = None
    if order_execution_strategy == 'market':
      # Get current market price before placing order
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, self.stablecoin_fiat_pair)
      if not ticker:
        self.logger.error("Could not fetch ticker for slippage calculation")
        return 0.0

      expected_price = ticker['ask']  # Expected buy price
      self.logger.info(f"Current market price: {expected_price}")

    # 2. Place the order
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

    # Check order status - for market orders, status might be 'closed' or 'filled'
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

    # 3. Check slippage for market orders
    if order_execution_strategy == 'market' and stablecoin_order:
      actual_price = stablecoin_order.get('average') or stablecoin_order.get('price', 0)
      if actual_price and expected_price:
        slippage = abs(actual_price - expected_price) / expected_price
        if slippage > max_slippage:
          self.logger.warning(f"⚠️ High slippage detected: {slippage:.2%} (limit: {max_slippage:.2%})")
        else:
          self.logger.info(f"✅ Slippage within limits: {slippage:.2%}")

    # Get the filled amount and actual cost from the order (more accurate than pre-calculated estimate)
    filled_amount = stablecoin_order.get('filled', 0) or 0
    actual_cost = stablecoin_order.get('cost') or 0

    self.logger.success(f"✅ Successfully converted {actual_cost} {self.fiat_currency} to {filled_amount} {self.stablecoin_currency}!")
    return filled_amount



  async def create_order(self,
                         symbol: str,
                         side: str,
                         spend_percentage: float = None,
                         quantity: float = None,
                         order_execution_strategy: str = 'market',
                         dry_run: bool = False,
                         params: dict = None,
                         *,
                         spend_amount: float = None) -> Optional[Dict[str, Any]]:
    """
    Creates an order on the OKX subaccount with flexible execution and amount.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT').
      side (str): The order side ('buy' or 'sell').
      spend_percentage (float): The percentage of available funds/asset to spend/sell (0.0 to 1.0).
                                Either spend_percentage or quantity must be provided.
      quantity (float): The exact amount of base currency to buy/sell (e.g., 0.5 BTC).
                        Either spend_percentage or quantity must be provided.
      order_execution_strategy (str): 'market' for immediate execution (taker fee),
                                      'maker_limit' for a limit order aiming for maker fee.
      dry_run (bool): If True, simulate the order without executing it (default: False).
      params (dict): Additional exchange-specific parameters.
    """
    self.logger.debug("[CREATE ORDER] starting order creation process...")
    if params is None:
      params = {}

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 1 — VALIDATE PARAMETERS  (base class: _validate_order_params)
    # Checks symbol, side, mutually-exclusive amount fields,
    # order_execution_strategy, dry_run, and numeric ranges.
    # ─────────────────────────────────────────────────────────────────────────
    try:
      self._validate_order_params(symbol,
                                   side,
                                   spend_percentage,
                                   quantity,
                                   order_execution_strategy=order_execution_strategy,
                                   spend_amount=spend_amount,
                                   dry_run=dry_run)
      self.logger.info("Order parameters validated successfully.")
    except ValueError as e:
      self.logger.error(f"Order validation failed: {e}")
      return None

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 2 — RESOLVE MARKET DATA & BALANCE  (base class: _resolve_market_and_balance)
    # Loads the CCXT market dict for the symbol and fetches live account
    # balances. Returns a ctx dict with base/quote, amount/cost limits,
    # and free/total balance snapshots.
    # ─────────────────────────────────────────────────────────────────────────
    try:
      ctx = await self._resolve_market_and_balance(symbol)
    except RuntimeError as exc:
      self.logger.error(f"{exc}")
      return None

    base_currency  = ctx['base']
    quote_currency = ctx['quote']

    self.logger.debug(f"[CREATE ORDER] quantity={quantity}, spend_percentage={spend_percentage}, spend_amount={spend_amount}")

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 3 — CALCULATE ORDER SIZE  (base class: _calculate_order_size)
    # Resolves (order_type, amount_to_trade, price) from the mode
    # (spend_percentage vs quantity) and execution strategy.
    # For spend% market buy: amount_to_trade is in QUOTE currency (cost);
    # all other cases: amount_to_trade is in BASE currency, precision applied.
    # ─────────────────────────────────────────────────────────────────────────
    try:
      order_type, amount_to_trade, price = await self._calculate_order_size(
        symbol=symbol,
        side=side,
        ctx=ctx,
        spend_percentage=spend_percentage,
        quantity=quantity,
        spend_amount=spend_amount,
        order_execution_strategy=order_execution_strategy,
      )
    except (ValueError, RuntimeError) as exc:
      self.logger.error(f"Order sizing failed: {exc}")
      return None

    # ─────────────────────────────────────────────────────────────────────────
    # DRY RUN — simulate order without execution
    # ─────────────────────────────────────────────────────────────────────────
    if dry_run:
      self.logger.warning("🧪 DRY RUN: Order simulation complete (NOT executed)")

      # For percentage mode market buy, amount_to_trade is in quote currency
      if self.is_cost_denominated(order_type, side, spend_percentage, spend_amount):
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        sim_price = ticker['last'] if ticker and ticker.get('last') else 0
        sim_amount = amount_to_trade / sim_price if sim_price > 0 else 0

        mock_order = {
          'id': 'DRY_RUN_' + str(int(datetime.now().timestamp())),
          'symbol': symbol,
          'type': order_type,
          'side': side,
          'amount': sim_amount,
          'price': sim_price,
          'status': 'simulated',
          'filled': 0,
          'remaining': sim_amount,
          'cost': amount_to_trade,
          'timestamp': int(datetime.now().timestamp() * 1000),
          'datetime': datetime.now().isoformat(),
          'info': {'dry_run': True}
        }
        self.logger.info("🧪 Simulated order details:")
        self.logger.info(f"  ID: {mock_order['id']}")
        self.logger.info(f"  {side.upper()} ~{sim_amount:.8f} {base_currency} with {amount_to_trade:.2f} {quote_currency} (MARKET)")
        return mock_order
      # For all other cases, amount_to_trade is in base currency
      amount_to_trade_precise = self.exchange.amount_to_precision(symbol, amount_to_trade)
      mock_order = {
        'id': 'DRY_RUN_' + str(int(datetime.now().timestamp())),
        'symbol': symbol,
        'type': order_type,
        'side': side,
        'amount': float(amount_to_trade_precise),
        'price': float(price) if price else None,
        'status': 'simulated',
        'filled': 0,
        'remaining': float(amount_to_trade_precise),
        'cost': 0,
        'timestamp': int(datetime.now().timestamp() * 1000),
        'datetime': datetime.now().isoformat(),
        'info': {'dry_run': True}
      }
      self.logger.info("🧪 Simulated order details:")
      self.logger.info(f"  ID: {mock_order['id']}")
      self.logger.info(f"  {side.upper()} {amount_to_trade_precise} {base_currency}" +
                      (f" @ {price} {quote_currency}" if price else " (MARKET)"))
      return mock_order

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 4 — EXECUTE ORDER  (OKX-specific, pending extraction to base class)
    # Handles OKX's createMarketBuyOrderWithCost for spend% market buys, then
    # falls back to the standard CCXT create_order call for all other cases.
    # ─────────────────────────────────────────────────────────────────────────

    # Special handling for OKX market buy orders in PERCENTAGE MODE only
    # In percentage mode, we spend a % of quote currency, so amount_to_trade is in quote currency
    # In quantity mode, amount_to_trade is already in base currency, so skip this special handling
    if self.is_cost_denominated(order_type, side, spend_percentage, spend_amount):  # pylint: disable=no-else-return
      # For OKX market buy orders, use createMarketBuyOrderWithCost to spend exact quote amount
      # This avoids precision loss from converting to base amount
      try:
        # Check if exchange supports createMarketBuyOrderWithCost
        if hasattr(self.exchange, 'createMarketBuyOrderWithCost'):
          self.logger.info(f"Using createMarketBuyOrderWithCost to spend exact {amount_to_trade} {quote_currency}")
          # Set the required parameter to avoid KeyError
          if 'createMarketBuyOrderRequiresPrice' not in self.exchange.options:
            self.exchange.options['createMarketBuyOrderRequiresPrice'] = False

          order = await self._safe_api_call(self.exchange.createMarketBuyOrderWithCost, symbol, amount_to_trade, params)
          if order:
            # Log order details
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
            self.logger.error(f"❌ Failed to place order for {symbol}. The exchange API call returned None (check logs above for details).")
          return order
      except (AttributeError, ccxt.NotSupported, KeyError) as e:
        self.logger.info(f"createMarketBuyOrderWithCost not supported or failed ({e}), using standard market order with cost parameter")

      # Fallback: Use standard create_order with cost in params for OKX
      # OKX accepts 'sz' (size) parameter which can be the quote currency amount for market buys
      self.logger.info(f"Using standard market order with cost: {amount_to_trade} {quote_currency}")
      # Don't convert to base amount - pass quote amount directly and set createMarketBuyOrderRequiresPrice to false
      params['createMarketBuyOrderRequiresPrice'] = False

      # For OKX, we can pass the quote amount directly as amount for market buy orders
      # The exchange will interpret it as the cost to spend
      amount_to_trade_final = amount_to_trade
      price = None

      self.logger.info(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Cost={amount_to_trade_final} {quote_currency} (Market Order)")

      # Place the order with cost parameter
      order = await self._safe_api_call(self.exchange.create_order, symbol, order_type, side, amount_to_trade_final, price, params)
      if order:
        # Defensive logging: order may be a dict or an object and fields may be missing (mocks/tests)
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
        self.logger.error(f"❌ Failed to place order for {symbol}. The exchange API call returned None (check logs above for details).")
      return order

    else:  # pylint: disable=no-else-return
      # All other order types: quantity mode, market sell, limit orders
      # Apply standard precision and place order

      # For quantity mode, check if precision adjustment changes the amount
      if quantity is not None:
        original_amount = amount_to_trade
        amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)
        if abs(float(amount_to_trade) - original_amount) > 0.0001:  # Significant difference
          self.logger.warning(f"⚠️ Requested quantity {original_amount} {base_currency} adjusted to {amount_to_trade} {base_currency} due to exchange precision rules")
      else:
        amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)

      # Consistent logging - show price only if it's a limit order
      if price is not None:
        self.logger.info(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Amount={amount_to_trade}, Price={price}")
      else:
        self.logger.info(f"Placing order: Symbol={symbol}, Type={order_type}, Side={side}, Amount={amount_to_trade} (Market Order)")

      # Place the order
      order = await self._safe_api_call(self.exchange.create_order,
                                        symbol,
                                        order_type,
                                        side,
                                        amount_to_trade,
                                        price,
                                        params)
      if order:
        # Defensive logging: order may be a dict or an object and fields may be missing (mocks/tests)
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




  async def cancel_order(self,
                         order_id: str,
                         symbol: str = None,
                         params: dict = None
                         ) -> Optional[Dict[str, Any]]:
    """
    Cancels an order by its ID on the OKX subaccount.

    Args:
      order_id (str): The ID of the order to cancel.
      symbol (str): The trading pair symbol associated with the order.
      params (dict): Additional exchange-specific parameters.
    """
    if params is None:
      params = {}
    self.logger.info(f"\nAttempting to cancel order ID: {order_id} for {symbol} on OKX subaccount: {self.subaccount_name}...")
    cancel_result = await self._safe_api_call(self.exchange.cancel_order, order_id, symbol, params)
    if cancel_result:
      self.logger.success(f"Order {order_id} cancelled successfully! Status: {cancel_result['status']}")
    return cancel_result


if __name__ == "__main__":
  # Test script for OKXTrader - Run this file directly to test basic functionality.
  # Requires a configured bot_configs/crypto/okx.yaml file.

  from src.misc.config_loader import get_bot_configs # pylint: disable=wrong-import-position

  async def test_okx_trader():
    """Test basic OKX trader functionality"""
    bots = [b for b in get_bot_configs() if b.get('exchange') == 'okx']
    if not bots:
      print("❌ No OKX bot configs found in bot_configs/crypto/okx.yaml")
      return

    config = bots[0]
    print(f"\n{'='*60}")
    print(f"Testing OKXTrader with bot: {config['id']}")
    print(f"{'='*60}\n")

    trader = None
    try:
      trader = OKXTrader(config)
      await trader.post_init()

      print("\n--- Test 1: Fetching Balance ---")
      balance = await trader.fetch_balance()
      print(f"Balance fetched successfully: {balance}")

      print("\n--- Test 2: Trading Pair Validity ---")
      print(f"Configured pair: {trader.crypto_stablecoin_pair}")
      print(f"Pair valid: {trader.trading_pair_valid}")

      print("\n--- Test 3: Fetching Open Orders ---")
      orders = await trader.fetch_open_orders(trader.crypto_stablecoin_pair)
      if orders:
        print(f"Found {len(orders)} open orders")
        for order in orders[:3]:
          print(f"  Order {order.get('id')}: {order.get('side')} {order.get('amount')} @ {order.get('price')}")
      else:
        print("No open orders found")

      if trader.fiat_currency:
        print(f"\n--- Test 4: Listing {trader.fiat_currency} Markets ---")
        fiat_markets = await trader.list_fiat_markets(trader.fiat_currency)
        if fiat_markets:
          print(f"Found {len(fiat_markets)} {trader.fiat_currency} markets")

      print(f"\n{'='*60}")
      print("✅ All tests completed successfully!")
      print(f"{'='*60}\n")

    except Exception as e: # pylint: disable=broad-exception-caught
      print(f"\n❌ Error during testing: {e}")
      traceback.print_exc()

    finally:
      if trader:
        await trader.close()
        print("Connection closed.")

  asyncio.run(test_okx_trader())
