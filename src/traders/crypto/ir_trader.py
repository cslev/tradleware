import asyncio
from pathlib import Path
from typing import Dict, Any
from typing import Optional, List
import traceback

from ccxt import async_support as ccxt_async # Use an alias to avoid name collision

from dotenv import load_dotenv
from src.traders.crypto.base_crypto_trader import BaseCryptoTrader
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env


class IRTrader(BaseCryptoTrader):
  """
  Independent Reserve trader (ccxt). Mirrors OKXTrader behaviour for basic operations:
  - fetch_balance
  - fetch_open_orders
  - list_fiat_markets
  - convert_fiat_to_stablecoin
  - create_order
  - cancel_order
  """

  def __init__(self, account_identifier: str, default_type: str = "spot"):
    """
    Initialize an IRTrader instance.

    Args:
      account_identifier: Logical name used to resolve environment variables for API credentials.
      default_type: Market type to use by default (e.g. 'spot').

    Behavior:
      - Configures a CustomLogger instance.
      - Calls BaseExchangeTrader.__init__ to load credentials and defaults.
      - Instantiates a ccxt async Independent Reserve exchange object (ccxt_async.independentreserve).
      - Uses self.exchange for all subsequent API interactions.

    Notes:
      - Uses BaseExchangeTrader helper methods (e.g., _safe_api_call) to perform API calls safely.
    """
    self.logger = CustomLogger(
      name=self.__class__.__name__,
      gotify_url=get_env("GOTIFY_SERVER_URL"),
      gotify_token=get_env("GOTIFY_APP_TOKEN"),
      gotify_log_level=int(get_env("GOTIFY_LOG_LEVEL", "30")),
    )

    # Use exchange id string consistent with image naming / env convention
    super().__init__(account_identifier, "IR", default_type, self.logger)

    # Build ccxt options (Independent Reserve typically doesn't use subaccounts)
    ir_options = {
      "defaultType": self.default_type,
    }

    # Initialize ccxt async exchange instance
    self.exchange = ccxt_async.independentreserve({
      "apiKey": self.api_key,
      "secret": self.secret_key,
      'hostname': self.hostname if self.hostname else "independentreserve.com",
      "options": ir_options,
      "enableRateLimit": True,
    })

    self.logger.success(f"IRTrader initialized for {self.account_identifier}")

  async def fetch_balance(self) -> Dict[str, Any]:
    """
    Fetch and log account balances from Independent Reserve.

    Returns:
      The raw balance dictionary as returned by ccxt (may contain 'total' and/or 'free' maps),
      or None on error.

    Notes:
      - Uses _safe_api_call to centralize exception handling.
      - Logs each non-zero currency balance for visibility.
    """
    self.logger.info(f"Fetching balance for IR account: {self.account_identifier}...")
    bal = await self._safe_api_call(self.exchange.fetch_balance)
    if bal:
      self.logger.info(f"Balance for {self.account_identifier}:")
      total_balances = (bal.get("total") or {})
      found = False
      for cur, val in total_balances.items():
        try:
          if val and float(val) > 0:
            self.logger.info(f"  {cur}: {val}")
            found = True
        except Exception:
          continue
      if not found:
        self.logger.warning("❌ No assets found in this account.")
    return bal

  async def fetch_open_orders(self, symbol: str = None, since: int = None, limit: int = None, params: dict = None) -> Optional[List[Dict[str, Any]]]:
    """
    Retrieve open orders for the account.

    Args:
      symbol: Optional market symbol to filter (e.g. "BTC/SGD"). If None, returns all open orders.
      since: Timestamp (ms) to filter orders created after this time (optional).
      limit: Maximum number of orders to return (optional).
      params: Exchange-specific extra parameters (optional).

    Returns:
      A list of open order dicts (or None on failure).
    """
    if params is None:
      params = {}
    self.logger.info(f"Fetching open orders for {symbol} on IR...")
    orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol, since, limit, params)
    return orders

  async def list_fiat_markets(self, fiat_currency: str = "SGD"):
    """
    List markets on the exchange that involve the provided fiat currency.

    Args:
      fiat_currency: Fiat currency symbol to search for (default: "SGD").

    Returns:
      A list of market dicts that reference the given fiat currency in base or quote.
    """
    self.logger.info(f"Loading markets for {self.exchange_id} to find {fiat_currency} pairs...")
    fiat_markets = []
    try:
      await self._safe_api_call(self.exchange.load_markets, True)
      for sym, market in (getattr(self.exchange, "markets", {}) or {}).items():
        try:
          if fiat_currency in sym.upper() or market.get("base") == fiat_currency or market.get("quote") == fiat_currency:
            fiat_markets.append(market)
        except Exception:
          continue
      if fiat_markets:
        self.logger.success(f"Found {len(fiat_markets)} {fiat_currency} markets on {self.exchange_id}")
        for m in fiat_markets:
          self.logger.info(f"  {m.get('symbol')}  base={m.get('base')} quote={m.get('quote')}")
      else:
        self.logger.warning(f"No {fiat_currency} markets found on {self.exchange_id}.")
    except Exception as exc:
      self.logger.error(f"Error loading markets: {exc}")
    return fiat_markets

  async def convert_fiat_to_stablecoin(self,
                                       spend_percentage: float = 1.0,
                                       order_execution_strategy: str = "market",
                                       max_slippage: float = 0.05):
    """
    Convert a percentage of the account's fiat balance into the configured stablecoin.

    Args:
      spend_percentage: Fraction of available fiat to spend (0.0 < spend_percentage <= 1.0).
      order_execution_strategy: 'market' or 'maker_limit' determining execution style.
      max_slippage: Maximum acceptable slippage (not strictly enforced by this helper but logged).

    Returns:
      The filled amount of stablecoin (numeric) on success, or 0.0 on failure.

    Behavior:
      - Reads balances via fetch_balance().
  - Submits a buy order using create_order() for the configured stablecoin_fiat_pair.
      - Optionally refreshes the order (market flow) to obtain the final filled amount.
    """
    self.logger.info(f"Converting {spend_percentage*100}% {self.fiat_currency} -> {self.stablecoin_currency} via {self.stablecoin_fiat_pair}")
    if not 0.0 < spend_percentage <= 1.0:
      self.logger.error("spend_percentage must be (0.0, 1.0].")
      return 0.0

    balance = await self.fetch_balance()
    if not balance:
      self.logger.error("Could not fetch balance for conversion.")
      return 0.0

    total_bal = (balance.get("total") or {})
    free_bal = (balance.get("free") or {})
    fiat_available = free_bal.get(self.fiat_currency, total_bal.get(self.fiat_currency, 0.0))
    self.logger.info(f"Available {self.fiat_currency}: {fiat_available}")
    if fiat_available <= 0:
      self.logger.warning("No fiat available for conversion.")
      return 0.0

    # Place order using create_order which enforces limits/precision
    order = await self.create_order(
      symbol=self.stablecoin_fiat_pair,
      side="buy",
      spend_percentage=spend_percentage,
      order_execution_strategy=order_execution_strategy,
      params=None
    )

    if not order:
      self.logger.error("Stablecoin buy order failed.")
      return 0.0

    order_id = None
    if isinstance(order, dict):
      order_id = order.get("id")
    else:
      order_id = getattr(order, "id", None)

    # try to refresh order for final filled amount
    if order_id and order_execution_strategy == "market":
      await asyncio.sleep(1)
      try:
        updated = await self._safe_api_call(self.exchange.fetch_order, order_id, self.stablecoin_fiat_pair)
        if updated:
          order = updated
      except Exception as exc:
        self.logger.warning(f"Could not refresh order: {exc}")

    filled = (order.get("filled") if isinstance(order, dict) else getattr(order, "filled", None)) or 0
    self.logger.success(f"Converted {fiat_available*spend_percentage} {self.fiat_currency} -> {filled} {self.stablecoin_currency}")
    return filled

  async def create_order(self,
                         symbol: str,
                         side: str,
                         spend_percentage: float = 1.0,
                         order_execution_strategy: str = "market",
                         params: dict = None):
    """
    Create an order on Independent Reserve according to the provided strategy.

    Args:
      symbol: Trading pair symbol (e.g. "USDT/SGD").
      side: 'buy' or 'sell'.
      spend_percentage: Fraction of available quote/base to use (0.0 <= spend_percentage <= 1.0).
      order_execution_strategy: 'market' for immediate execution, 'maker_limit' to post a limit order targeting maker.
      params: Optional exchange-specific params dict.

    Returns:
      The order object/dict returned by the exchange on success, or None on failure.

    Implementation notes:
      - Loads markets safely via _safe_api_call(self.exchange.load_markets, True).
      - Performs defensive lookups for balance keys ('free' / 'total') and market limits.
      - For market buys, it converts quote-cost -> base amount using the ticker price (defensive parsing).
      - All exchange calls are executed through _safe_api_call so CCXT exceptions are handled uniformly.
    """
    if params is None:
      params = {}

    if not 0.0 <= spend_percentage <= 1.0:
      self.logger.error("spend_percentage must be between 0.0 and 1.0")
      return None

    # load markets safely
    await self._safe_api_call(self.exchange.load_markets, True)
    market = None
    try:
      market = self.exchange.market(symbol)
    except Exception as exc:
      self.logger.error(f"Could not load market {symbol}: {exc}")
      return None
    if not market:
      self.logger.error("Market data missing.")
      return None

    # limits = market.get("limits", {}) or {}
    # amount_limits = limits.get("amount", {}) or {}
    # cost_limits = limits.get("cost", {}) or {}

    balance = await self.fetch_balance()
    if not balance:
      return None
    total_bal = (balance.get("total") or {})
    free_bal = (balance.get("free") or {})

    order_type = "market"
    amount_to_trade = 0.0
    price = None

    base = market.get("base")
    quote = market.get("quote")

    if side == "buy":
      available_quote = free_bal.get(quote, total_bal.get(quote, 0.0))
      spend_cost = available_quote * spend_percentage
      if spend_cost <= 0:
        self.logger.error("Insufficient quote balance")
        return None

      if order_execution_strategy == "market":
        order_type = "market"
        # Use full spend_cost - let exchange handle fees (matching OKX behavior)
        amount_to_trade = spend_cost
        self.logger.info(f"Calculated market buy cost: {amount_to_trade} {quote}")
      elif order_execution_strategy == "maker_limit":
        # get price and compute base amount
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker:
          self.logger.error("Could not get ticker for maker_limit buy.")
          return None
        # best attempt to read bid/last/ask
        ask = ticker.get("ask") if isinstance(ticker, dict) else getattr(ticker, "ask", None)
        try:
          ask = float(ask)
        except Exception:
          self.logger.error("Invalid ticker price for maker limit")
          return None
        price = ask * 0.9999
        amount_to_trade = spend_cost / price

    elif side == "sell":
      available_base = free_bal.get(base, total_bal.get(base, 0.0))
      amount_to_trade = available_base * spend_percentage
      if amount_to_trade <= 0:
        self.logger.error("Insufficient base balance")
        return None
      if order_execution_strategy == "maker_limit":
        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker:
          self.logger.error("Could not get ticker for maker_limit sell.")
          return None
        bid = ticker.get("bid") if isinstance(ticker, dict) else getattr(ticker, "bid", None)
        try:
          bid = float(bid)
        except Exception:
          self.logger.error("Invalid ticker price for maker limit sell")
          return None
        price = bid * 1.0001
        order_type = "limit"

    # convert market buy cost->amount if needed
    # For IR, check if exchange supports cost-based market buy orders
    if order_type == "market" and side == "buy":
      # Try to use cost-based ordering if supported (similar to OKX approach)
      try:
        # Check if exchange supports createMarketBuyOrderWithCost
        if hasattr(self.exchange, 'createMarketBuyOrderWithCost'):
          self.logger.info(f"Using createMarketBuyOrderWithCost to spend exact {amount_to_trade} {quote}")
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
            self.logger.success(f"  Status: {status}, Filled: {filled_str} {base}")
          else:
            self.logger.error(f"❌ Failed to place order for {symbol}.")
          return order
      except Exception as e:
        self.logger.info(f"createMarketBuyOrderWithCost not supported or failed ({e}), using standard market order")

      # Fallback: convert to base amount using ticker price
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
      if not ticker:
        self.logger.error("Could not fetch ticker to convert market buy cost")
        return None
      expected_price = None
      if isinstance(ticker, dict):
        expected_price = ticker.get("ask") or ticker.get("last") or ticker.get("bid")
      else:
        expected_price = getattr(ticker, "ask", None) or getattr(ticker, "last", None) or getattr(ticker, "bid", None)
      try:
        expected_price = float(expected_price)
      except Exception:
        self.logger.error("Invalid ticker price for conversion")
        return None

      # IR trader uses full amount - no buffer needed (exchange handles properly)
      base_amount = (amount_to_trade / expected_price) if expected_price else 0.0
      amount_to_trade = base_amount
      self.logger.info(f"Converting to base amount: {amount_to_trade} {base} at market price {expected_price}")
      price = None

    # apply precision
    try:
      amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)
    except Exception:
      # fallback: keep numeric
      pass

    # log and place
    if price is not None:
      self.logger.info(f"Placing {order_type} {side} {amount_to_trade} {base} @ {price}")
    else:
      self.logger.info(f"Placing {order_type} {side} {amount_to_trade} {base} (market/cost style)")

    order = await self._safe_api_call(self.exchange.create_order, symbol, order_type, side, amount_to_trade, price, params)
    if order:
      if isinstance(order, dict):
        oid = order.get("id", "unknown")
        status = order.get("status", "unknown")
        filled = order.get("filled", 0)
        average = order.get("average", None)
        cost = order.get("cost", None)
      else:
        oid = getattr(order, "id", "unknown")
        status = getattr(order, "status", "unknown")
        filled = getattr(order, "filled", 0)
        average = getattr(order, "average", None)
        cost = getattr(order, "cost", None)

      self.logger.success(f"Order placed successfully! Order ID: {oid}")

      # Show meaningful trade information based on buy/sell
      if side == 'buy':
        if filled and average:
          self.logger.success(f"  ✅ Bought {filled:.8f} {base} for {filled * average:.2f} {quote} @ avg price {average:.8f}")
        elif filled and cost:
          self.logger.success(f"  ✅ Bought {filled:.8f} {base} for {cost:.2f} {quote}")
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base}")
      else:  # sell
        if filled and average:
          self.logger.success(f"  ✅ Sold {filled:.8f} {base} for {filled * average:.2f} {quote} @ avg price {average:.8f}")
        elif filled and cost:
          self.logger.success(f"  ✅ Sold {filled:.8f} {base} for {cost:.2f} {quote}")
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base}")
    else:
      self.logger.error("Order call returned no result")

    return order

  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = None):
    """
    Cancel a specific order.

    Args:
      order_id: Order identifier returned by the exchange.
      symbol: Optional market symbol for the order (some exchanges require it).
      params: Optional exchange-specific parameters.

    Returns:
      Exchange response for the cancel operation, or None on failure.
    """
    if params is None:
      params = {}
    self.logger.info(f"Cancelling order {order_id} on IR...")
    res = await self._safe_api_call(self.exchange.cancel_order, order_id, symbol, params)
    if res:
      self.logger.success(f"Order {order_id} cancelled.")
    return res


if __name__ == "__main__":
  # Test script for IRTrader - Run this file directly to test basic functionality.
  # Requires environment variables to be set in .env file.

  # Load environment variables from .env file
  env_path = Path(__file__).parent.parent.parent / '.env'
  print(f"Loading .env from: {env_path}")
  load_dotenv(dotenv_path=env_path, override=True)

  async def test_ir_trader():
    """Test basic IR trader functionality"""
    # Get the first active IR config from environment
    active_configs = get_env('ACTIVE_TRADING_CONFIGS', '')
    ir_configs = [c.strip() for c in active_configs.split(',') if '_IR' in c.upper()]

    if not ir_configs:
      print("❌ No IR configurations found in ACTIVE_TRADING_CONFIGS")
      print("   Please add an IR config to your .env file")
      return

    account_identifier = ir_configs[0].rsplit('_', 1)[0]
    print(f"\n{'='*60}")
    print(f"Testing IRTrader with account: {account_identifier}")
    print(f"{'='*60}\n")

    trader = None
    try:
      # Initialize trader
      trader = IRTrader(account_identifier=account_identifier)
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
  asyncio.run(test_ir_trader())
