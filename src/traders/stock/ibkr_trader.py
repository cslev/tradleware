import asyncio
import math
from datetime import datetime

from typing import Optional, Dict, Any, List
from ib_async import IB, Stock, MarketOrder, LimitOrder

from src.traders.stock.base_stock_trader import BaseStockTrader
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env


class IBKRTrader(BaseStockTrader):
  """
  Interactive Brokers trader implementation using IB Gateway.
  Connects to IB Gateway for stock trading operations.
  Uses ib-async library (actively maintained fork of ib_insync).
  """

  def __init__(self,
               account_identifier: str,
               symbol: str,
               extended_hours: bool = False,
               logger: Optional[CustomLogger] = None):
    """
    Initialize IBKR trader.

    Args:
      account_identifier: Unique name for this trading bot (e.g., "MYAPPLEBOT")
      symbol: Stock symbol to trade (e.g., "AAPL", "TSLA")
      extended_hours: Allow pre-market and after-hours trading
      logger: Logger instance for logging
    """
    super().__init__(
      account_identifier=account_identifier,
      broker_id="ibkr",
      symbol=symbol,
      extended_hours=extended_hours,
      logger=logger
    )

    # Load IBKR-specific environment variables
    self.gateway_host = get_env('IBKR_GATEWAY_HOST', '127.0.0.1')
    # With extrange/ibkr image, API is always on port 8888 (auto-forwarded)
    self.gateway_port = int(get_env('IBKR_GATEWAY_PORT', '8888'))
    self.account_id = get_env(f'{account_identifier}_IBKR_ACCOUNT_ID')
    self.tradleware_api_key = get_env(f'{account_identifier}_IBKR_TRADLEWARE_API_KEY')

    # Fractional share support (off by default — not all symbols support it)
    # Set {IDENTIFIER}_IBKR_FRACTIONAL_SHARES=true in .env to enable
    self.fractional_shares = get_env(
        f'{account_identifier}_IBKR_FRACTIONAL_SHARES', 'false'
    ).lower() == 'true'

    # IB client
    self.ib = IB()
    self.contract = None  # Will be created on connect
    self.is_connected = False

    self.logger.info(
        f"IBKRTrader initialized: {symbol} on port {self.gateway_port} "
        f"(fractional_shares={'enabled' if self.fractional_shares else 'disabled'})"
    )

  async def connect(self):
    """
    Connect to IB Gateway and create stock contract.
    Disconnects existing connection first if already connected.
    """
    try:
      # If already connected, disconnect first to avoid duplicate clients in IB Gateway
      if self.is_connected:
        self.logger.warning("Already connected to IB Gateway, disconnecting first...")
        await self.disconnect()
      
      # Also check if ib client has an existing connection and disconnect it
      if self.ib.isConnected():
        self.logger.debug("Found existing ib connection, disconnecting...")
        self.ib.disconnect()

      self.logger.info(f"Connecting to IB Gateway at {self.gateway_host}:{self.gateway_port}...")
      
      # Connect to IB Gateway asynchronously
      await self.ib.connectAsync(
        host=self.gateway_host,
        port=self.gateway_port,
        clientId=hash(self.account_identifier) % 1000  # Unique client ID per bot
      )
      
      # Register error handler for connection issues
      self.ib.errorEvent += self._on_error
      
      # Create stock contract
      self.contract = Stock(self.symbol, 'SMART', 'USD')
      await self.ib.qualifyContractsAsync(self.contract)
      
      self.is_connected = True
      self.logger.success(f"Connected to IB Gateway for {self.symbol}")
      
    except Exception as e:
      self.logger.error(f"Failed to connect to IB Gateway: {e}")
      self.is_connected = False
      raise

  async def disconnect(self):
    """
    Disconnect from IB Gateway and clean up resources.
    """
    try:
      if not self.is_connected:
        self.logger.debug("Already disconnected from IB Gateway")
        return

      self.logger.info(f"Disconnecting from IB Gateway for {self.symbol}...")
      
      # Disconnect from IB Gateway
      self.ib.disconnect()
      
      self.is_connected = False
      self.contract = None
      
      self.logger.success(f"Disconnected from IB Gateway for {self.symbol}")
      
    except Exception as e:
      self.logger.error(f"Error disconnecting from IB Gateway: {e}")
      self.is_connected = False
      raise

  def _sync_connection_state(self) -> bool:
    """
    Syncs is_connected with the actual ib client state.
    Returns the real connection status.
    Should be called at the start of any method that makes IB API calls.
    """
    actual = self.ib.isConnected()
    if self.is_connected and not actual:
      self.logger.warning("IB Gateway connection lost (detected on sync). Updating status to disconnected.")
      self.is_connected = False
    return actual

  def _handle_ib_exception(self, exc: Exception, context: str = "") -> bool:
    """
    Checks if an exception signals a disconnection and updates is_connected.
    Returns True if this was a 'not connected' type error.
    """
    msg = str(exc).lower()
    #matching on common IB connection error messages to update our is_connected flag accordingly
    if 'not connected' in msg or 'disconnected' in msg:
      if self.is_connected:
        label = f" in {context}" if context else ""
        self.logger.warning(f"IB disconnection detected{label}: {exc}")
        self.is_connected = False
      return True
    return False

  async def fetch_positions(self) -> Dict[str, Any]:
    """
    Get position details with unrealized P&L for this symbol.

    Returns:
      Dictionary with position information:
      {
        'symbol': str,
        'quantity': int,
        'unrealized_pnl': float,
        'unrealized_pnl_pct': float
      }
    """
    try:
      # Sync flag with real IB client state before any API calls
      if not self._sync_connection_state():
        try:
          await self.connect()
        except Exception as conn_err:
          self.logger.error(f"Failed to connect to IB Gateway: {conn_err}")
          self.is_connected = False
          raise RuntimeError(f"Cannot fetch positions: IB Gateway connection failed - {conn_err}") from conn_err

      # Get all positions
      all_positions = self.ib.positions()
      # self.logger.debug(f"All positions: {all_positions}")

      # Filter positions for our specific account
      positions = [p for p in all_positions if p.account == self.account_id]
      self.logger.debug(f"Positions for account {self.account_id}: {positions}")  
      # Debug: log positions for this account
      self.logger.debug(f"Total positions for account {self.account_id}: {len(positions)}")
      for p in positions:
        self.logger.debug(f" Position p is: {p}")
        self.logger.info(f"  Position: {p.contract.symbol} - {p.position} shares @ ${p.avgCost}")
      
      # Find position for our symbol
      target_pos = next((p for p in positions if p.contract.symbol == self.symbol), None)
      if not target_pos:
        self.logger.warning(f"No position found for {self.symbol}")
        
        # Still get cash balance even if no position
        cash_balance = 0.0
        try:
          account_summary = await self.ib.accountSummaryAsync()
          for item in account_summary:
            if item.account == self.account_id and item.tag == 'TotalCashValue' and item.currency == 'USD':
              cash_balance = float(item.value)
              break
        except Exception as e:
          self._handle_ib_exception(e, "fetch_positions/accountSummary")
          self.logger.warning(f"Error fetching cash balance: {e}")
        
        return {
          'symbol': self.symbol,
          'quantity': 0,
          'unrealized_pnl': 0.0,
          'unrealized_pnl_pct': 0.0,
          'cash': cash_balance
        }
      
      # Get position details
      quantity = int(target_pos.position)
      avg_cost = float(target_pos.avgCost)
      total_cost = quantity * avg_cost
      
      # Get real-time P&L data
      account = self.ib.wrapper.accounts[0] if self.ib.wrapper.accounts else self.account_id
      conid = target_pos.contract.conId
      
      # Check if there's already a PnL subscription for this position and cancel it
      key = (account, "", conid)
      if hasattr(self.ib, 'wrapper') and hasattr(self.ib.wrapper, 'pnlSingleKey2ReqId'):
        if key in self.ib.wrapper.pnlSingleKey2ReqId:
          self.logger.debug(f"Cancelling existing PnL subscription for {self.symbol}")
          try:
            self.ib.cancelPnLSingle(account, "", conid)
          except Exception as cancel_err:
            self.logger.debug(f"Error cancelling PnL subscription: {cancel_err}")
      
      # Request new PnL subscription
      pnl_stream = self.ib.reqPnLSingle(account, "", conid)
      
      # Wait for P&L data to arrive with retry logic
      unrealized_pnl = 0.0
      try:
        for attempt in range(5):  # Try up to 5 times
          await asyncio.sleep(0.5)  # Wait 0.5 seconds between attempts
          
          if pnl_stream.unrealizedPnL is not None and not math.isnan(pnl_stream.unrealizedPnL):
            unrealized_pnl = float(pnl_stream.unrealizedPnL)
            self.logger.debug(f"Got P&L data on attempt {attempt + 1}: ${unrealized_pnl:.2f}")
            break
          else:
            self.logger.debug(f"Waiting for P&L data... attempt {attempt + 1}/5")
        else:
          self.logger.warning(f"P&L data not available after 2.5 seconds, using 0.0")
      finally:
        # Always cancel the PnL subscription after reading to prevent accumulation
        try:
          self.ib.cancelPnLSingle(account, "", conid)
        except Exception as cancel_err:
          self.logger.debug(f"Error in final PnL cleanup: {cancel_err}")
      
      unrealized_pnl_pct = (unrealized_pnl / abs(total_cost) * 100) if total_cost != 0 else 0.0
      
      # Get account cash balance
      cash_balance = 0.0
      try:
        account_summary = await self.ib.accountSummaryAsync()
        for item in account_summary:
          if item.account == self.account_id and item.tag == 'TotalCashValue' and item.currency == 'USD':
            cash_balance = float(item.value)
            self.logger.debug(f"Cash balance: ${cash_balance:.2f}")
            break
      except Exception as e:
        self._handle_ib_exception(e, "fetch_positions/accountSummary")
        self.logger.warning(f"Error fetching cash balance: {e}")
      
      self.logger.info(f"Position: {quantity} shares, cost: ${total_cost:.2f}, P&L: ${unrealized_pnl:.2f} ({unrealized_pnl_pct:.2f}%), Cash: ${cash_balance:.2f}")
      
      return {
        'symbol': self.symbol,
        'quantity': quantity,
        'unrealized_pnl': unrealized_pnl,
        'unrealized_pnl_pct': unrealized_pnl_pct,
        'cash': cash_balance
      }
     
      
    except RuntimeError as e:
      # Connection errors should propagate to the API layer
      self.logger.error(f"Error fetching positions: {e}")
      raise
    except Exception as e:
      # Unexpected errors - log with traceback but still raise
      self.logger.error(f"Unexpected error fetching positions: {e}", exc_info=True)
      raise RuntimeError(f"Failed to fetch positions: {e}") from e

  async def fetch_account_value(self) -> Dict[str, Any]:
    """
    Not yet implemented. Cash balance is fetched inline inside create_order via
    accountSummaryAsync() — this method is reserved for a future dashboard Summary tab.
    """
    raise NotImplementedError("fetch_account_value() is not yet implemented for IBKRTrader")

  async def get_market_price(self, symbol: Optional[str] = None) -> Optional[float]:
    """
    Get current market price for a symbol.
    First tries delayed market data, falls back to historical close price.

    Args:
      symbol: Stock symbol (e.g., "AAPL")

    Returns:
      Current market price or None if unavailable.
    """
    symbol_str = symbol or self.symbol
    try:
      # Sync flag with real IB client state before any API calls
      if not self._sync_connection_state():
        try:
          await self.connect()
        except Exception as conn_err:
          self.logger.error(f"Failed to connect to IB Gateway: {conn_err}")
          self.is_connected = False
          raise RuntimeError(f"Cannot fetch market price: IB Gateway connection failed - {conn_err}") from conn_err

      # Create contract if it's different from our main symbol
      if symbol and symbol != self.symbol:
        self.logger.info(f"Creating contract for symbol: {symbol}")
        contract = Stock(symbol, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)
      else:
        self.logger.info(f"Using main contract for symbol: {self.symbol}")
        contract = self.contract

      # Try delayed market data first
      self.ib.reqMarketDataType(3)  # Delayed data (free)
      tickers = await self.ib.reqTickersAsync(contract)

      if tickers:
        ticker = tickers[0]
        if ticker.marketPrice() and ticker.marketPrice() > 0:
          self.logger.info(f"Got delayed market price for {contract.symbol}")
          return float(ticker.marketPrice())
        if ticker.last and ticker.last > 0:
          self.logger.info(f"Got last price for {contract.symbol}")
          return float(ticker.last)
        if ticker.close and ticker.close > 0:
          self.logger.info(f"Got close price for {contract.symbol}")
          return float(ticker.close)

      # Fallback: Get recent historical data (always available)
      self.logger.info(f"Falling back to historical data for {contract.symbol}")
      bars = await self.ib.reqHistoricalDataAsync(
        contract,
        endDateTime='',
        durationStr='1 D',
        barSizeSetting='1 day',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=1
      )

      if bars and len(bars) > 0:
        last_bar = bars[-1]
        self.logger.info(f"Got historical close price for {contract.symbol}: ${last_bar.close}")
        return float(last_bar.close)

      self.logger.warning(f"No price data available for {contract.symbol}")
      return None

    except Exception as e:
      self._handle_ib_exception(e, "get_market_price")
      self.logger.error(f"Error fetching market price for {symbol_str}: {e}")
      return None

  async def create_order(self,
                         side: str,
                         spend_percentage: float = None,
                         order_execution_strategy: str = 'market',
                         limit_price: Optional[float] = None,
                         quantity: Optional[float] = None,
                         params: dict = None) -> Optional[Dict[str, Any]]:
    """
    Place a buy/sell order for this symbol.

    Args:
      side: 'buy' or 'sell' (validated by webhook handler)
      spend_percentage: Percentage of available funds/shares to use (validated by webhook handler)
      order_execution_strategy: 'market' or 'maker_limit'
      limit_price: Price for limit orders (required if order_execution_strategy is 'maker_limit')
      quantity: Explicit share count to trade. Float when fractional_shares=True, whole number otherwise.
      params: Additional IB-specific parameters

    Returns:
      Order information dict or None on failure
    """
    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 1 — VALIDATE PARAMETERS  (base/IBKR-specific checks)
    # ─────────────────────────────────────────────────────────────────────────
    self.logger.debug("[CREATE ORDER] Starting order creation process...")
    if params is None:
      params = {}



    try:
      self._validate_order_params(
        side=side,
        spend_percentage=spend_percentage,
        order_execution_strategy=order_execution_strategy,
        limit_price=limit_price,
        quantity=quantity)
    except ValueError as exc:
      self.logger.error(f"Order parameter validation failed: {exc}")
      raise

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 2 — RESOLVE MARKET STATUS, PRICE, POSITION/CASH (base class: _resolve_market_and_balance)
    # ─────────────────────────────────────────────────────────────────────────

    try:
      ctx = await self._resolve_market_and_balance(side, dry_run=params.get('dry_run', False))
    except RuntimeError as exc:
      self.logger.error(f"[LAYER 2] {exc}")
      raise

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 3 — CALCULATE ORDER SIZE (base class: _calculate_order_size)
    # ─────────────────────────────────────────────────────────────────────────
    dry_run_mode = params.get('dry_run', False)
    try:
      if quantity is not None:
        # QUANTITY MODE: caller supplied an explicit share count — no live API needed.
        self.logger.info(f"[LAYER 3] Quantity mode: {quantity} shares (skipping balance fetch)")
      elif dry_run_mode:
        # DRY RUN + PERCENTAGE MODE: try real balance first; fall back to simulated
        # values only if the gateway is unreachable.
        try:
          if side == 'buy':
            account_summary = await self.ib.accountSummaryAsync()
            cash_available = 0.0
            for item in account_summary:
              if item.account == self.account_id and item.tag == 'TotalCashValue' and item.currency == 'USD':
                cash_available = float(item.value)
                break
            ctx['cash_available'] = cash_available
            self.logger.info(f"[LAYER 3][DRY RUN] Using real cash balance: ${cash_available:.2f}")
          else:
            position_info = await self.fetch_positions()
            shares = position_info.get('quantity', 0)
            ctx['shares_owned'] = shares
            self.logger.info(f"[LAYER 3][DRY RUN] Using real position: {shares} shares")
        except Exception as balance_exc:
          # Gateway unavailable — fall back to simulated values so dry_run still works.
          self.logger.warning(f"[LAYER 3][DRY RUN] Could not fetch real balance ({balance_exc}); using simulated values")
          if side == 'buy':
            ctx['cash_available'] = 10_000.0
          else:
            ctx['shares_owned'] = 10
        quantity = self._calculate_order_size(side, spend_percentage, ctx,
                                                fractional_shares=self.fractional_shares)
      else:
        # LIVE PERCENTAGE MODE: fetch real balance from IB Gateway.
        if side == 'buy':
          try:
            account_summary = await self.ib.accountSummaryAsync()
            cash_available = 0.0
            for item in account_summary:
              if item.account == self.account_id and item.tag == 'TotalCashValue' and item.currency == 'USD':
                cash_available = float(item.value)
                break
            ctx['cash_available'] = cash_available
          except Exception as balance_exc:
            self._handle_ib_exception(balance_exc, "create_order/accountSummary")
            raise RuntimeError(f"Failed to fetch cash balance: {balance_exc}") from balance_exc
        else:
          position_info = await self.fetch_positions()
          shares = position_info.get('quantity', 0)
          ctx['shares_owned'] = shares
        quantity = self._calculate_order_size(side, spend_percentage, ctx,
                                              fractional_shares=self.fractional_shares)
    except (ValueError, RuntimeError) as exc:
      self.logger.error(f"[LAYER 3] {exc}")
      raise

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 4 — DRY RUN AND LIVE EXECUTION (IBKR-specific)
    # ─────────────────────────────────────────────────────────────────────────
    try:
      if params.get('dry_run', False):
        # DRY RUN: return simulated order object
        now = datetime.now()
        mock_order = {
          'order_id': f'DRY_RUN_{int(now.timestamp())}',
          'symbol': self.symbol,
          'side': side,
          'quantity': quantity,
          'filled_quantity': 0,
          'price': limit_price if order_execution_strategy == 'maker_limit' else ctx.get('current_price'),
          'order_type': order_execution_strategy,
          'status': 'simulated',
          'timestamp': now,
          'info': {'dry_run': True}
        }
        self.logger.warning(f"🧪 DRY RUN: Simulated order {mock_order}")
        return mock_order

      # LIVE ORDER LOGIC (real execution)
      if order_execution_strategy == 'market':
        order = MarketOrder(side.upper(), quantity)
        self.logger.info(f"Creating market order: {side.upper()} {quantity} shares of {self.symbol}")
      else:
        order = LimitOrder(side.upper(), quantity, limit_price)
        self.logger.info(f"Creating limit order: {side.upper()} {quantity} shares of {self.symbol} @ ${limit_price:.2f}")

      trade = self.ib.placeOrder(self.contract, order)

      # Poll for a terminal or confirmed status instead of a blind sleep.
      # IB validates orders asynchronously — rejections can arrive within ms
      # but may take up to a few seconds under load.
      # Terminal success: Filled, Submitted, PreSubmitted
      # Terminal failure: Rejected, Cancelled, Inactive
      TERMINAL_OK     = {'Filled', 'Submitted', 'PreSubmitted'}
      TERMINAL_FAIL   = {'Rejected', 'Cancelled', 'Inactive'}
      POLL_INTERVAL   = 0.5   # seconds between checks
      POLL_TIMEOUT    = 10.0  # max seconds to wait
      elapsed         = 0.0
      order_status    = 'Unknown'

      while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        order_status = trade.orderStatus.status if trade.orderStatus else 'Unknown'
        if order_status in TERMINAL_OK | TERMINAL_FAIL:
          break

      if order_status in TERMINAL_FAIL:
        hint = (
          f" Note: {self.symbol} may not support fractional shares on IBKR."
          if self.fractional_shares else ""
        )
        raise RuntimeError(
          f"Order rejected by IB Gateway (status={order_status}).{hint}"
        )

      filled_quantity = float(trade.orderStatus.filled) if trade.orderStatus else 0.0
      avg_fill_price = float(trade.orderStatus.avgFillPrice) if trade.orderStatus and trade.orderStatus.avgFillPrice > 0 else 0.0

      self.logger.success(
        f"Order placed successfully: {trade.order.orderId} - "
        f"{side.upper()} {quantity} {self.symbol} - Status: {order_status}"
      )

      return {
        'order_id': str(trade.order.orderId),
        'symbol': self.symbol,
        'side': side,
        'quantity': quantity,
        'filled_quantity': filled_quantity,
        'price': limit_price if order_execution_strategy == 'maker_limit' else avg_fill_price,
        'order_type': order_execution_strategy,
        'status': order_status,
        'timestamp': datetime.now()
      }

    except (ValueError, RuntimeError) as e:
      self.logger.error(f"Order creation failed: {e}")
      raise
    except Exception as e:
      self._handle_ib_exception(e, "create_order/placeOrder")
      self.logger.error(f"Unexpected error creating order: {e}", exc_info=True)
      return None

  async def cancel_order(self, order_id: str) -> bool:
    """
    Not yet implemented. Only relevant once limit orders are supported;
    all current orders are market orders that fill immediately.
    """
    raise NotImplementedError("cancel_order() is not yet implemented for IBKRTrader")

  async def fetch_open_orders(self) -> List[Dict[str, Any]]:
    """
    Not yet implemented. Would provide dashboard visibility into pending orders;
    not required for market-order-only trading.
    """
    raise NotImplementedError("fetch_open_orders() is not yet implemented for IBKRTrader")

  def _on_error(self, reqId, errorCode, errorString, contract):
    """
    Handle IB Gateway error events.
    Error 1100: Connectivity between IBKR and TWS has been lost.
    Error 1101: Connectivity restored.
    Error 1102: Connectivity between IBKR and server has been lost and restored.
    Error 2103: Market data farm connection is inactive but should be available upon demand.
    Error 2104: Market data farm connection is OK.
    Error 2105: A historical data farm is disconnected.
    Error 2106: A historical data farm is connected.
    Error 2158: Sec-def data farm connection is OK.
    """
    # Critical connection loss errors that require reconnection
    if errorCode == 1100:
      self.logger.error(f"Connection lost to IB Gateway (Error {errorCode}): {errorString}")
      self.is_connected = False
      # Note: Auto-reconnection would need to be handled by the application layer
      # to avoid infinite loops. For now, just log and mark as disconnected.
    
    # Connection restored
    elif errorCode == 1101:
      self.logger.success(f"Connection restored to IB Gateway (Error {errorCode}): {errorString}")
      self.is_connected = True
    
    # Connection lost and restored
    elif errorCode == 1102:
      self.logger.warning(f"Connection briefly lost and restored (Error {errorCode}): {errorString}")
    
    # Market data farm status (informational)
    elif errorCode in [2103, 2104, 2105, 2106, 2108, 2158]:
      self.logger.debug(f"Market data status (Error {errorCode}): {errorString}")
    
    # Other errors
    else:
      if reqId == -1:
        # System-level error, not tied to a specific request
        self.logger.warning(f"IB System Error {errorCode}: {errorString}")
      else:
        self.logger.warning(f"IB Error {errorCode} (reqId {reqId}): {errorString}")

  async def close(self):
    """
    Close the IBKR trader and disconnect from gateway.
    Called during application shutdown.
    """
    try:
      self.logger.info(f"Attempting to close IBKR connection for {self.account_identifier} ({self.symbol})...")
      await self.disconnect()
      self.logger.info(f"IBKR connection for {self.account_identifier} ({self.symbol}) closed successfully.")
    except Exception as e:
      self.logger.error(f"Error closing IBKR trader {self.account_identifier}: {e}")


async def main():
  """
  Test function to verify IBKR trader initialization and connection.
  """
  print("=" * 60)
  print("IBKR Trader Test - Init & Connect Only")
  print("=" * 60)
  
  # Create logger instance
  logger = CustomLogger(name="IBKRTraderTest")
  
  # Initialize trader
  trader = IBKRTrader(
    account_identifier="MYPLTRBOT",
    symbol="PLTR",
    extended_hours=False,
    logger=logger
  )
  
  try:
    # Test 1: Connect
    print("\n[TEST 1] Connecting to IB Gateway...")
    await trader.connect()
    print("✓ Connection successful")
    input("\nPress Enter to continue to next test...")
    
    # Test 2: Fetch market price
    print("\n[TEST 2] Fetching market price for symbol set for the bot...")
    price = await trader.get_market_price()
    print(f"✓ Current price: ${price}")
    input("\nPress Enter to continue...")
    # Test 4: Fetch positions with P&L for our symbol
    print(f"\n[TEST 4] Fetching position details for {trader.symbol}...")
    position = await trader.fetch_positions()
    logger.success(f"✓ Position Details:")
    logger.success(f"  Symbol: {position['symbol']}")
    logger.success(f"  Quantity: {position['quantity']} shares")
    logger.success(f"  Unrealized P&L: ${position['unrealized_pnl']:.2f}")
    logger.success(f"  Unrealized P&L %: {position['unrealized_pnl_pct']:.2f}%")
    input("\nPress Enter to continue...")
    
    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)
    
  except Exception as e:
    print(f"\n✗ Error during testing: {e}")
    import traceback
    traceback.print_exc()
  
  finally:
    # Cleanup
    print("\nDisconnecting...")
    if trader.is_connected:
      trader.ib.disconnect()
    print("✓ Disconnected")


if __name__ == "__main__":
  import asyncio
  asyncio.run(main())
