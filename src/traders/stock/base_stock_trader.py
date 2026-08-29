from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, time, timedelta
import math
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

from src.misc.logger import CustomLogger, format_log_buffer
from src.misc.get_env import get_env


class BaseStockTrader(ABC):


  bot_type = "stock"  # Used by GUI to distinguish from crypto bots
  VALID_ORDER_TYPES = ['market', 'maker_limit']
  VALID_ORDER_SIDES = ['buy', 'sell']
  MIN_SPEND_PERCENTAGE = 0.0
  MAX_SPEND_PERCENTAGE = 1.0

  def __init__(self,
               config: dict,
               logger: Optional[CustomLogger] = None):
    """
    Initializes the BaseStockTrader from a bot config dict (from config_loader).

    Args:
      config (dict): Bot configuration dict as returned by config_loader.get_bot_configs().
                     Must contain: id, broker, symbol, extended_hours, tradleware_api_key.
      logger (CustomLogger): The logger instance for printing and logging.
    """
    self.account_identifier = config['id']
    self.broker_id = config['broker'].lower()
    self.symbol = config['symbol'].upper()
    self.extended_hours = config.get('extended_hours', False)

    # Currency of the cash balance orders are sized against, and the unit every cash
    # figure in this class is reported in. Broker-agnostic: any stock account has one.
    # Defaults to USD, which is what every config predating this setting assumed.
    self.account_currency = str(config.get('account_currency', 'USD')).upper()
    self.tradleware_api_key = config['tradleware_api_key']
    self.logger = logger if logger else CustomLogger(
      name=self.__class__.__name__,
      gotify_url=get_env('GOTIFY_SERVER_URL'),
      gotify_token=get_env('GOTIFY_APP_TOKEN'),
      gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30'))
    )

    # Stock-specific attributes
    self.client = None  # Broker client instance (IB, Alpaca, etc.)
    self.positions = {}  # Current positions {symbol: {quantity, avg_cost, ...}}
    self.cash_available = 0.0

    # Market hours — read from config with US Eastern defaults
    tz_str = config.get('market_timezone', 'America/New_York')
    try:
      self.market_timezone = ZoneInfo(tz_str)
    except KeyError:
      self.logger.warning(
        f"Unknown timezone '{tz_str}' for {self.account_identifier}, "
        f"falling back to America/New_York"
      )
      self.market_timezone = ZoneInfo('America/New_York')
    self.regular_open = self._parse_time(config.get('market_open', '09:30'))
    self.regular_close = self._parse_time(config.get('market_close', '16:00'))
    self.pre_market_open = self._parse_time(config.get('pre_market_open', '04:00'))
    self.after_hours_close = self._parse_time(config.get('after_hours_close', '20:00'))

    # Log buffer for UI (keep last 50 messages)
    self.log_buffer = deque(maxlen=50)
    self._add_to_buffer("INFO", f"STOCK trader {self.account_identifier} initialized for {self.symbol}")

    # Setup log buffer wrapping
    self._setup_log_buffer()

    self.logger.info(f"BaseStockTrader initialized for {self.account_identifier} - {self.broker_id} - {self.symbol}")


  @staticmethod
  def _parse_time(value: str) -> time:
    """Parse a 'HH:MM' string into a time object."""
    try:
      h, m = value.split(':')
      return time(int(h), int(m))
    except (ValueError, AttributeError) as exc:
      raise ValueError(f"Invalid time format '{value}'. Expected 'HH:MM' (e.g. '09:30').") from exc

  def _validate_explicit_quantity(self, side: str, quantity: float, ctx: dict) -> None:
    """
    Check that an explicitly requested share count is actually affordable / held.

    Quantity mode has nothing to size, so it used to skip the balance fetch entirely —
    but "nothing to calculate" is not "nothing to check". The crypto traders have always
    validated both directions here; the stock path did not, and sent the order straight
    to the broker.

    The sell case is the one that matters. An oversized sell is not simply refused in a
    margin-enabled account: the excess opens a short position, which is the opposite of
    the intended trade and is not a position Tradleware models anywhere else. Refusing
    it here means short exposure can never arrive by accident. (If deliberate shorting
    is ever wanted, this is the check to relax.)

    Raises:
      ValueError: quantity exceeds available cash (buy) or the position held (sell).
    """
    if side == 'buy':
      cash = ctx.get('cash_available')
      price = ctx.get('current_price')
      if cash is None or price is None:
        raise ValueError("cash_available or current_price missing in context")
      estimated_cost = quantity * price
      if estimated_cost > cash:
        raise ValueError(
          f"Insufficient cash. Need ~{estimated_cost:.2f} {self.account_currency} "
          f"for {quantity} shares @ {price:.2f}, have {cash:.2f} {self.account_currency}."
        )
      self.logger.info(
        f"Quantity check: {quantity} shares @ {price:.2f} "
        f"≈ {estimated_cost:.2f} {self.account_currency} of {cash:.2f} available"
      )
      return

    shares = ctx.get('shares_owned')
    if shares is None:
      raise ValueError("shares_owned missing in context")
    if quantity > shares:
      raise ValueError(
        f"Insufficient position. Asked to sell {quantity} shares, hold {shares}. "
        f"Selling more than is held would open a short position."
      )
    self.logger.info(f"Quantity check: selling {quantity} of {shares} shares held")

  def _calculate_order_size(self,
                            side: str,
                            spend_percentage: float,
                            ctx: dict,
                            fractional_shares: bool = False,
                            *,
                            spend_amount: float = None) -> float:
    """
    Compute share quantity to trade from context and either a percentage or a cash amount.

    Args:
      side: 'buy' or 'sell'
      spend_percentage: Fraction of available cash/shares to use (0.0 < x <= 1.0).
      ctx: Output of _resolve_market_and_balance().
      fractional_shares: When True, returns a float floored to 4 decimal places
                         instead of truncating to a whole integer. The broker must
                         support fractional shares for the symbol — if not, the
                         order will be rejected at execution time.
      spend_amount: Exact cash to spend, in `self.account_currency`. Buy only, and
                    mutually exclusive with spend_percentage. Keyword-only so it cannot
                    be passed positionally into the existing four-argument signature.

    Returns:
      float: Share quantity to trade (whole number as float when fractional_shares=False,
             fractional float when fractional_shares=True).

    Raises:
      ValueError: On insufficient funds, missing context, zero quantity, or a
                  cash-denominated sell.
    """
    if side == 'buy':
      cash = ctx.get('cash_available')
      price = ctx.get('current_price')
      if cash is None or price is None:
        raise ValueError("cash_available or current_price missing in context")
      if cash <= 0:
        raise ValueError(f"No cash available for buying. Cash: {cash:.2f} {self.account_currency}")

      if spend_amount is not None:
        if spend_amount > cash:
          raise ValueError(
            f"Insufficient cash. Asked to spend {spend_amount:.2f} "
            f"{self.account_currency}, have {cash:.2f} {self.account_currency}."
          )
        amount_to_spend = spend_amount
        basis = f"{spend_amount:.2f} requested"
      else:
        amount_to_spend = cash * spend_percentage
        basis = f"{spend_percentage*100:.1f}% of {cash:.2f}"

      raw_quantity = amount_to_spend / price
      # Floor, never round: rounding up puts the order over the cash it was sized
      # against, which for a full-balance order is rejected outright by the broker.
      quantity = math.floor(raw_quantity * 10_000) / 10_000 if fractional_shares \
          else int(raw_quantity)
      if quantity <= 0:
        raise ValueError(
          f"Calculated quantity is 0. Cash: {cash:.2f} {self.account_currency}, "
          f"Price: {price:.2f} {self.account_currency}, "
          f"Spend: {amount_to_spend:.2f} {self.account_currency}"
        )

      spent = quantity * price
      self.logger.info(
        f"Buy order sizing: {amount_to_spend:.2f} {self.account_currency} "
        f"({basis}) → {quantity} shares @ {price:.2f}"
        + (" [fractional]" if fractional_shares else " [whole shares]")
      )
      # Whole shares rarely consume the whole budget, and in cash mode the shortfall
      # does not self-correct: the next order is pinned to the same amount and never
      # looks at the balance, so the remainder accumulates instead of being deployed.
      if spend_amount is not None and amount_to_spend - spent > 0.01:
        self.logger.warning(
          f"Spent {spent:.2f} of {amount_to_spend:.2f} {self.account_currency} "
          f"requested — {amount_to_spend - spent:.2f} not deployed "
          f"({'enable fractional_shares to spend it' if not fractional_shares else 'rounding'})"
        )
      return quantity

    if spend_amount is not None:
      raise ValueError(
        "Cash-denominated sizing is buy-only. To exit a position use "
        "order_size_type 'percentage' (100 closes it) or 'quantity'."
      )
    shares = ctx.get('shares_owned')
    if shares is None:
      raise ValueError("shares_owned missing in context")
    if shares <= 0:
      raise ValueError(f"No shares to sell. Current position: {shares}")
    raw_quantity = shares * spend_percentage
    quantity = round(raw_quantity, 4) if fractional_shares else int(raw_quantity)
    if quantity <= 0:
      raise ValueError(
        f"Calculated sell quantity is 0. Position: {shares}, "
        f"Percentage: {spend_percentage*100:.1f}%"
      )
    self.logger.info(
      f"Sell order sizing: {quantity} shares ({spend_percentage*100:.1f}% of {shares})"
      + (" [fractional]" if fractional_shares else " [whole shares]")
    )
    return quantity

  async def _resolve_market_and_balance(self, side: str, dry_run: bool = False) -> dict:
    """
    Gathers the actual context for a stock trade.
    Returns a dict with keys:
     - can_trade (bool)
     - market_status (str)
     - cash_available (float, for buy)
     - shares_owned (int, for sell)
     - current_price (float)
     - account_summary (dict)  # broker-specific, optional
     - position_info (dict)    # broker-specific, optional
    Raises RuntimeError if trading not allowed for any reason (unless dry_run is True).
    """
    # Ensure trading is allowed right now (check market hours and extended hours support)
    if not dry_run:
      if not self.can_trade_now():
        market_status = self.get_market_status()
        time_until_open = self.get_time_until_market_opens()
        msg = (
          f"Market is {market_status}. "
          f"{'Extended hours trading is disabled.' if market_status in ['pre-market', 'after-hours'] else f'Market opens in {time_until_open}.'}"
        )
        self.logger.error(msg)
        raise RuntimeError(msg)
    else:
      self.logger.warning("[DRY RUN] Skipping market hours check; simulating as if market is open.")

    # Gather primary context
    ctx = {
      'can_trade': True,
      'market_status': self.get_market_status(),
      'cash_available': None,
      'shares_owned': None,
      'current_price': None,
      'account_summary': None,
      'position_info': None
    }

    # Fetch market price via required method
    ctx['current_price'] = await self.get_market_price(self.symbol)
    if not ctx['current_price']:
      self.logger.error(f"Could not get current price for {self.symbol}")
      raise RuntimeError(f"No current price available for {self.symbol}")

    # Get cash or shares as appropriate, using impl methods
    if side == 'buy':
      # Must be overridden to actually fetch cash in subclass
      # Default 0 to force subclass to fill in
      ctx['cash_available'] = 0
    else:  # sell
      ctx['shares_owned'] = 0
    return ctx

  def _validate_order_params(self,
                            side: str,
                            spend_percentage: float = None,
                            order_execution_strategy: str = 'market',
                            limit_price: Optional[float] = None,
                            quantity: Optional[float] = None,
                            *,
                            spend_amount: Optional[float] = None):
    """
    Validates the standard order parameters for all stock traders.
    Exactly one of spend_percentage or quantity must be provided.
    Raises ValueError with a helpful log message if input is invalid.
    """
    self.logger.debug(f"[L1] Validating order params: side={side}, spend_percentage={spend_percentage}, quantity={quantity}, order_execution_strategy={order_execution_strategy}, limit_price={limit_price}")
    # --- side ---
    if side not in self.VALID_ORDER_SIDES:
      self.logger.error(f"Invalid 'side' argument: {side}")
      raise ValueError(f"'side' must be one of {self.VALID_ORDER_SIDES}")
    # --- exactly one sizing mode ---
    # Counted rather than compared pairwise: a chain of "A and B", "A and C", "B and C"
    # grows quadratically and is the kind of thing a fourth mode would silently break.
    modes = {'spend_percentage': spend_percentage,
             'quantity': quantity,
             'spend_amount': spend_amount}
    supplied = [name for name, value in modes.items() if value is not None]
    if len(supplied) > 1:
      raise ValueError(f"Cannot specify more than one of {', '.join(sorted(supplied))}. Choose one.")
    if not supplied:
      raise ValueError(f"Must specify exactly one of {', '.join(sorted(modes))}.")
    # --- spend_percentage range ---
    if spend_percentage is not None:
      if spend_percentage <= self.MIN_SPEND_PERCENTAGE or spend_percentage > self.MAX_SPEND_PERCENTAGE:
        self.logger.error(f"Invalid 'spend_percentage': {spend_percentage}")
        raise ValueError(f"'spend_percentage' must be in ({self.MIN_SPEND_PERCENTAGE}, {self.MAX_SPEND_PERCENTAGE}]")
    # --- spend_amount positivity ---
    if spend_amount is not None:
      if spend_amount <= 0:
        self.logger.error(f"Invalid 'spend_amount': {spend_amount}")
        raise ValueError(f"'spend_amount' must be positive, got: {spend_amount}")
    # --- quantity positivity ---
    if quantity is not None:
      if quantity <= 0:
        self.logger.error(f"Invalid 'quantity': {quantity}")
        raise ValueError(f"'quantity' must be a positive integer, got: {quantity}")
    # --- execution strategy ---
    if order_execution_strategy not in self.VALID_ORDER_TYPES:
      self.logger.error(f"Invalid execution strategy: {order_execution_strategy}")
      raise ValueError(f"Invalid 'order_execution_strategy', must be one of {self.VALID_ORDER_TYPES}")
    if order_execution_strategy == "maker_limit" and not limit_price:
      self.logger.error("limit_price is required for 'maker_limit' orders")
      raise ValueError("limit_price is required for 'maker_limit' orders")
    self.logger.info(f"[LAYER 1] Param validation successful: side={side}, spend_percentage={spend_percentage}, quantity={quantity}, strategy={order_execution_strategy}, limit_price={limit_price}")

  def _setup_log_buffer(self):
    """Setup logging to also write to the trader's log buffer"""
    original_info = self.logger.info
    original_error = self.logger.error
    original_warning = self.logger.warning
    original_success = self.logger.success
    original_critical = self.logger.critical

    def info_with_buffer(msg, *args, **kwargs):
      display = msg % args if args else str(msg)
      self._add_to_buffer("INFO", display)
      return original_info(display, **kwargs)

    def error_with_buffer(msg, *args, **kwargs):
      display = msg % args if args else str(msg)
      self._add_to_buffer("ERROR", display)
      return original_error(display, **kwargs)

    def warning_with_buffer(msg, *args, **kwargs):
      display = msg % args if args else str(msg)
      self._add_to_buffer("WARNING", display)
      return original_warning(display, **kwargs)

    def success_with_buffer(msg, *args, **kwargs):
      display = msg % args if args else str(msg)
      self._add_to_buffer("SUCCESS", display)
      return original_success(display, **kwargs)

    def critical_with_buffer(msg, *args, **kwargs):
      display = msg % args if args else str(msg)
      self._add_to_buffer("CRITICAL", display)
      return original_critical(display, **kwargs)

    self.logger.info = info_with_buffer
    self.logger.error = error_with_buffer
    self.logger.warning = warning_with_buffer
    self.logger.success = success_with_buffer
    self.logger.critical = critical_with_buffer

  def _add_to_buffer(self, level: str, message: str):
    """
    Add a log message to this trader's buffer.

    The day is stored alongside the clock time rather than rendered into the line, so
    `get_recent_logs()` can group entries under one date header instead of repeating
    the date on every row.
    """
    now = datetime.now()
    self.log_buffer.append(
      (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), level, message)
    )

  def get_recent_logs(self) -> list:
    """
    Render the buffer for the dashboard, inserting a header each time the day changes.

    Headers are built here rather than stored, so the buffer holds 50 real entries
    rather than spending slots on separators — and the oldest surviving line always
    keeps a date above it even after older ones have been evicted.
    """
    return format_log_buffer(self.log_buffer)

  def is_market_open(self) -> bool:
    """
    Check if market is currently open for trading.

    Returns:
      True if market is open (regular hours), False otherwise.
    """
    now = datetime.now(self.market_timezone).time()
    return self.regular_open <= now < self.regular_close

  def can_trade_now(self) -> bool:
    """
    Check if trading is allowed right now based on market hours and extended_hours setting.

    Returns:
      True if trading is allowed, False otherwise.
    """
    status = self.get_market_status()

    if status == 'closed':
      return False  # Never trade when fully closed

    if status == 'open':
      return True  # Always OK during regular hours

    # Pre-market or after-hours - depends on extended_hours setting
    return self.extended_hours

  def get_market_status(self) -> str:
    """
    Get current market status.

    Returns:
      'open', 'closed', 'pre-market', or 'after-hours'
    """
    now_dt = datetime.now(self.market_timezone)
    now = now_dt.time()

    # Check if it's a weekend (Saturday=5, Sunday=6)
    if now_dt.weekday() >= 5:
      return 'closed'

    if self.regular_open <= now < self.regular_close:
      return 'open'
    if self.pre_market_open <= now < self.regular_open:
      return 'pre-market'
    if self.regular_close <= now < self.after_hours_close:
      return 'after-hours'
    return 'closed'

  def get_time_until_market_opens(self) -> Optional[str]:
    """
    Get human-readable time until market opens.

    Returns:
      String like "2h 34m" or None if market is open.
    """
    status = self.get_market_status()
    if status == 'open':
      return None

    now = datetime.now(self.market_timezone)
    today = now.date()

    if status == 'pre-market':
      # Market opens at regular_open today
      market_open = datetime.combine(today, self.regular_open, self.market_timezone)
    else:
      # Market closed - find next weekday opening
      next_day = today + timedelta(days=1)

      # Skip to Monday if next_day is Saturday or Sunday
      while next_day.weekday() >= 5:  # 5=Saturday, 6=Sunday
        next_day += timedelta(days=1)

      market_open = datetime.combine(next_day, self.regular_open, self.market_timezone)

    time_diff = market_open - now
    hours = int(time_diff.total_seconds() // 3600)
    minutes = int((time_diff.total_seconds() % 3600) // 60)

    if hours > 0:
      return f"{hours}h {minutes}m"
    return f"{minutes}m"

  @abstractmethod
  async def connect(self):
    """
    Establish connection to the broker.
    Must be implemented by concrete broker classes.
    """
    pass

  @abstractmethod
  async def disconnect(self):
    """
    Close connection to the broker.
    Must be implemented by concrete broker classes.
    """
    pass

  @abstractmethod
  async def fetch_positions(self) -> Dict[str, Any]:
    """
    Get current positions (shares held).

    Returns:
      Dictionary with position information for this symbol:
      {
        'symbol': str,
        'quantity': int,
        'avg_cost': float,
        'market_value': float,
        'unrealized_pnl': float,
        'unrealized_pnl_pct': float
      }
    """
    pass

  @abstractmethod
  async def fetch_account_value(self) -> Dict[str, Any]:
    """
    Get account cash and buying power.

    Returns:
      Dictionary with account value information:
      {
        'cash': float,
        'buying_power': float,
        'total_value': float
      }
    """
    pass

  @abstractmethod
  async def get_market_price(self, symbol: str) -> Optional[float]:
    """
    Get current market price for a symbol.

    Args:
      symbol: Stock symbol (e.g., "AAPL")

    Returns:
      Current market price or None if unavailable.
    """
    pass

  @abstractmethod
  async def create_order(self,
                         side: str,
                         spend_percentage: float = None,
                         order_execution_strategy: str = 'market',
                         limit_price: Optional[float] = None,
                         quantity: Optional[float] = None,
                         params: dict = None) -> Optional[Dict[str, Any]]:
    """
    Place a buy/sell order.

    Args:
      side: 'buy' or 'sell'
      spend_percentage: Percentage of available funds/shares to use (0.0 to 1.0). Default is 1.0 (100%).
      order_execution_strategy: 'market' for immediate execution, 'maker_limit' for limit order
      limit_price: Price for limit orders (required if order_execution_strategy is 'maker_limit')
      params: Additional broker-specific parameters

    Returns:
      Order information dict or None on failure:
      {
        'order_id': str,
        'symbol': str,
        'side': str,
        'quantity': int,
        'price': float,
        'status': str,
        'timestamp': datetime
      }
    """
    pass

  @abstractmethod
  async def cancel_order(self, order_id: str) -> bool:
    """
    Cancel an order by its ID.

    Args:
      order_id: Order identifier from the broker.

    Returns:
      True if cancelled successfully, False otherwise.
    """
    pass

  @abstractmethod
  async def fetch_open_orders(self) -> List[Dict[str, Any]]:
    """
    Fetch all open orders for this symbol.

    Returns:
      List of open order dictionaries.
    """
    pass

  async def close(self):
    """
    Cleanup method to close broker connection.
    Should be called when the trader is no longer needed.
    """
    try:
      await self.disconnect()
      self.logger.info(f"Stock trader {self.account_identifier} closed successfully.")
    except Exception as e:
      self.logger.error(f"Error closing stock trader {self.account_identifier}: {e}")
