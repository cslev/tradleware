import asyncio
import math
from datetime import datetime

from typing import Optional, Dict, Any, List
from ib_async import IB, Stock, MarketOrder, LimitOrder

from src.traders.stock.base_stock_trader import BaseStockTrader
from src.misc.logger import CustomLogger


class IBKRTrader(BaseStockTrader):
  """
  Interactive Brokers trader implementation using IB Gateway.
  Connects to IB Gateway for stock trading operations.
  Uses ib-async library (actively maintained fork of ib_insync).
  """

  def __init__(self,
               config: dict,
               logger: Optional[CustomLogger] = None):
    """
    Initialize IBKR trader.

    Args:
      config: Bot configuration dict from config_loader.
      logger: Logger instance for logging.
    """
    super().__init__(config, logger)

    # Gateway connection details (from config['gateway'] section in ibkr.yaml)
    gateway = config['gateway']
    self.gateway_host = gateway['host']
    self.gateway_port = int(gateway['port'])

    # Per-bot IBKR credentials
    self.account_id = config['account_id']

    # Fractional share support (off by default — not all symbols support it)
    self.fractional_shares = config.get('fractional_shares', False)

    # --- Contract routing -------------------------------------------------------
    # What the instrument is, as opposed to what the account holds. SMART lets IB pick
    # a venue, which is right for US-listed symbols and ambiguous for anything cross-
    # listed: the same ticker exists on several European exchanges in different
    # currencies, and qualifyContracts either picks one arbitrarily or fails.
    #
    # trading_currency defaults to account_currency because they match in the common
    # case, but they are genuinely separate — a USD account can buy a EUR-denominated
    # instrument, with IB converting or lending. Set it when they differ.
    self.exchange = str(config.get('exchange', 'SMART')).upper()
    self.primary_exchange = str(config.get('primary_exchange', '')).upper()
    self.trading_currency = str(
      config.get('trading_currency', self.account_currency)
    ).upper()

    # IB client
    self.ib = IB()
    self.contract = None  # Will be created on connect
    self.is_connected = False

    # Stores the most recent IB error per reqId so cancelled orders can surface
    # the root-cause message (e.g. "Order presets disallow this buy") rather
    # than just reporting "status=Cancelled".
    self._order_errors: dict = {}

    self.logger.info(
        f"IBKRTrader initialized: {self.symbol} on {self.exchange} "
        f"in {self.trading_currency} via port {self.gateway_port} "
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

      # Register error handler for connection issues.
      # Remove first to prevent duplicate registrations on reconnect.
      try:
        self.ib.errorEvent -= self._on_error
      except Exception:  # pylint: disable=broad-except
        pass
      self.ib.errorEvent += self._on_error

      # Create stock contract. primaryExchange is omitted rather than sent empty —
      # IB treats a blank string as a filter that matches nothing.
      contract_kwargs = (
        {'primaryExchange': self.primary_exchange} if self.primary_exchange else {}
      )
      self.contract = Stock(
        self.symbol, self.exchange, self.trading_currency, **contract_kwargs
      )
      self.logger.debug(
        f"Contract: {self.symbol} on {self.exchange} in {self.trading_currency}"
        + (f" (primary: {self.primary_exchange})" if self.primary_exchange else "")
      )
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

  async def _fetch_cash_balance(self) -> float:
    """
    Return this account's TotalCashValue in `self.account_currency`.

    IB reports one TotalCashValue row per currency an account holds, so a
    multi-currency account returns several and only the configured one is meaningful
    for sizing. Returns 0.0 when that currency is absent — but says so, naming the
    currencies IB did report. Without that line a typo in `account_currency` is
    indistinguishable from an empty account: both size every order against zero.

    Propagates whatever `accountSummaryAsync` raises; callers decide whether a failed
    lookup is fatal (it is when placing a live order, not when refreshing a card).
    """
    account_summary = await self.ib.accountSummaryAsync()
    found_currencies = []
    for item in account_summary:
      if item.account == self.account_id and item.tag == 'TotalCashValue':
        if item.currency == self.account_currency:
          return float(item.value)
        found_currencies.append(item.currency)

    if found_currencies:
      self.logger.warning(
        f"No {self.account_currency} cash reported for account {self.account_id} — "
        f"IB returned {', '.join(sorted(set(found_currencies)))}. "
        f"Set 'account_currency' in the bot config to one of those."
      )
    else:
      self.logger.warning(
        f"IB reported no TotalCashValue at all for account {self.account_id}."
      )
    return 0.0

  async def _fetch_sizing_context(self, side: str, ctx: dict, dry_run: bool = False) -> dict:
    """
    Populate `ctx` with the balance an order needs to be sized: cash to buy, shares to sell.

    `dry_run` decides only what happens when the gateway is unreachable. A dry run falls
    back to simulated values so it still returns something to look at; a live order
    fails instead, because sizing against a number nobody managed to read is how you
    place an order you did not mean.

    Splitting that from "do we need a balance at all?" is deliberate. Those two
    questions used to be braided into one if/elif chain, which meant the same fetch was
    written twice and every change to it had to be made in both copies.
    """
    try:
      if side == 'buy':
        ctx['cash_available'] = await self._fetch_cash_balance()
        self.logger.info(
          f"{'[DRY RUN] ' if dry_run else ''}Cash available: "
          f"{ctx['cash_available']:.2f} {self.account_currency}"
        )
      else:
        position_info = await self.fetch_positions()
        ctx['shares_owned'] = position_info.get('quantity', 0)
        self.logger.info(
          f"{'[DRY RUN] ' if dry_run else ''}Position: "
          f"{ctx['shares_owned']} shares"
        )
      return ctx
    except Exception as exc:
      self._handle_ib_exception(exc, "create_order/sizing")
      if not dry_run:
        raise RuntimeError(f"Failed to fetch balance for sizing: {exc}") from exc
      self.logger.warning(
        f"[DRY RUN] Could not fetch real balance ({exc}); using simulated values"
      )
      if side == 'buy':
        ctx['cash_available'] = 10_000.0
      else:
        ctx['shares_owned'] = 10
      return ctx

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

      # Filter positions for our specific account and symbol
      positions = [p for p in all_positions if p.account == self.account_id]

      # Find position for our symbol
      target_pos = next((p for p in positions if p.contract.symbol == self.symbol), None)
      if target_pos:
        self.logger.debug(f"Position for {self.symbol}: {target_pos}")
        self.logger.info(f"  Position: {target_pos.contract.symbol} - {target_pos.position} shares @ ${target_pos.avgCost}")
      if not target_pos:
        self.logger.warning(f"No position found for {self.symbol}")

        # Still get cash balance even if no position
        cash_balance = 0.0
        try:
          cash_balance = await self._fetch_cash_balance()
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
          self.logger.debug(f"Waiting for P&L data... attempt {attempt + 1}/5")
        else:
          self.logger.warning("P&L data not available after 2.5 seconds, using 0.0")
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
        cash_balance = await self._fetch_cash_balance()
        self.logger.debug(f"Cash balance: {cash_balance:.2f} {self.account_currency}")
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
        # Same routing as the bot's own contract: an ad-hoc lookup on SMART/USD would
        # price a different listing than the one this bot actually trades.
        ad_hoc_kwargs = (
          {'primaryExchange': self.primary_exchange} if self.primary_exchange else {}
        )
        contract = Stock(symbol, self.exchange, self.trading_currency, **ad_hoc_kwargs)
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
                         params: dict = None,
                         *,
                         spend_amount: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """
    Place a buy/sell order for this symbol.

    Args:
      side: 'buy' or 'sell' (validated by webhook handler)
      spend_percentage: Percentage of available funds/shares to use (validated by webhook handler)
      order_execution_strategy: 'market' or 'maker_limit'
      limit_price: Price for limit orders (required if order_execution_strategy is 'maker_limit')
      quantity: Explicit share count to trade. Float when fractional_shares=True, whole number otherwise.
      params: Additional IB-specific parameters
      spend_amount: Exact cash to spend, in the account currency. Buy only. Mutually
                    exclusive with spend_percentage and quantity.

    Returns:
      Order information dict or None on failure
    """
    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 1 — VALIDATE PARAMETERS  (base/IBKR-specific checks)
    # ─────────────────────────────────────────────────────────────────────────
    self.logger.debug("[CREATE ORDER] Starting order creation process...")
    if params is None:
      params = {}
    dry_run_mode = params.get('dry_run', False)

    try:
      self._validate_order_params(
        side=side,
        spend_percentage=spend_percentage,
        order_execution_strategy=order_execution_strategy,
        limit_price=limit_price,
        quantity=quantity,
        spend_amount=spend_amount)
    except ValueError as exc:
      self.logger.error(f"Order parameter validation failed: {exc}")
      raise

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 2 — RESOLVE MARKET STATUS, PRICE, POSITION/CASH (base class: _resolve_market_and_balance)
    # ─────────────────────────────────────────────────────────────────────────

    try:
      ctx = await self._resolve_market_and_balance(side, dry_run=dry_run_mode)
    except RuntimeError as exc:
      self.logger.error(f"{exc}")
      raise

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 3 — CALCULATE ORDER SIZE (base class: _calculate_order_size)
    # ─────────────────────────────────────────────────────────────────────────
    try:
      # The balance is fetched either way. An explicit share count needs nothing
      # calculated from it, but it still has to be checked against it — see
      # _validate_explicit_quantity for why the sell case in particular matters.
      await self._fetch_sizing_context(side, ctx, dry_run=dry_run_mode)
      if quantity is not None:
        self.logger.info(f"Quantity mode: {quantity} shares")
        self._validate_explicit_quantity(side, quantity, ctx)
      else:
        quantity = self._calculate_order_size(side, spend_percentage, ctx,
                                              fractional_shares=self.fractional_shares,
                                              spend_amount=spend_amount)
    except (ValueError, RuntimeError) as exc:
      self.logger.error(f"{exc}")
      raise

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 4 — DRY RUN AND LIVE EXECUTION (IBKR-specific)
    # ─────────────────────────────────────────────────────────────────────────
    try:
      if dry_run_mode:
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

      order.account = self.account_id
      trade = self.ib.placeOrder(self.contract, order)
      req_id = trade.order.orderId

      # Poll for a terminal or confirmed status instead of a blind sleep.
      # IB validates orders asynchronously — rejections can arrive within ms
      # but may take up to a few seconds under load.
      # Terminal success: Filled, Submitted, PreSubmitted
      # Terminal failure: Rejected, Cancelled, Inactive
      #
      # IMPORTANT: error 10349 ("TIF set to DAY based on order preset") causes
      # IB to internally cancel and resubmit the order with the corrected TIF.
      # The status transiently shows Cancelled before filling. We detect this
      # by checking the trade log and skip the Cancelled state in that case.
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
        if order_status not in TERMINAL_OK | TERMINAL_FAIL:
          continue
        # Transient Cancelled due to 10349 (TIF preset adjustment) — keep polling.
        if order_status == 'Cancelled':
          last_error = next(
            (e.errorCode for e in reversed(trade.log) if e.errorCode != 0),
            None
          )
          if last_error == 10349:
            self.logger.debug(
              f"Order {req_id} transiently Cancelled due to 10349 (TIF preset); continuing to poll..."
            )
            order_status = 'Unknown'
            continue
        break

      if order_status in TERMINAL_FAIL:
        hint = (
          f" Note: {self.symbol} may not support fractional shares on IBKR."
          if self.fractional_shares else ""
        )
        reason = self._order_errors.pop(req_id, None)
        reason_str = f" Reason: {reason}" if reason else ""
        raise RuntimeError(
          f"Order rejected by IB Gateway (status={order_status}).{reason_str}{hint}"
        )
      self._order_errors.pop(req_id, None)  # clean up on success

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

    Connection state (critical — affect trading):
      1100: Connectivity between IB and TWS/Gateway lost. Orders will fail until restored.
      1101: Connectivity restored; IB re-subscribes data automatically.
      1102: Connectivity lost and restored in the same session; no action needed.

    Market data farms (informational — do NOT affect order execution or positions):
      2103: Market data farm connection is broken (live price feed interrupted).
      2104: Market data farm connection is OK (live price feed restored).
      2105: Historical data farm is disconnected (historical bars unavailable).
      2106: Historical data farm is connected (historical bars available again).
      2107: HMDS data farm inactive — will reconnect on demand.
      2108: Market data farm connection is inactive — will reconnect on demand.
      2109: Order Event Warning: attempt to cancel order that has already been filled.
      2119: Market data farm is connecting.

    Sec-def data farms (informational — only used at contract qualification time):
      2157: Sec-def data farm connection is broken (security definition lookup interrupted).
      2158: Sec-def data farm connection is OK (security definition lookup restored).
      Once a contract is qualified and cached in self.contract, these have no effect.

    Position derived values (informational — fires when IB cannot compute P&L, e.g. outside market hours):
      2150: Invalid position trade derived value (market price unavailable; does not affect trading).

    Delayed/snapshot data (informational):
      10167: Requested market data is not subscribed; switching to delayed data.

    Order preset overrides (informational — fire on every order):
      10349: Order TIF was set to DAY based on account order preset. IB internally
             cancels and resubmits the order with TIF=DAY; the polling loop detects
             this transient Cancelled state via trade.log and continues polling.

    All codes in the group above share the same handler: debug-level log only, no Gotify alert.
    """
    # Critical connection loss errors that require reconnection
    if errorCode == 1100:
      self.logger.error(f"Connection lost to IB Gateway (Error {errorCode}): {errorString}")
      self.is_connected = False

    # Connection restored (1101: restored after loss; 1102: briefly lost and restored in same session)
    elif errorCode in [1101, 1102]:
      self.logger.success(f"Connection restored to IB Gateway (Error {errorCode}): {errorString}")
      self.is_connected = True

    # Market data farm / connectivity status / order preset overrides (informational — not actionable, no Gotify)
    elif errorCode in [2103, 2104, 2105, 2106, 2107, 2108, 2109, 2119, 2150, 2157, 2158, 10167, 10349]:
      self.logger.debug(f"IB info (Error {errorCode}): {errorString}")

    # Other errors
    else:
      if reqId == -1:
        # System-level error, not tied to a specific request
        self.logger.warning(f"IB System Error {errorCode}: {errorString}")
      else:
        # Store against reqId so create_order can surface the reason if the
        # order is subsequently cancelled.
        self._order_errors[reqId] = f"[{errorCode}] {errorString}"
        self.logger.warning(f"IB Error {errorCode} (reqId {reqId}): {errorString}")

  async def health_check(self) -> bool:
    """
    Probe the IB Gateway connection liveness.

    Uses reqCurrentTimeAsync() as a lightweight round-trip test — it requires
    a live TWS/Gateway socket response, unlike ib.isConnected() which only
    reflects local socket state and can be stale after a silent drop.

    Returns:
      True if the gateway is reachable and responsive, False otherwise.
    """
    if not self.ib.isConnected():
      self.is_connected = False
      return False
    try:
      await asyncio.wait_for(self.ib.reqCurrentTimeAsync(), timeout=5.0)
      self.is_connected = True
      return True
    except Exception:  # pylint: disable=broad-exception-caught
      self.is_connected = False
      return False

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
  from src.misc.config_loader import get_bot_configs # pylint: disable=import-outside-toplevel

  bots = [b for b in get_bot_configs() if b.get('broker') == 'ibkr']
  if not bots:
    print("❌ No IBKR bot configs found in bot_configs/stock/ibkr.yaml")
    return

  config = bots[0]
  print("=" * 60)
  print(f"IBKR Trader Test - {config['id']} / {config['symbol']}")
  print("=" * 60)

  logger = CustomLogger(name="IBKRTraderTest")

  trader = IBKRTrader(config, logger=logger)

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
    logger.success("✓ Position Details:")
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
    import traceback  # pylint: disable=import-outside-toplevel
    traceback.print_exc()

  finally:
    # Cleanup
    print("\nDisconnecting...")
    if trader.is_connected:
      trader.ib.disconnect()
    print("✓ Disconnected")


if __name__ == "__main__":
  asyncio.run(main())
