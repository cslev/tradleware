import asyncio

from datetime import datetime
from typing import Dict, Any, Optional, List
import traceback

import ccxt
from ccxt import async_support as ccxt_async

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env


class CoinbaseTrader(BaseCryptoTrader):
  """
  Concrete trader class for the Coinbase Advanced Trade exchange (CDP API).

  Authentication is handled automatically by CCXT: when the apiKey contains
  'organizations/' or the secret starts with '-----BEGIN', CCXT generates
  the required JWT token internally using ES256 — no manual JWT needed.
  """

  def __init__(self, config: dict, default_type: str = 'spot'):
    """
    Initializes the CoinbaseTrader for the Coinbase Advanced Trade exchange.

    Args:
      config (dict): Bot configuration dict from config_loader.
      default_type (str): The default market type (e.g., 'spot').
    """
    self.logger = CustomLogger(
      name=self.__class__.__name__,
      gotify_url=get_env('GOTIFY_SERVER_URL'),
      gotify_token=get_env('GOTIFY_APP_TOKEN'),
      gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30'))
    )

    super().__init__(config, default_type, self.logger)

    # Initialize the CCXT async Coinbase exchange client.
    # CCXT detects CDP keys automatically from the apiKey/secret format and
    # handles all JWT signing internally — no extra options required.
    self.exchange = ccxt_async.coinbase({
      'apiKey': self.api_key,
      'secret': self.secret_key,
      'hostname': self.hostname if self.hostname else 'api.coinbase.com',
      'options': {
        'defaultType': self.default_type,
      },
      'enableRateLimit': True,
    })

    self.logger.success(f"CoinbaseTrader initialized for {self.account_identifier}")

  def _get_maker_buy_price(self, symbol: str, ticker: dict) -> float:
    """
    Override: Coinbase enforces limit-only mode on some pairs (e.g. USDC/SGD).
    For fiat→stablecoin conversion we want the order to fill promptly, so we
    price at ask (current offer) instead of below bid. This still submits a
    limit order (satisfying the exchange requirement) but fills immediately.
    """
    ask = ticker.get('ask')
    if not ask or float(ask) <= 0:
      raise ValueError(f"Invalid ask price in ticker for {symbol}: {ask}")
    return float(self.exchange.price_to_precision(symbol, float(ask)))

  async def fetch_balance(self) -> Dict[str, Any]:
    """
    Fetches and logs the balance for the Coinbase account.
    """
    self.logger.info(f"Fetching balance for Coinbase account: {self.account_identifier}...")

    balance = await self._safe_api_call(self.exchange.fetch_balance)
    if balance:
      self.logger.info(f"Balance for {self.account_identifier}:")
      found_assets = False
      total_balances = balance.get('total') or {}
      for currency, data in total_balances.items():
        try:
          if data and float(data) > 0:
            self.logger.info(f"  {currency}: {data}")
            found_assets = True
        except Exception:  # pylint: disable=broad-except
          continue
      if not found_assets:
        self.logger.warning("❌ No assets found in this account.")
    return balance

  async def fetch_open_orders(
    self,
    symbol: str = None,
    since: int = None,
    limit: int = None,
    params: dict = None
  ) -> Optional[List[Dict[str, Any]]]:
    """
    Fetches all open orders for the Coinbase account.

    Args:
      symbol (str): Trading pair to filter by (e.g. 'BTC/USDT'). None fetches all.
      since (int): Filter orders created after this timestamp (milliseconds).
      limit (int): Maximum number of orders to return.
      params (dict): Additional exchange-specific parameters.

    Returns:
      list: Open orders for the given symbol (or all symbols).
    """
    if params is None:
      params = {}
    self.logger.info(
      f"Fetching open orders for {symbol} on Coinbase account: {self.account_identifier}..."
    )
    orders = await self._safe_api_call(
      self.exchange.fetch_open_orders, symbol, since, limit, params
    )
    return orders

  async def list_fiat_markets(self, fiat_currency: str = "USD"):
    """
    Fetches and lists all markets on Coinbase that involve fiat_currency.
    Useful for identifying available trading pairs when fiat is not directly tradeable.

    Args:
      fiat_currency (str): The fiat currency to search for (e.g. 'USD', 'EUR').
    """
    self.logger.info(
      f"Loading all markets for {self.exchange_id} to find {fiat_currency} pairs..."
    )
    fiat_markets = []
    try:
      await self._safe_api_call(self.exchange.load_markets, True)

      self.logger.info(f"Markets loaded. Filtering for {fiat_currency} related pairs...")
      for symbol, market in self.exchange.markets.items():
        if (
          fiat_currency in symbol.upper()
          or market['base'] == fiat_currency
          or market['quote'] == fiat_currency
        ):
          fiat_markets.append(market)

      if fiat_markets:
        self.logger.success(
          f"Found {len(fiat_markets)} {fiat_currency} related markets on {self.exchange_id}:"
        )
        for market in fiat_markets:
          self.logger.info(
            f"  Symbol: {market['symbol']}, "
            f"Type: {market['type']}, "
            f"Base: {market['base']}, "
            f"Quote: {market['quote']}, "
            f"Active: {market['active']}"
          )
      else:
        self.logger.warning(
          f"No direct {fiat_currency} trading pairs found on {self.exchange_id} "
          f"via CCXT for the '{self.default_type}' type."
        )
        self.logger.info(
          "You may need to convert fiat to a stablecoin (e.g., USDT) first, "
          "then trade via crypto/stablecoin pairs (e.g., BTC/USDT)."
        )
    except Exception as exc:  # pylint: disable=broad-except
      self.logger.error(f"❌ Error listing {fiat_currency} markets for {self.exchange_id}: {exc}")
    return fiat_markets

  async def convert_fiat_to_stablecoin(
    self,
    spend_percentage: float = 1.0,
    order_execution_strategy: str = 'maker_limit',
    max_slippage: float = 0.05
  ):
    """
    Converts a percentage of available fiat currency into a stablecoin.

    Args:
      spend_percentage (float): Fraction of available fiat funds to spend (0.0 to 1.0).
      order_execution_strategy (str): 'market' or 'maker_limit'.
      max_slippage (float): Maximum allowed slippage for market orders (0.0 to 1.0).
    """
    self.logger.info(
      f"Attempting to convert {spend_percentage*100}% of {self.fiat_currency} "
      f"to {self.stablecoin_currency} via {self.stablecoin_fiat_pair}..."
    )
    if not 0.0 < spend_percentage <= 1.0:
      self.logger.error("❌ spend_percentage must be between 0.0 (exclusive) and 1.0 (inclusive).")
      return 0.0

    expected_price = None
    if order_execution_strategy == 'market':
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, self.stablecoin_fiat_pair)
      if not ticker:
        self.logger.error("Could not fetch ticker for slippage calculation.")
        return 0.0
      expected_price = ticker['ask']
      self.logger.info(f"Current market price: {expected_price}")

    stablecoin_order = await self.create_order(
      symbol=self.stablecoin_fiat_pair,
      side='buy',
      spend_percentage=spend_percentage,
      order_execution_strategy=order_execution_strategy
    )

    if not stablecoin_order:
      self.logger.error(
        f"❌ Order to buy {self.stablecoin_currency} failed or returned no order object."
      )
      return 0.0

    order_id = stablecoin_order.get('id')
    if order_id and order_execution_strategy == 'market':
      await asyncio.sleep(1)
      try:
        updated_order = await self._safe_api_call(
          self.exchange.fetch_order, order_id, self.stablecoin_fiat_pair
        )
        if updated_order:
          stablecoin_order = updated_order
          self.logger.info(
            f"Updated order status: {stablecoin_order.get('status')}, "
            f"filled: {stablecoin_order.get('filled', 0)}"
          )
      except Exception as exc:  # pylint: disable=broad-except
        self.logger.warning(f"Could not fetch updated order details: {exc}")

    order_status = stablecoin_order.get('status')
    if order_execution_strategy == 'maker_limit':
      # Limit order placed — fetch it after a brief wait to get populated fields,
      # since Coinbase's immediate create_order response often returns None for amount/cost.
      order_id_placed = stablecoin_order.get('id')
      if order_id_placed:
        await asyncio.sleep(1)
        try:
          updated = await self._safe_api_call(
            self.exchange.fetch_order, order_id_placed, self.stablecoin_fiat_pair
          )
          if updated:
            stablecoin_order = updated
            self.logger.info(
              f"Limit order status: {stablecoin_order.get('status')}, "
              f"amount: {stablecoin_order.get('amount')}"
            )
        except Exception as exc:  # pylint: disable=broad-except
          self.logger.warning(f"Could not fetch limit order details: {exc}")

        expected_amount = stablecoin_order.get('amount') or stablecoin_order.get('remaining') or 0
        expected_cost = stablecoin_order.get('cost') or 0
        self.logger.success(
          f"✅ Limit order placed (ID: {order_id_placed}). "
          f"Expecting ~{expected_amount} {self.stablecoin_currency} "
          f"for ~{expected_cost} {self.fiat_currency} when filled."
        )
        return float(expected_amount) if expected_amount else 0.0
      self.logger.error("❌ Limit order returned no ID — order may not have been placed.")
      return 0.0

    if order_status not in ['closed', 'filled']:
      self.logger.warning(
        f"⚠️ Market order to buy {self.stablecoin_currency} was not immediately executed. "
        f"Current status: {order_status}"
      )
      filled_amount = stablecoin_order.get('filled', 0)
      if filled_amount > 0:
        self.logger.info(f"✅ Order partially/fully filled: {filled_amount} {self.stablecoin_currency}")
      else:
        return 0.0

    if order_execution_strategy == 'market' and stablecoin_order:
      actual_price = stablecoin_order.get('average') or stablecoin_order.get('price', 0)
      if actual_price and expected_price:
        slippage = abs(actual_price - expected_price) / expected_price
        if slippage > max_slippage:
          self.logger.warning(f"⚠️ High slippage detected: {slippage:.2%} (limit: {max_slippage:.2%})")
        else:
          self.logger.info(f"✅ Slippage within limits: {slippage:.2%}")

    filled_amount = stablecoin_order.get('filled', 0) or 0
    actual_cost = stablecoin_order.get('cost') or 0
    self.logger.success(
      f"✅ Successfully converted {actual_cost} {self.fiat_currency} "
      f"to {filled_amount} {self.stablecoin_currency}!"
    )
    return filled_amount



  async def create_order(
    self,
    symbol: str,
    side: str,
    spend_percentage: float = None,
    quantity: float = None,
    order_execution_strategy: str = 'market',
    dry_run: bool = False,
    params: dict = None
  ):
    """
    Creates an order on Coinbase with flexible execution and amount.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT').
      side (str): The order side ('buy' or 'sell').
      spend_percentage (float): Fraction of available funds to spend (0.0–1.0).
                                Mutually exclusive with quantity.
      quantity (float): Exact base currency amount to buy/sell.
                        Mutually exclusive with spend_percentage.
      order_execution_strategy (str): 'market' or 'maker_limit'.
      dry_run (bool): If True, simulate the order without executing it.
      params (dict): Additional exchange-specific parameters.
    """
    self.logger.debug("[CREATE ORDER] starting order creation process...")
    if params is None:
      params = {}

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 1 — VALIDATE PARAMETERS
    # ─────────────────────────────────────────────────────────────────────────
    try:
      self._validate_order_params(
        symbol, side, spend_percentage, quantity,
        order_execution_strategy=order_execution_strategy,
        dry_run=dry_run
      )
      self.logger.info("[CREATE ORDER] Order parameters validated successfully.")
    except ValueError as exc:
      self.logger.error(f"Order validation failed: {exc}")
      return None

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 2 — RESOLVE MARKET DATA & BALANCE
    # ─────────────────────────────────────────────────────────────────────────
    try:
      ctx = await self._resolve_market_and_balance(symbol)
    except RuntimeError as exc:
      self.logger.error(f"[CREATE ORDER] {exc}")
      return None

    base_currency = ctx['base']
    quote_currency = ctx['quote']

    self.logger.debug(
      f"[CREATE ORDER] quantity={quantity}, spend_percentage={spend_percentage}"
    )

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 3 — CALCULATE ORDER SIZE
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
        self.logger.info(
          f"  {side.upper()} ~{sim_amount:.8f} {base_currency} "
          f"with {amount_to_trade:.2f} {quote_currency} (MARKET)"
        )
        return mock_order

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
      self.logger.info(
        f"  {side.upper()} {amount_to_trade_precise} {base_currency}" +
        (f" @ {price} {quote_currency}" if price else " (MARKET)")
      )
      return mock_order

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 4 — EXECUTE ORDER  (Coinbase-specific)
    # For spend% market buys, try createMarketBuyOrderWithCost first (passes
    # the exact quote cost to Coinbase), then fall back to a ticker-based
    # base-amount conversion if unsupported.
    # All other cases (quantity mode, sells, limit orders) use the standard
    # CCXT create_order call.
    # ─────────────────────────────────────────────────────────────────────────
    if order_type == 'market' and side == 'buy' and spend_percentage is not None:
      try:
        if hasattr(self.exchange, 'createMarketBuyOrderWithCost'):
          self.logger.info(
            f"Using createMarketBuyOrderWithCost to spend exact "
            f"{amount_to_trade} {quote_currency}"
          )
          order = await self._safe_api_call(
            self.exchange.createMarketBuyOrderWithCost, symbol, amount_to_trade, params
          )
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
            self.logger.success(
              f"  Status: {status}, Filled: {filled_str} {base_currency}"
            )
          else:
            self.logger.error(f"❌ Failed to place order for {symbol}.")
          return order
      except (AttributeError, ccxt.NotSupported, KeyError) as exc:
        self.logger.warning(
          f"createMarketBuyOrderWithCost not supported or failed ({exc}), "
          "falling back to ticker-based base amount conversion"
        )

      # Fallback: convert quote cost → base amount via current ticker price
      ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
      if ticker is None:
        self.logger.error(
          f"Could not fetch market price for {symbol} to calculate buy amount."
        )
        return None

      expected_price = (
        ticker.get('ask') or ticker.get('last') or ticker.get('bid')
        if isinstance(ticker, dict)
        else None
      )
      if expected_price is None:
        self.logger.error(f"Could not determine price from ticker for {symbol}.")
        return None

      # Apply a small buffer to account for fees / rounding so the order
      # doesn't fail due to insufficient quote balance after fee deduction.
      adjusted_cost = amount_to_trade * 0.995
      amount_to_trade = self._safe_amount_to_precision(
        symbol, adjusted_cost / expected_price
      )
      price = None
      self.logger.info(
        f"Fallback: buying ~{amount_to_trade} {base_currency} "
        f"@ {expected_price} (spending ~{adjusted_cost:.2f} {quote_currency})"
      )

    # Apply precision for all non-spend%-market-buy paths
    if not (order_type == 'market' and side == 'buy' and spend_percentage is not None):
      amount_to_trade = self._safe_amount_to_precision(symbol, amount_to_trade)

    if price is not None:
      self.logger.info(
        f"Placing order: Symbol={symbol}, Type={order_type}, "
        f"Side={side}, Amount={amount_to_trade}, Price={price}"
      )
    else:
      self.logger.info(
        f"Placing order: Symbol={symbol}, Type={order_type}, "
        f"Side={side}, Amount={amount_to_trade} (Market Order)"
      )

    order = await self._safe_api_call(
      self.exchange.create_order, symbol, order_type, side, amount_to_trade, price, params
    )

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
      if side == 'buy':
        if filled and average:
          self.logger.success(
            f"  ✅ Bought {filled:.8f} {base_currency} "
            f"for {filled * average:.2f} {quote_currency} @ avg price {average:.8f}"
          )
        elif filled and cost:
          self.logger.success(
            f"  ✅ Bought {filled:.8f} {base_currency} for {cost:.2f} {quote_currency}"
          )
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base_currency}")
      else:
        if filled and average:
          self.logger.success(
            f"  ✅ Sold {filled:.8f} {base_currency} "
            f"for {filled * average:.2f} {quote_currency} @ avg price {average:.8f}"
          )
        elif filled and cost:
          self.logger.success(
            f"  ✅ Sold {filled:.8f} {base_currency} for {cost:.2f} {quote_currency}"
          )
        else:
          self.logger.success(f"  Status: {status}, Filled: {filled} {base_currency}")
    else:
      self.logger.error(
        f"❌ Failed to place order for {symbol}. "
        "The exchange API call returned None (check logs above for details)."
      )
    return order

  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = None):
    """
    Cancels an order by its ID on the Coinbase account.

    Args:
      order_id (str): The ID of the order to cancel.
      symbol (str): The trading pair symbol associated with the order.
      params (dict): Additional exchange-specific parameters.
    """
    if params is None:
      params = {}
    self.logger.info(
      f"Attempting to cancel order ID: {order_id} for {symbol} "
      f"on Coinbase account: {self.account_identifier}..."
    )
    cancel_result = await self._safe_api_call(
      self.exchange.cancel_order, order_id, symbol, params
    )
    if cancel_result:
      self.logger.success(
        f"Order {order_id} cancelled successfully! Status: {cancel_result.get('status')}"
      )
    return cancel_result


if __name__ == "__main__":
  # Test script for CoinbaseTrader — run directly to validate basic functionality.
  # Requires a configured bot_configs/crypto/coinbase.yaml file.

  from src.misc.config_loader import get_bot_configs  # pylint: disable=wrong-import-position

  async def test_coinbase_trader():
    """Test basic CoinbaseTrader functionality."""
    bots = [b for b in get_bot_configs() if b.get('exchange') == 'coinbase']
    if not bots:
      print("❌ No Coinbase bot configs found in bot_configs/crypto/coinbase.yaml")
      return

    config = bots[0]
    print(f"\n{'='*60}")
    print(f"Testing CoinbaseTrader with bot: {config['id']}")
    print(f"{'='*60}\n")

    trader = None
    try:
      trader = CoinbaseTrader(config)
      await trader.post_init()

      print("\n--- Test 1: Fetching Balance ---")
      await trader.fetch_balance()

      print("\n--- Test 2: Trading Pair Validity ---")
      print(f"Configured pair: {trader.crypto_stablecoin_pair}")
      print(f"Pair valid: {trader.trading_pair_valid}")

      print("\n--- Test 3: Fetching Open Orders ---")
      orders = await trader.fetch_open_orders(trader.crypto_stablecoin_pair)
      if orders:
        print(f"Found {len(orders)} open orders")
        for order in orders[:3]:
          print(
            f"  Order {order.get('id')}: "
            f"{order.get('side')} {order.get('amount')} @ {order.get('price')}"
          )
      else:
        print("No open orders found")

      print(f"\n{'='*60}")
      print("✅ All tests completed successfully!")
      print(f"{'='*60}\n")

    except Exception as exc:  # pylint: disable=broad-exception-caught
      print(f"\n❌ Error during testing: {exc}")
      traceback.print_exc()

    finally:
      if trader:
        await trader.close()
        print("Connection closed.")

  asyncio.run(test_coinbase_trader())
