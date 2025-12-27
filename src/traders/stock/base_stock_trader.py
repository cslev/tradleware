from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, time
from typing import Optional, Dict, Any, List
import pytz

from src.misc.logger import CustomLogger
from src.misc.get_env import get_env


class BaseStockTrader(ABC):
  """
  Abstract base class for stock/equity broker traders.
  Handles market hours validation, position tracking, and stock-specific trading operations.
  """
  
  bot_type = "stock"  # Used by GUI to distinguish from crypto bots
  
  def __init__(self,
               account_identifier: str,
               broker_id: str,
               symbol: str,
               extended_hours: bool = False,
               logger: Optional[CustomLogger] = None):
    """
    Initializes the BaseStockTrader with broker and symbol details.

    Args:
      account_identifier (str): A unique name for this trading bot (e.g., "MYAPPLEBOT").
      broker_id (str): The ID of the broker (e.g., "ibkr", "alpaca").
      symbol (str): The stock symbol to trade (e.g., "AAPL", "TSLA").
      extended_hours (bool): Allow pre-market and after-hours trading.
      logger (CustomLogger): The logger instance for printing and logging.
    """
    self.account_identifier = account_identifier
    self.broker_id = broker_id.lower()
    self.symbol = symbol.upper()
    self.extended_hours = extended_hours
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
    
    # Market hours (US Eastern Time)
    self.market_timezone = pytz.timezone('US/Eastern')
    self.regular_open = time(9, 30)      # 9:30 AM ET
    self.regular_close = time(16, 0)     # 4:00 PM ET
    self.pre_market_open = time(4, 0)    # 4:00 AM ET
    self.after_hours_close = time(20, 0) # 8:00 PM ET
    
    # Log buffer for UI (keep last 50 messages)
    self.log_buffer = deque(maxlen=50)
    self._add_to_buffer("INFO", f"STOCK trader {account_identifier} initialized for {symbol}")
    
    # Setup log buffer wrapping
    self._setup_log_buffer()
    
    self.logger.info(f"BaseStockTrader initialized for {self.account_identifier} - {self.broker_id} - {self.symbol}")

  def _setup_log_buffer(self):
    """Setup logging to also write to the trader's log buffer"""
    original_info = self.logger.info
    original_error = self.logger.error
    original_warning = self.logger.warning
    original_success = self.logger.success
    original_critical = self.logger.critical

    def info_with_buffer(msg, **kwargs):
      self._add_to_buffer("INFO", msg)
      return original_info(msg, **kwargs)

    def error_with_buffer(msg, **kwargs):
      self._add_to_buffer("ERROR", msg)
      return original_error(msg, **kwargs)

    def warning_with_buffer(msg, **kwargs):
      self._add_to_buffer("WARNING", msg)
      return original_warning(msg, **kwargs)

    def success_with_buffer(msg, **kwargs):
      self._add_to_buffer("SUCCESS", msg)
      return original_success(msg, **kwargs)

    def critical_with_buffer(msg, **kwargs):
      self._add_to_buffer("CRITICAL", msg)
      return original_critical(msg, **kwargs)

    self.logger.info = info_with_buffer
    self.logger.error = error_with_buffer
    self.logger.warning = warning_with_buffer
    self.logger.success = success_with_buffer
    self.logger.critical = critical_with_buffer

  def _add_to_buffer(self, level: str, message: str):
    """Add a log message to this trader's buffer"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    self.log_buffer.append(f"[{timestamp}] {level}: {message}")

  def get_recent_logs(self) -> list:
    """Get recent log messages for this trader"""
    return list(self.log_buffer)

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
    elif self.pre_market_open <= now < self.regular_open:
      return 'pre-market'
    elif self.regular_close <= now < self.after_hours_close:
      return 'after-hours'
    else:
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
      from datetime import timedelta
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
    else:
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
                         spend_percentage: float = 1.0,
                         order_execution_strategy: str = 'market',
                         limit_price: Optional[float] = None,
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
