from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any
# Log the full traceback for debugging
import traceback
import inspect

import ccxt

from src.misc.logger import CustomLogger
from src.misc.get_env import get_env  # Import centralized get_env helper

class BaseCryptoTrader(ABC):
  """
  Abstract base class for cryptocurrency exchange traders.
  Handles CCXT integration, trading pairs, and crypto-specific trading operations.
  """
  
  bot_type = "crypto"  # Used by GUI to distinguish from stock bots
  
  VALID_ORDER_TYPES = ['market', 'maker_limit']
  VALID_MARKET_TYPES = ['spot', 'future', 'swap']
  VALID_ORDER_SIDES = ['buy', 'sell']  # New constant
  MIN_SPEND_PERCENTAGE = 0.0  # New constant
  MAX_SPEND_PERCENTAGE = 1.0  # New constant

  def __init__(self,
               account_identifier: str,
               exchange_id: str,
               default_type: str = 'spot',
               logger: Optional[CustomLogger] = None):
    """
    Initializes the BaseCryptoTrader with account and exchange details.

    Args:
      account_identifier (str): A unique name for this specific trading setup
                                (e.g., "MYBOT", "MANUAL_TRADER").
      exchange_id (str): The ID of the exchange (e.g., "OKX", "IR", "CRYPTOCOM").
      default_type (str): The default market type (e.g., 'spot', 'future', 'swap').
      logger (CustomLogger): The logger instance for printing and logging
    """
    self.account_identifier = account_identifier
    self.exchange_id = exchange_id.lower() # Store as lowercase for ccxt lookup
    self.default_type = default_type
    # Logger is assigned after credentials are validated below; a temp reference
    # is not needed since no logger.xxx calls are made before that point.

    # Dynamically construct environment variable names based on identifier and exchange
    self.api_key_env = f'{account_identifier}_{exchange_id.upper()}_API_KEY'
    self.secret_key_env = f'{account_identifier}_{exchange_id.upper()}_SECRET_KEY'
    self.passphrase_env = f'{account_identifier}_{exchange_id.upper()}_PASSPHRASE'
    self.subaccount_name_env = f'{account_identifier}_{exchange_id.upper()}_SUBACCOUNT_NAME' # Generic for subaccounts/portfolios
    self.hostname_env = f'{account_identifier}_{exchange_id.upper()}_HOSTNAME'
    self.stablecoin_fiat_pair_env = f'{account_identifier}_{exchange_id.upper()}_STABLECOIN_FIAT_PAIR'
    self.crypto_stablecoin_pair_env = f'{account_identifier}_{exchange_id.upper()}_CRYPTO_STABLECOIN_PAIR'
    self.tradleware_api_key = get_env(f"{account_identifier}_{exchange_id.upper()}_TRADLEWARE_API_KEY")

    self.api_key = get_env(self.api_key_env)
    self.secret_key = get_env(self.secret_key_env)
    self.passphrase = get_env(self.passphrase_env)
    self.subaccount_name = get_env(self.subaccount_name_env)
    self.hostname = get_env(self.hostname_env)
    self.stablecoin_fiat_pair = get_env(self.stablecoin_fiat_pair_env)
    self.crypto_stablecoin_pair = get_env(self.crypto_stablecoin_pair_env)
    self.trading_pair_valid = None


    # --- Implement Validation Here ---
    # List of required environment variables and their corresponding loaded values
    required_vars = [
      (self.api_key_env, self.api_key),
      (self.secret_key_env, self.secret_key),
      # (self.passphrase_env, self.passphrase), # Critical for OKX
      # (self.subaccount_name_env, self.subaccount_name), # Optional for exchanges without subaccounts
      (self.stablecoin_fiat_pair_env, self.stablecoin_fiat_pair),
      (self.crypto_stablecoin_pair_env, self.crypto_stablecoin_pair),

    ]

    missing_vars = []
    for env_var_name, loaded_value in required_vars:
      if loaded_value is None:
        missing_vars.append(env_var_name)

    if missing_vars:
      raise ValueError(
        f"Missing one or more required environment variables for {account_identifier} on {exchange_id}. "
        f"Please ensure the following are set in your .env file: {', '.join(missing_vars)}."
      )

    self.stablecoin_currency = self.stablecoin_fiat_pair.split("/")[0]
    self.fiat_currency = self.stablecoin_fiat_pair.split("/")[1]
    # Validate and derive fiat_currency AFTER stablecoin_fiat_pair is confirmed to exist
    try:
      parts = self.stablecoin_fiat_pair.split("/")
      if len(parts) != 2 or not parts[1]:
        raise ValueError(
          f"Invalid format for {self.stablecoin_fiat_pair_env} ('{self.stablecoin_fiat_pair}'). "
          f"Expected 'BASE/QUOTE' format (e.g., 'USDT/SGD') for the stablecoin pair."
          )
      self.fiat_currency = parts[1]
    except Exception as e:
      # This should ideally be caught by the `missing_vars` check, but provides a safeguard
      # if the variable exists but is malformed.
      raise ValueError(
        f"Error processing {self.stablecoin_fiat_pair_env} ('{self.stablecoin_fiat_pair}'): "
        f"Could not determine fiat currency. Please ensure it's in 'BASE/QUOTE' format. Error: {e}"
        ) from e

    self.exchange = None # This will be initialized by concrete subclasses
    self.markets = None # To be loaded asynchronously after initialization
    # Add log buffer for this trader (keep last 50 messages)
    self.log_buffer = deque(maxlen=50)

    # Add some initial logs for testing
    self._add_to_buffer("INFO", f"CRYPTO trader {account_identifier} initialized for {self.crypto_stablecoin_pair}")

    # Create or honour the per-trader named logger (used for the entire object lifetime).
    # If a logger was passed in (e.g. from a subclass that pre-created one), use it;
    # otherwise create a properly account-named one.
    self.logger = logger if logger else CustomLogger(
      f'{account_identifier}_{exchange_id}',
      gotify_url=get_env('GOTIFY_SERVER_URL'),
      gotify_token=get_env('GOTIFY_APP_TOKEN'),
      gotify_log_level=int(get_env('GOTIFY_LOG_LEVEL', '30'))
    )
    self._setup_log_buffer()

    self.logger.info(f"Base credentials loaded for {self.account_identifier} on {self.exchange_id}.")


  async def post_init(self):
    """
    Asynchronous post-initialization: checks if crypto_stablecoin_pair is supported, rewrites to UNSUPPORTED if not.
    """
    try:
      self.markets = await self.exchange.load_markets()
      if self.crypto_stablecoin_pair in self.markets:
        self.trading_pair_valid = True
        self.logger.success(f"Pair supported: {self.crypto_stablecoin_pair}")
      else:
        self.trading_pair_valid = False
        self.logger.error(f"Pair not supported: {self.crypto_stablecoin_pair}")
        # Parse crypto symbol (before '/')
        crypto = self.crypto_stablecoin_pair.split("/")[0] if "/" in self.crypto_stablecoin_pair else self.crypto_stablecoin_pair
        # Find all available pairs for this crypto
        available_pairs = [pair for pair in self.markets.keys() if pair.startswith(f"{crypto}/")]
        if available_pairs:
          self.logger.warning(f"Available pairs for {crypto}: {', '.join(available_pairs)}")
        else:
          self.logger.warning(f"No available pairs found for {crypto}.")
    except Exception as exc:
      self.trading_pair_valid = None
      self.logger.error(f"Error checking pair support: {exc}")




  def _setup_log_buffer(self):
    """Setup logging to also write to the trader's log buffer"""
    # Override logger methods to also store in buffer
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
    """Add a log message to this trader's buffer"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    self.log_buffer.append(f"[{timestamp}] {level}: {message}")

  def get_recent_logs(self) -> list:
    """Get recent log messages for this trader"""
    return list(self.log_buffer)


  async def _safe_api_call(self, api_method, *args, **kwargs):
    """
    Call an API method and handle common ccxt exceptions.
    Accepts both async functions (coroutines) and sync callables (for tests/mocks).
    """
    try:
      result = api_method(*args, **kwargs)
      # If the call returned an awaitable (coroutine/future), await it
      if inspect.isawaitable(result):
        return await result
      # otherwise return the result directly (sync function / Mock returning value for pytest!)
      return result

    except ccxt.AuthenticationError as e:
      self.logger.critical(f"Authentication error for {self.account_identifier} ({self.exchange_id}). Check API keys/passphrase: {e}")
      raise # Re-raise AuthenticationError, as it's often a critical config issue
    except ccxt.ExchangeNotAvailable as e:
      self.logger.warning(f"Exchange not available for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise for caller to decide on retry strategy
    except ccxt.DDoSProtection as e:
      self.logger.warning(f"DDoS protection activated by exchange for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise for caller to implement backoff
    except ccxt.RateLimitExceeded as e:
      self.logger.warning(f"Rate limit exceeded for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise for caller to implement backoff
    except ccxt.NetworkError as e:
      self.logger.error(f"Network error for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise NetworkError for caller to handle specific network issues
    except ccxt.ExchangeError as e:
      # General ExchangeError: Log with exc_info and re-raise.
      # Specific interpretation (e.g., "market symbol not found") is left to the caller.
      self.logger.error(f"Exchange error for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise ExchangeError for specific handling by caller
    except Exception as e:
      # Catch all other unexpected Python errors that are not CCXT specific.
      self.logger.critical(f"An unexpected CRITICAL error occurred for {self.account_identifier} ({self.exchange_id}): {e}")

      self.logger.critical(f"Full traceback:\n{traceback.format_exc()}")
      return None # Return None for truly unhandled, generic exceptions to prevent hard crash



  # --- NEW METHOD TO ADD ---
  async def close(self):
    """
    Closes the exchange connection, releasing resources.
    This coroutine should be called when the exchange instance is no longer needed
    to prevent Unclosed client session warnings/errors.
    """
    if self.exchange and hasattr(self.exchange, 'close') and callable(self.exchange.close):
      self.logger.info(f"Attempting to close exchange connection for {self.account_identifier} ({self.exchange_id})...")
      try:
        await self.exchange.close()
        self.logger.info(f"Exchange connection for {self.account_identifier} ({self.exchange_id}) closed successfully.")
      except Exception as e:
        self.logger.error(f"Error closing exchange connection for {self.account_identifier} ({self.exchange_id}): {e}")
    else:
      self.logger.warning(f"No active exchange connection or .close() method found for {self.account_identifier} ({self.exchange_id}).")


  @abstractmethod
  async def fetch_balance(self):
    """
    Abstract method to fetch the account balance.
    Must be implemented by concrete exchange classes.
    """
    pass

  @abstractmethod
  async def create_order(self, 
                         symbol: str, 
                         side: str, 
                         spend_percentage: float = None, 
                         quantity: float = None,
                         order_execution_strategy: str = 'market',
                         dry_run: bool = False,
                         params: dict = None):
    """
    Abstract method to create an order with flexible execution and amount.
    Must be implemented by concrete exchange classes.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT').
      side (str): The order side ('buy' or 'sell').
      spend_percentage (float): The percentage of available funds/asset to spend/sell (0.0 to 1.0).
                                Either spend_percentage or quantity must be provided (not both).
      quantity (float): The exact amount of crypto to buy/sell (e.g., 0.5 BTC).
                        Either spend_percentage or quantity must be provided (not both).
      order_execution_strategy (str): 'market' for immediate execution (taker fee),
                                      'maker_limit' for a limit order aiming for maker fee.
      dry_run (bool): If True, simulate the order without executing it (default: False).
      params (dict): Additional exchange-specific parameters.
    """
    pass

  @abstractmethod
  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = None):
    """
    Abstract method to cancel an order by its ID.
    Must be implemented by concrete exchange classes.
    """
    pass

  @abstractmethod
  async def fetch_open_orders(self,
                              symbol: str = None, since: int = None, limit: int = None, params: dict = None):
    """
    Abstract method to fetch open orders.
    Must be implemented by concrete exchange classes.
    """
    pass

  # @abstractmethod
  # async def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
  #   """Fetch current positions (especially important for futures)."""
  #   pass

  # @abstractmethod
  # async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
  #     """Fetch current price information for a symbol."""
  #     pass

  # @abstractmethod
  # async def fetch_order_book(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
  #     """Fetch order book for a symbol."""
  #     pass

  async def _resolve_market_and_balance(self, symbol: str) -> Dict[str, Any]:
    """
    Loads market data and fetches account balance for a given symbol.
    Centralises the repeated market-loading + balance-fetching block that every
    create_order implementation needs.

    Args:
      symbol (str): Trading pair symbol (e.g. 'BTC/USDT').

    Returns:
      dict with keys:
        'market'        — raw CCXT market dict
        'base'          — base currency string (e.g. 'BTC')
        'quote'         — quote currency string (e.g. 'USDT')
        'amount_limits' — {'min': float|None, 'max': float|None}
        'cost_limits'   — {'min': float|None, 'max': float|None}
        'free'          — {currency: float} free balances
        'total'         — {currency: float} total balances

    Raises:
      RuntimeError: If markets cannot be loaded or the symbol is not found.
      RuntimeError: If balance cannot be fetched.
    """
    # --- load markets ---
    load_result = await self._safe_api_call(self.exchange.load_markets, True)
    if load_result is None and not getattr(self.exchange, 'markets', None):
      raise RuntimeError(f"Failed to load markets for {self.exchange_id}. Cannot resolve symbol '{symbol}'.")

    try:
      market = self.exchange.market(symbol)
    except ccxt.ExchangeError as exc:
      raise RuntimeError(f"Symbol '{symbol}' not found on {self.exchange_id}: {exc}") from exc
    except Exception as exc:
      raise RuntimeError(f"Error resolving market for '{symbol}' on {self.exchange_id}: {exc}") from exc

    if not market:
      raise RuntimeError(f"Market data empty for '{symbol}' on {self.exchange_id}.")

    base  = market['base']
    quote = market['quote']
    limits        = market.get('limits', {}) or {}
    amount_limits = limits.get('amount', {}) or {}
    cost_limits   = limits.get('cost',   {}) or {}

    # --- fetch balance ---
    balance_info = await self.fetch_balance()
    if not balance_info:
      raise RuntimeError(f"Could not fetch balance for {self.account_identifier} on {self.exchange_id}.")

    free  = balance_info.get('free',  {}) or {}
    total = balance_info.get('total', {}) or {}

    self.logger.debug(
      f"[RESOLVE] symbol={symbol} base={base} quote={quote} "
      f"amount_limits={amount_limits} cost_limits={cost_limits} "
      f"free_{quote}={free.get(quote)} free_{base}={free.get(base)}"
    )

    return {
      'market':        market,
      'base':          base,
      'quote':         quote,
      'amount_limits': amount_limits,
      'cost_limits':   cost_limits,
      'free':          free,
      'total':         total,
    }

  def _get_maker_buy_price(self, symbol: str, ticker: dict) -> float:
    """
    Calculate the limit buy price targeting maker fee.
    Default: 0.01% below current bid.
    Override in subclasses for exchange-specific slippage logic.

    Args:
      symbol (str): Trading pair — used for price precision.
      ticker (dict): CCXT ticker dict, must contain 'bid'.

    Returns:
      float: Limit buy price with exchange precision applied.

    Raises:
      ValueError: If bid price is missing or invalid.
    """
    bid = ticker.get('bid')
    if not bid or float(bid) <= 0:
      raise ValueError(f"Invalid bid price in ticker for {symbol}: {bid}")
    return float(self.exchange.price_to_precision(symbol, float(bid) * 0.9999))

  def _get_maker_sell_price(self, symbol: str, ticker: dict) -> float:
    """
    Calculate the limit sell price targeting maker fee.
    Default: 0.01% above current ask.
    Override in subclasses for exchange-specific slippage logic.

    Args:
      symbol (str): Trading pair — used for price precision.
      ticker (dict): CCXT ticker dict, must contain 'ask'.

    Returns:
      float: Limit sell price with exchange precision applied.

    Raises:
      ValueError: If ask price is missing or invalid.
    """
    ask = ticker.get('ask')
    if not ask or float(ask) <= 0:
      raise ValueError(f"Invalid ask price in ticker for {symbol}: {ask}")
    return float(self.exchange.price_to_precision(symbol, float(ask) * 1.0001))

  def _safe_amount_to_precision(self, symbol: str, amount: float) -> float:
    """
    Wraps exchange.amount_to_precision with a fallback to the raw value.
    Some exchanges (e.g. Independent Reserve via CCXT) may not define
    amount precision for all markets, causing a TypeError/AttributeError.
    Falls back to the unmodified float so the order can still proceed.
    """
    try:
      return float(self.exchange.amount_to_precision(symbol, amount))
    except Exception:  # pylint: disable=broad-except
      return float(amount)

  async def _calculate_order_size(
      self,
      symbol: str,
      side: str,
      ctx: Dict[str, Any],
      spend_percentage: float = None,
      quantity: float = None,
      order_execution_strategy: str = 'market',
  ) -> tuple:
    """
    Determines order_type, amount_to_trade, and price from market context and inputs.
    All balance checks and exchange limit validations are performed here.

    For spend_percentage market buy, amount_to_trade is returned in QUOTE currency
    (the cost to spend) — the execution layer is responsible for converting to base.
    For all other cases, amount_to_trade is in BASE currency with precision applied.

    Args:
      symbol (str): Trading pair symbol.
      side (str): 'buy' or 'sell'.
      ctx (dict): Output of _resolve_market_and_balance().
      spend_percentage (float): Fraction of available funds (0.0 < x <= 1.0).
      quantity (float): Exact base currency amount.
      order_execution_strategy (str): 'market' or 'maker_limit'.

    Returns:
      tuple: (order_type: str, amount_to_trade: float, price: float | None)

    Raises:
      ValueError: Insufficient balance or exchange limits violated.
      RuntimeError: Ticker fetch failed when required.
    """
    base          = ctx['base']
    quote         = ctx['quote']
    amount_limits = ctx['amount_limits']
    cost_limits   = ctx['cost_limits']
    free          = ctx['free']
    total         = ctx['total']

    order_type      = 'market'
    amount_to_trade = 0.0
    price           = None

    #####################
    ### QUANTITY MODE ###
    #####################
    if quantity is not None:
      self.logger.info(f"[QUANTITY MODE] {side} {quantity} {base} on {symbol} via {order_execution_strategy}")
      amount_to_trade = quantity

      min_amount = amount_limits.get('min')
      max_amount = amount_limits.get('max')
      if min_amount is not None and amount_to_trade < min_amount:
        raise ValueError(
          f"Order amount {amount_to_trade:.8f} {base} is below exchange minimum {min_amount:.8f} {base}."
        )
      if max_amount is not None and amount_to_trade > max_amount:
        raise ValueError(
          f"Order amount {amount_to_trade:.8f} {base} exceeds exchange maximum {max_amount:.8f} {base}."
        )

      if side == 'buy':
        # Apply upward precision buffer so post-precision quantity >= requested
        precision_amount = self._safe_amount_to_precision(symbol, quantity * 1.001)
        if precision_amount < quantity:
          self.logger.warning(
            f"⚠️ Exchange precision prevents buying exactly {quantity} {base}. "
            f"Will buy {precision_amount} {base} instead."
          )
        elif precision_amount > quantity:
          self.logger.info(f"Adjusted buy amount {quantity} → {precision_amount} {base} for precision.")
        amount_to_trade = precision_amount

        ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
        if not ticker or not ticker.get('last'):
          raise RuntimeError(f"Could not fetch ticker for {symbol} to validate buy order.")
        current_price = float(ticker['last'])
        if current_price <= 0:
          raise RuntimeError(f"Invalid last price from ticker for {symbol}: {current_price}")

        estimated_cost  = amount_to_trade * current_price
        available_quote = free.get(quote, total.get(quote, 0.0))
        if estimated_cost > available_quote:
          raise ValueError(
            f"Insufficient {quote} balance. Need ~{estimated_cost:.2f}, have {available_quote:.2f}."
          )

        if order_execution_strategy == 'market':
          order_type = 'market'
          self.logger.info(f"[QUANTITY MODE] Market buy: {amount_to_trade} {base}")
        elif order_execution_strategy == 'maker_limit':
          order_type = 'limit'
          price = self._get_maker_buy_price(symbol, ticker)
          self.logger.info(f"[QUANTITY MODE] Maker limit buy: {amount_to_trade} {base} @ {price}")

      elif side == 'sell':
        available_base = free.get(base, total.get(base, 0.0))
        if quantity > available_base:
          raise ValueError(
            f"Insufficient {base} balance. Need {quantity:.8f}, have {available_base:.8f}."
          )
        if order_execution_strategy == 'market':
          order_type = 'market'
          self.logger.info(f"[QUANTITY MODE] Market sell: {amount_to_trade} {base}")
        elif order_execution_strategy == 'maker_limit':
          order_type = 'limit'
          ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
          if not ticker:
            raise RuntimeError(f"Could not fetch ticker for {symbol} for maker limit sell.")
          price = self._get_maker_sell_price(symbol, ticker)
          self.logger.info(f"[QUANTITY MODE] Maker limit sell: {amount_to_trade} {base} @ {price}")

      # Precision already applied for buy above; apply for sell here
      if side == 'sell':
        amount_to_trade = self._safe_amount_to_precision(symbol, amount_to_trade)

    #############################
    ### SPEND PERCENTAGE MODE ###
    #############################
    elif spend_percentage is not None:
      self.logger.info(
        f"[SPEND % MODE] {side} {spend_percentage*100:.1f}% on {symbol} via {order_execution_strategy}"
      )

      if side == 'buy':
        available_quote = free.get(quote, total.get(quote, 0.0))
        spend_cost      = available_quote * spend_percentage
        if spend_cost <= 0:
          raise ValueError(
            f"Insufficient {quote} balance ({available_quote:.2f}) to place buy order."
          )

        if order_execution_strategy == 'market':
          order_type = 'market'
          # amount_to_trade is in QUOTE (cost); execution layer converts to base
          amount_to_trade = spend_cost
          self.logger.info(f"[SPEND % MODE] Market buy cost: {amount_to_trade:.2f} {quote}")
          min_cost = cost_limits.get('min')
          max_cost = cost_limits.get('max')
          if min_cost is not None and amount_to_trade < min_cost:
            raise ValueError(
              f"Order cost {amount_to_trade:.2f} {quote} is below exchange minimum {min_cost:.2f} {quote}."
            )
          if max_cost is not None and amount_to_trade > max_cost:
            raise ValueError(
              f"Order cost {amount_to_trade:.2f} {quote} exceeds exchange maximum {max_cost:.2f} {quote}."
            )

        elif order_execution_strategy == 'maker_limit':
          order_type = 'limit'
          ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
          if not ticker or not ticker.get('bid'):
            raise RuntimeError(f"Could not fetch bid price for {symbol} for maker limit buy.")
          price = self._get_maker_buy_price(symbol, ticker)
          if float(price) <= 0:
            raise RuntimeError("Calculated maker buy price is zero or negative.")
          amount_to_trade = spend_cost / float(price)
          self.logger.info(f"[SPEND % MODE] Maker limit buy: {amount_to_trade:.8f} {base} @ {price}")
          min_amount = amount_limits.get('min')
          max_amount = amount_limits.get('max')
          if min_amount is not None and amount_to_trade < min_amount:
            raise ValueError(
              f"Order amount {amount_to_trade:.6f} {base} is below exchange minimum {min_amount:.6f} {base}."
            )
          if max_amount is not None and amount_to_trade > max_amount:
            raise ValueError(
              f"Order amount {amount_to_trade:.6f} {base} exceeds exchange maximum {max_amount:.6f} {base}."
            )
          amount_to_trade = self._safe_amount_to_precision(symbol, amount_to_trade)

      elif side == 'sell':
        available_base  = free.get(base, total.get(base, 0.0))
        amount_to_trade = available_base * spend_percentage
        if amount_to_trade <= 0:
          raise ValueError(
            f"Insufficient {base} balance ({available_base:.8f}) to place sell order."
          )
        min_amount = amount_limits.get('min')
        max_amount = amount_limits.get('max')
        if min_amount is not None and amount_to_trade < min_amount:
          raise ValueError(
            f"Sell amount {amount_to_trade:.6f} {base} is below exchange minimum {min_amount:.6f} {base}."
          )
        if max_amount is not None and amount_to_trade > max_amount:
          raise ValueError(
            f"Sell amount {amount_to_trade:.6f} {base} exceeds exchange maximum {max_amount:.6f} {base}."
          )
        if order_execution_strategy == 'market':
          order_type = 'market'
          amount_to_trade = self._safe_amount_to_precision(symbol, amount_to_trade)
          self.logger.info(f"[SPEND % MODE] Market sell: {amount_to_trade} {base}")
        elif order_execution_strategy == 'maker_limit':
          order_type = 'limit'
          ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
          if not ticker or not ticker.get('ask'):
            raise RuntimeError(f"Could not fetch ask price for {symbol} for maker limit sell.")
          price = self._get_maker_sell_price(symbol, ticker)
          amount_to_trade = self._safe_amount_to_precision(symbol, amount_to_trade)
          self.logger.info(f"[SPEND % MODE] Maker limit sell: {amount_to_trade} {base} @ {price}")

    return order_type, amount_to_trade, price

  def _validate_order_params(self,
                              symbol: str,
                              side: str,
                              spend_percentage: float = None,
                              quantity: float = None,
                              order_execution_strategy: str = 'market',
                              dry_run: bool = False) -> None:
    """
    Validates all order parameters before execution.

    Args:
      symbol (str): Trading pair symbol — must contain '/'.
      side (str): Order side — must be one of VALID_ORDER_SIDES.
      spend_percentage (float): Fraction of available funds to spend (0.0 < x <= 1.0).
      quantity (float): Exact base currency amount — must be positive.
      order_execution_strategy (str): Must be one of VALID_ORDER_TYPES.
      dry_run (bool): Must be a bool.

    Raises:
      ValueError: If any parameter is invalid.
    """
    # --- symbol ---
    if not symbol or '/' not in symbol:
      raise ValueError(f"Invalid symbol format: '{symbol}'. Expected 'BASE/QUOTE' (e.g. 'BTC/USDT').")

    # --- side ---
    if side not in self.VALID_ORDER_SIDES:
      raise ValueError(f"Invalid side: '{side}'. Must be one of {self.VALID_ORDER_SIDES}.")

    # --- spend_percentage / quantity mutual exclusivity ---
    if spend_percentage is not None and quantity is not None:
      raise ValueError("Cannot specify both spend_percentage and quantity. Choose one.")

    if spend_percentage is None and quantity is None:
      raise ValueError("Must specify either spend_percentage or quantity.")

    # --- spend_percentage range ---
    if spend_percentage is not None:
      if not self.MIN_SPEND_PERCENTAGE < spend_percentage <= self.MAX_SPEND_PERCENTAGE:
        raise ValueError(
          f"spend_percentage must be between {self.MIN_SPEND_PERCENTAGE} (exclusive) "
          f"and {self.MAX_SPEND_PERCENTAGE} (inclusive), got: {spend_percentage}."
        )

    # --- quantity positivity ---
    if quantity is not None:
      if quantity <= 0:
        raise ValueError(f"quantity must be positive, got: {quantity}.")

    # --- order_execution_strategy ---
    if order_execution_strategy not in self.VALID_ORDER_TYPES:
      raise ValueError(
        f"Invalid order_execution_strategy: '{order_execution_strategy}'. "
        f"Must be one of {self.VALID_ORDER_TYPES}."
      )

    # --- dry_run type ---
    if not isinstance(dry_run, bool):
      raise ValueError(f"dry_run must be a bool, got: {type(dry_run).__name__}.")

    self.logger.debug(
      f"Order parameters validated: symbol={symbol}, side={side}, "
      f"spend_percentage={spend_percentage}, quantity={quantity}, "
      f"order_execution_strategy={order_execution_strategy}, dry_run={dry_run}"
    )
