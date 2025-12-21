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
    self.account_id = get_env(f'{account_identifier}_ACCOUNT_ID')
    
    # IB client
    self.ib = IB()
    self.contract = None  # Will be created on connect
    self.is_connected = False

    self.logger.info(f"IBKRTrader initialized: {symbol} on port {self.gateway_port}")

  async def connect(self):
    """
    Connect to IB Gateway and create stock contract.
    """
    try:
      if self.is_connected:
        self.logger.warning("Already connected to IB Gateway")
        return

      self.logger.info(f"Connecting to IB Gateway at {self.gateway_host}:{self.gateway_port}...")
      
      # Connect to IB Gateway asynchronously
      await self.ib.connectAsync(
        host=self.gateway_host,
        port=self.gateway_port,
        clientId=hash(self.account_identifier) % 1000  # Unique client ID per bot
      )
      
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
    """Placeholder - to be implemented"""
    pass

  async def fetch_positions(self) -> Dict[str, Any]:
    """Placeholder - to be implemented"""
    return {}

  async def fetch_account_value(self) -> Dict[str, Any]:
    """Placeholder - to be implemented"""
    return {}

  async def get_market_price(self, symbol: Optional[str] = None) -> Optional[float]:
    """
    Get current market price for a symbol.
    First tries delayed market data, falls back to historical close price.

    Args:
      symbol: Stock symbol (e.g., "AAPL")

    Returns:
      Current market price or None if unavailable.
    """
    try:
      if not self.is_connected:
        await self.connect()

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
          self.logger.info(f"Got delayed market price for {symbol}")
          return float(ticker.marketPrice())
        if ticker.last and ticker.last > 0:
          self.logger.info(f"Got last price for {symbol}")
          return float(ticker.last)
        if ticker.close and ticker.close > 0:
          self.logger.info(f"Got close price for {symbol}")
          return float(ticker.close)
      
      # Fallback: Get recent historical data (always available)
      self.logger.info(f"Falling back to historical data for {symbol}")
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
        self.logger.info(f"Got historical close price for {symbol}: ${last_bar.close}")
        return float(last_bar.close)
      
      self.logger.warning(f"No price data available for {symbol}")
      return None
        
    except Exception as e:
      self.logger.error(f"Error fetching market price for {symbol}: {e}")
      return None

  async def create_order(self, side: str, quantity: int, order_type: str = 'market', limit_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Placeholder - to be implemented"""
    return None

  async def cancel_order(self, order_id: str) -> bool:
    """Placeholder - to be implemented"""
    return False

  async def fetch_open_orders(self) -> List[Dict[str, Any]]:
    """Placeholder - to be implemented"""
    return []

  async def close(self):
    """Placeholder - to be implemented"""
    pass


async def main():
  """
  Test function to verify IBKR trader initialization and connection.
  """
  import asyncio
  
  print("=" * 60)
  print("IBKR Trader Test - Init & Connect Only")
  print("=" * 60)
  
  # Initialize trader
  trader = IBKRTrader(
    account_identifier="TESTBOT",
    symbol="PLTR",
    extended_hours=False
  )
  
  try:
    # Test 1: Connect
    print("\n[TEST 1] Connecting to IB Gateway...")
    await trader.connect()
    print("✓ Connection successful")
    input("\nPress Enter to continue to next test...")
    
    # Test 2: Fetch market price
    print("\n[TEST 2] Fetching market price")
    price = await trader.get_market_price()
    print(f"✓ Current price: ${price}")
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
