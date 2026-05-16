import asyncio
from datetime import datetime
from typing import Dict, Any
from typing import Optional, List
import traceback

from ccxt import async_support as ccxt_async # Use an alias to avoid name collision

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

  def __init__(self, config: dict, default_type: str = "spot"):
    """
    Initialize an IRTrader instance.

    Args:
      config: Bot configuration dict from config_loader.
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
    super().__init__(config, default_type, self.logger)

    # Build ccxt options (Independent Reserve typically doesn't use subaccounts)
    ir_options = {
      "defaultType": self.default_type,
    }

    if not self.hostname:
      self.hostname = 'api.independentreserve.com'

    # Initialize ccxt async exchange instance
    self.exchange = ccxt_async.independentreserve({
      "apiKey": self.api_key,
      "secret": self.secret_key,
      'hostname': self.hostname,
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

  async def list_fiat_markets(self, fiat_currency: str = "SGD") -> List[Dict[str, Any]]:
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
                                       max_slippage: float = 0.05) -> float:
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
    actual_cost = (order.get("cost") if isinstance(order, dict) else getattr(order, "cost", None)) or fiat_available * spend_percentage
    self.logger.success(f"Converted {actual_cost} {self.fiat_currency} -> {filled} {self.stablecoin_currency}")
    return filled

  async def create_order(self,
                         symbol: str,
                         side: str,
                         spend_percentage: float = None,
                         quantity: float = None,
                         order_execution_strategy: str = "market",
                         dry_run: bool = False,
                         params: dict = None) -> Optional[Dict[str, Any]]:
    """
    Create an order on Independent Reserve according to the provided strategy.

    Args:
      symbol: Trading pair symbol (e.g. "USDT/SGD").
      side: 'buy' or 'sell'.
      spend_percentage: Fraction of available quote/base to use (0.0 < x <= 1.0).
                        Mutually exclusive with quantity.
      quantity: Exact base currency amount to buy/sell.
                Mutually exclusive with spend_percentage.
      order_execution_strategy: 'market' for immediate execution, 'maker_limit' to post a limit order targeting maker.
      dry_run: If True, simulate the order without executing it.
      params: Optional exchange-specific params dict.

    Returns:
      The order object/dict returned by the exchange on success, or None on failure.
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
      self._validate_order_params(symbol, side, spend_percentage, quantity,
                                   order_execution_strategy=order_execution_strategy,
                                   dry_run=dry_run)
      self.logger.info("[CREATE ORDER] Order parameters validated successfully.")
    except ValueError as exc:
      self.logger.error(f"Order validation failed: {exc}")
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
      self.logger.error(f"[CREATE ORDER] {exc}")
      return None

    base_currency  = ctx['base']
    quote_currency = ctx['quote']

    self.logger.debug(f"[CREATE ORDER] quantity={quantity}, spend_percentage={spend_percentage}")

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
        order_execution_strategy=order_execution_strategy,
      )
    except (ValueError, RuntimeError) as exc:
      self.logger.error(f"[CREATE ORDER] Order sizing failed: {exc}")
      return None

    # ─────────────────────────────────────────────────────────────────────────
    # DRY RUN — simulate order without execution
    # ─────────────────────────────────────────────────────────────────────────
    if dry_run:
      self.logger.warning("🧪 DRY RUN: Order simulation complete (NOT executed)")

      # For percentage mode market buy, amount_to_trade is in quote currency
      if order_type == 'market' and side == 'buy' and spend_percentage is not None:
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
      amount_to_trade_precise = self._safe_amount_to_precision(symbol, amount_to_trade)
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
    # LAYER 4 — EXECUTE ORDER  (IR-specific: no createMarketBuyOrderWithCost)
    # For spend% market buy: amount_to_trade is in QUOTE currency; convert to
    # base via ticker fetch before calling create_order. All other cases
    # (quantity mode, sell, limit) go directly to the standard CCXT path.
    # ─────────────────────────────────────────────────────────────────────────
    if order_type == 'market' and side == 'buy' and spend_percentage is not None:
      # IR does not support createMarketBuyOrderWithCost — always convert
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
      if not ticker:
        self.logger.error("[CREATE ORDER] Could not fetch ticker to convert market buy cost")
        return None

      expected_price = None
      if isinstance(ticker, dict):
        expected_price = ticker.get("ask") or ticker.get("last") or ticker.get("bid")
      else:
        expected_price = (
          getattr(ticker, "ask", None) or
          getattr(ticker, "last", None) or
          getattr(ticker, "bid", None)
        )
      try:
        expected_price = float(expected_price)
      except Exception:
        self.logger.error("[CREATE ORDER] Invalid ticker price for market buy conversion")
        return None

      base_amount = amount_to_trade / expected_price
      try:
        amount_to_trade = self.exchange.amount_to_precision(symbol, base_amount)
      except Exception:
        amount_to_trade = base_amount
      self.logger.info(
        f"[CREATE ORDER] Market buy: ~{amount_to_trade} {base_currency} @ {expected_price} {quote_currency}"
      )
      price = None

    else:
      # Quantity mode, sell, or limit orders: amount_to_trade already in base currency
      try:
        amount_to_trade = self.exchange.amount_to_precision(symbol, amount_to_trade)
      except Exception:
        pass

    if price is not None:
      self.logger.info(f"Placing {order_type} {side} {amount_to_trade} {base_currency} @ {price}")
    else:
      self.logger.info(f"Placing {order_type} {side} {amount_to_trade} {base_currency} (market)")

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

      if side == 'buy':
        if filled and average:
          self.logger.success(
            f"  ✅ Bought {filled:.8f} {base_currency} for {filled * average:.2f} {quote_currency} @ avg {average:.8f}"
          )
        elif filled and cost:
          self.logger.success(f"  ✅ Bought {filled:.8f} {base_currency} for {cost:.2f} {quote_currency}")
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base_currency}")
      else:  # sell
        if filled and average:
          self.logger.success(
            f"  ✅ Sold {filled:.8f} {base_currency} for {filled * average:.2f} {quote_currency} @ avg {average:.8f}"
          )
        elif filled and cost:
          self.logger.success(f"  ✅ Sold {filled:.8f} {base_currency} for {cost:.2f} {quote_currency}")
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base_currency}")
    else:
      self.logger.error("Order call returned no result")

    return order

  async def cancel_order(self,
                         order_id: str,
                         symbol: str = None,
                         params: dict = None
                         ) -> Optional[Dict[str, Any]]:
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
  # Requires a configured bot_configs/crypto/ir.yaml file.

  from src.misc.config_loader import get_bot_configs # pylint: disable=wrong-import-position

  async def test_ir_trader():
    """Test basic IR trader functionality"""
    bots = [b for b in get_bot_configs() if b.get('exchange') == 'ir']
    if not bots:
      print("❌ No IR bot configs found in bot_configs/crypto/ir.yaml")
      return

    config = bots[0]
    print(f"\n{'='*60}")
    print(f"Testing IRTrader with bot: {config['id']}")
    print(f"{'='*60}\n")

    trader = None
    try:
      trader = IRTrader(config)
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

    except Exception as e: # pylint: disable=broad-exception-caught
      print(f"\n❌ Error during testing: {e}")
      traceback.print_exc()

    finally:
      if trader:
        await trader.close()
        print("Connection closed.")

  asyncio.run(test_ir_trader())
