import os
import ccxt
from abc import ABC, abstractmethod
import asyncio # Imported here for the _safe_api_call helper
from misc.logger import CustomLogger

class BaseExchangeTrader(ABC):
  """
  Abstract base class for interacting with cryptocurrency exchanges.
  Handles generic environment variable loading and defines common trading methods.
  """
  def __init__(self, account_identifier: str, exchange_id: str, default_type: str = 'spot', logger: CustomLogger = None):
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

    self.api_key = os.getenv(self.api_key_env)
    self.secret_key = os.getenv(self.secret_key_env)
    self.passphrase = os.getenv(self.passphrase_env)
    self.subaccount_name = os.getenv(self.subaccount_name_env)
    self.hostname = os.getenv(self.hostname_env)

    # Basic validation for essential credentials
    if not self.api_key or not self.secret_key:
      raise ValueError(
          f"Missing essential API credentials for {account_identifier} on {exchange_id}. "
          f"Please ensure {self.api_key_env} and {self.secret_key_env} are set in your .env file."
      )

    self.exchange = None # This will be initialized by concrete subclasses

    return(f"Base credentials loaded for {self.account_identifier} on {self.exchange_id}.")

  async def _safe_api_call(self, api_method, *args, **kwargs):
    """
    Helper method to wrap asynchronous API calls with common error handling.
    This prevents the application from crashing on network or exchange errors.
    """
    try:
      return await api_method(*args, **kwargs)
    except ccxt.NetworkError as e:
      self.logger.error(f"Network error for {self.account_identifier} ({self.exchange_id}): {e}")
    except ccxt.ExchangeError as e:
      self.logger.error(f"Exchange error for {self.account_identifier} ({self.exchange_id}): {e}")
    except Exception as e:
      self.logger.error(f"An unexpected error occurred for {self.account_identifier} ({self.exchange_id}): {e}")
    return None

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
  async def fetch_open_orders(self, symbol: str = None, since: int = None, limit: int = None, params: dict = {}):
    """
    Abstract method to fetch open orders.
    Must be implemented by concrete exchange classes.
    """
    pass
