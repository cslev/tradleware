import asyncio
import math

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
      if not self.is_connected:
        await self.connect()

      # Get all positions
      all_positions = self.ib.positions()
      self.logger.debug(f"All positions: {all_positions}")

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
        return {
          'symbol': self.symbol,
          'quantity': 0,
          'unrealized_pnl': 0.0,
          'unrealized_pnl_pct': 0.0
        }
      
      # Get position details
      quantity = int(target_pos.position)
      avg_cost = float(target_pos.avgCost)
      total_cost = quantity * avg_cost
      
      # Get real-time P&L data
      
      account = self.ib.wrapper.accounts[0] if self.ib.wrapper.accounts else self.account_id
      pnl_stream = self.ib.reqPnLSingle(account, "", target_pos.contract.conId)
      
      # Wait for P&L data to arrive with retry logic
      unrealized_pnl = 0.0
      for attempt in range(5):  # Try up to 5 times
        await asyncio.sleep(0.5)  # Wait 0.5 seconds between attempts
        
        if pnl_stream.unrealizedPnL is not None and not math.isnan(pnl_stream.unrealizedPnL):
          unrealized_pnl = float(pnl_stream.unrealizedPnL)
          self.logger.debug(f"Got P&L data on attempt {attempt + 1}: ${unrealized_pnl:.2f}")
          break
        else:
          self.logger.info(f"Waiting for P&L data... attempt {attempt + 1}/5")
      else:
        self.logger.warning(f"P&L data not available after 2.5 seconds, using 0.0")
      
      unrealized_pnl_pct = (unrealized_pnl / abs(total_cost) * 100) if total_cost != 0 else 0.0
      
      self.logger.info(f"Position: {quantity} shares, cost: ${total_cost:.2f}, P&L: ${unrealized_pnl:.2f} ({unrealized_pnl_pct:.2f}%)")
      
      return {
        'symbol': self.symbol,
        'quantity': quantity,
        'unrealized_pnl': unrealized_pnl,
        'unrealized_pnl_pct': unrealized_pnl_pct
      }
     
      
    except Exception as e:
      self.logger.error(f"Error fetching positions: {e}", exc_info=True)
      return {
        'symbol': self.symbol,
        'quantity': 0,
        'unrealized_pnl': 0.0,
        'unrealized_pnl_pct': 0.0
      }

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
      
      symbol_str = contract.symbol if contract else (symbol or self.symbol)
      
      if tickers:
        ticker = tickers[0]
        if ticker.marketPrice() and ticker.marketPrice() > 0:
          self.logger.info(f"Got delayed market price for {symbol_str}")
          return float(ticker.marketPrice())
        if ticker.last and ticker.last > 0:
          self.logger.info(f"Got last price for {symbol_str}")
          return float(ticker.last)
        if ticker.close and ticker.close > 0:
          self.logger.info(f"Got close price for {symbol_str}")
          return float(ticker.close)
      
      # Fallback: Get recent historical data (always available)
      self.logger.info(f"Falling back to historical data for {symbol_str}")
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
        self.logger.info(f"Got historical close price for {symbol_str}: ${last_bar.close}")
        return float(last_bar.close)
      
      self.logger.warning(f"No price data available for {symbol_str}")
      return None
        
    except Exception as e:
      self.logger.error(f"Error fetching market price for {symbol_str if 'symbol_str' in locals() else (symbol or self.symbol)}: {e}")
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
