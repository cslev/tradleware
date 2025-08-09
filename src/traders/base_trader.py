import os
import ccxt
from abc import ABC, abstractmethod
import asyncio # Imported here for the _safe_api_call helper
from src.misc.logger import CustomLogger
from typing import Optional, Dict, Any, List

class BaseExchangeTrader(ABC):
  """
  Abstract base class for interacting with cryptocurrency exchanges.
  Handles generic environment variable loading and defines common trading methods.
  """
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
    Initializes the BaseExchangeTrader with account and exchange details.

    Args:
      account_identifier (str): A unique name for this specific trading setup
                                (e.g., "MYBOT", "MANUAL_TRADER").
      exchange_id (str): The ID of the exchange (e.g., "OKX", "COINBASEPRO").
      default_type (str): The default market type (e.g., 'spot', 'future', 'swap').
      logger (CustomLogger): The logger instance for printing and logging
    """
    self.account_identifier = account_identifier
    self.exchange_id = exchange_id.lower() # Store as lowercase for ccxt lookup
    self.default_type = default_type
    self.logger = logger if logger else CustomLogger(name=self.__class__.__name__)

    # Dynamically construct environment variable names based on identifier and exchange
    self.api_key_env = f'{account_identifier}_{exchange_id.upper()}_API_KEY'
    self.secret_key_env = f'{account_identifier}_{exchange_id.upper()}_SECRET_KEY'
    self.passphrase_env = f'{account_identifier}_{exchange_id.upper()}_PASSPHRASE'
    self.subaccount_name_env = f'{account_identifier}_{exchange_id.upper()}_SUBACCOUNT_NAME' # Generic for subaccounts/portfolios
    self.hostname_env = f'{account_identifier}_{exchange_id.upper()}_HOSTNAME'
    self.fiat_stablecoin_pair_env = f'{account_identifier}_{exchange_id.upper()}_FIAT_STABLECOIN_PAIR'
    self.crypto_stablecoin_pair_env = f'{account_identifier}_{exchange_id.upper()}_CRYPTO_STABLECOIN_PAIR'

    self.api_key = os.getenv(self.api_key_env)
    self.secret_key = os.getenv(self.secret_key_env)
    self.passphrase = os.getenv(self.passphrase_env)
    self.subaccount_name = os.getenv(self.subaccount_name_env)
    self.hostname = os.getenv(self.hostname_env)
    self.fiat_stablecoin_pair = os.getenv(self.fiat_stablecoin_pair_env)
    self.crypto_stablecoin_pair = os.getenv(self.crypto_stablecoin_pair_env)
    

    # --- Implement Validation Here ---
    # List of required environment variables and their corresponding loaded values
    required_vars = [
      (self.api_key_env, self.api_key),
      (self.secret_key_env, self.secret_key),
      (self.passphrase_env, self.passphrase), # Critical for OKX
      (self.subaccount_name_env, self.subaccount_name),
      (self.fiat_stablecoin_pair_env, self.fiat_stablecoin_pair),
      (self.crypto_stablecoin_pair_env, self.crypto_stablecoin_pair)
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

    self.stablecoin_currency = self.fiat_stablecoin_pair.split("/")[0]
    self.fiat_currency = self.fiat_stablecoin_pair.split("/")[1]
    # Validate and derive fiat_currency AFTER fiat_stablecoin_pair is confirmed to exist
    try:
      parts = self.fiat_stablecoin_pair.split("/")
      if len(parts) != 2 or not parts[1]:
        raise ValueError(
          f"Invalid format for {self.fiat_stablecoin_pair_env} ('{self.fiat_stablecoin_pair}'). "
          f"Expected 'BASE/QUOTE' format (e.g., 'USDT/SGD') for the stablecoin pair."
          )
      self.fiat_currency = parts[1]
    except Exception as e:
      # This should ideally be caught by the `missing_vars` check, but provides a safeguard
      # if the variable exists but is malformed.
      raise ValueError(
        f"Error processing {self.fiat_stablecoin_pair_env} ('{self.fiat_stablecoin_pair}'): "
        f"Could not determine fiat currency. Please ensure it's in 'BASE/QUOTE' format. Error: {e}"
        )

    self.exchange = None # This will be initialized by concrete subclasses

    self.logger.success(f"Base credentials loaded for {self.account_identifier} on {self.exchange_id}.")


  async def _safe_api_call(self, 
                           api_method, 
                           *args, 
                           **kwargs):
    """
    Helper method to wrap asynchronous API calls with common error handling.
    This prevents the application from crashing on network or exchange errors.
    """
    try:
      return await api_method(*args, **kwargs)
    except ccxt.NetworkError as e:
      self.logger.error(f"Network error for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise NetworkError for caller to handle specific network issues
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
    except ccxt.ExchangeError as e:
      # General ExchangeError: Log with exc_info and re-raise.
      # Specific interpretation (e.g., "market symbol not found") is left to the caller.
      self.logger.error(f"Exchange error for {self.account_identifier} ({self.exchange_id}): {e}")
      raise # Re-raise ExchangeError for specific handling by caller
    except Exception as e:
      # Catch all other unexpected Python errors that are not CCXT specific.
      self.logger.critical(f"An unexpected CRITICAL error occurred for {self.account_identifier} ({self.exchange_id}): {e}")
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
        self.logger.success(f"Exchange connection for {self.account_identifier} ({self.exchange_id}) closed successfully.")
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
  async def create_order(self, symbol: str, side: str, spend_percentage: float = 1.0, order_execution_strategy: str = 'market', params: dict = {}):
    """
    Abstract method to create an order with flexible execution and amount.
    Must be implemented by concrete exchange classes.

    Args:
      symbol (str): The trading pair symbol (e.g., 'BTC/USDT').
      side (str): The order side ('buy' or 'sell').
      spend_percentage (float): The percentage of available funds/asset to spend/sell (0.0 to 1.0).
                                Default is 1.0 (100%).
      order_execution_strategy (str): 'market' for immediate execution (taker fee),
                                      'maker_limit' for a limit order aiming for maker fee.
      params (dict): Additional exchange-specific parameters.
    """
    pass

  @abstractmethod
  async def cancel_order(self, order_id: str, symbol: str = None, params: dict = {}):
    """
    Abstract method to cancel an order by its ID.
    Must be implemented by concrete exchange classes.
    """
    pass

  @abstractmethod
  async def fetch_open_orders(self, 
                              symbol: str = None, since: int = None, limit: int = None, params: dict = {}):
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

  def _validate_order_params( self, 
                              symbol: str, 
                              side: str, 
                              spend_percentage: float) -> None:
    """Validates order parameters before execution."""
    if side not in self.VALID_ORDER_SIDES:
        raise ValueError(f"Invalid side: {side}. Must be one of {self.VALID_ORDER_SIDES}")
    
    if not self.MIN_SPEND_PERCENTAGE < spend_percentage <= self.MAX_SPEND_PERCENTAGE:
        raise ValueError(f"spend_percentage must be between {self.MIN_SPEND_PERCENTAGE} and {self.MAX_SPEND_PERCENTAGE}")
        
    if not symbol or '/' not in symbol:
        raise ValueError(f"Invalid symbol format: {symbol}")