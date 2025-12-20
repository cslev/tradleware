from typing import Optional, Dict, Any, List
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order
import asyncio

from src.traders.stock.base_stock_trader import BaseStockTrader
from src.misc.logger import CustomLogger
from src.misc.get_env import get_env


class IBKRTrader(BaseStockTrader):
  """
  Interactive Brokers trader implementation using IB Gateway.
  Connects to IB Gateway for stock trading operations.
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
    self.gateway_port = 8888
    self.account_id = get_env(f'{account_identifier}_ACCOUNT_ID')
    
    # IB client
    self.ib = IB()
    self.contract = None  # Will be created on connect
    self.is_connected = False

    self.logger.info(f"IBKRTrader initialized: {symbol} on port {self.gateway_port}")

  async def connect(self):
    """
    Connect to IB Gateway.
    """
    try:
      if self.is_connected:
        self.logger.warning("Already connected to IB Gateway")
        return

      self.logger.info(f"Connecting to IB Gateway at {self.gateway_host}:{self.gateway_port}...")
      
      # Connect to IB Gateway with longer timeout
      await self.ib.connectAsync(
        host=self.gateway_host,
        port=self.gateway_port,
        clientId=hash(self.account_identifier) % 1000,  # Unique client ID per bot
        timeout=20  # Increase timeout to 20 seconds
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
    """
    Disconnect from IB Gateway.
    """
    try:
      if not self.is_connected:
        self.logger.warning("Not connected to IB Gateway")
        return

      self.ib.disconnect()
      self.is_connected = False
      self.logger.info("Disconnected from IB Gateway")
      
    except Exception as e:
      self.logger.error(f"Error disconnecting from IB Gateway: {e}")

  async def fetch_positions(self) -> Dict[str, Any]:
    """
    Get current position for this symbol.

    Returns:
      Dictionary with position information:
      {
        'symbol': str,
        'quantity': int,
        'avg_cost': float,
        'market_value': float,
        'unrealized_pnl': float,
        'unrealized_pnl_pct': float
      }
    """
    try:
      if not self.is_connected:
        await self.connect()

      # Get all positions for this account, so it returns all stocks held under that account
      positions = self.ib.positions(account=self.account_id)
      self.logger.info(f"Fetched positions from IBKR for account {self.account_id}: {positions}")
      # Find position for our symbol
      for position in positions:
        if position.contract.symbol == self.symbol:
          # Get current market price
          current_price = await self.get_market_price(self.symbol)
          
          quantity = int(position.position)
          avg_cost = float(position.avgCost)
          market_value = quantity * current_price if current_price else 0
          unrealized_pnl = market_value - (quantity * avg_cost)
          unrealized_pnl_pct = (unrealized_pnl / (quantity * avg_cost) * 100) if quantity * avg_cost != 0 else 0
          
          return {
            'symbol': self.symbol,
            'quantity': quantity,
            'avg_cost': avg_cost,
            'market_value': market_value,
            'unrealized_pnl': unrealized_pnl,
            'unrealized_pnl_pct': unrealized_pnl_pct
          }
      
      # No position found - return zero position
      return {
        'symbol': self.symbol,
        'quantity': 0,
        'avg_cost': 0.0,
        'market_value': 0.0,
        'unrealized_pnl': 0.0,
        'unrealized_pnl_pct': 0.0
      }
      
    except Exception as e:
      self.logger.error(f"Error fetching positions: {e}")
      return {
        'symbol': self.symbol,
        'quantity': 0,
        'avg_cost': 0.0,
        'market_value': 0.0,
        'unrealized_pnl': 0.0,
        'unrealized_pnl_pct': 0.0
      }

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
    try:
      if not self.is_connected:
        await self.connect()

      # Get account summary
      account_values = self.ib.accountValues(account=self.account_id)
      
      cash = 0.0
      buying_power = 0.0
      total_value = 0.0
      
      for item in account_values:
        if item.tag == 'CashBalance':
          cash = float(item.value)
        elif item.tag == 'BuyingPower':
          buying_power = float(item.value)
        elif item.tag == 'NetLiquidation':
          total_value = float(item.value)
      
      return {
        'cash': cash,
        'buying_power': buying_power,
        'total_value': total_value
      }
      
    except Exception as e:
      self.logger.error(f"Error fetching account value: {e}")
      return {
        'cash': 0.0,
        'buying_power': 0.0,
        'total_value': 0.0
      }

  async def get_market_price(self, symbol: str) -> Optional[float]:
    """
    Get current market price for a symbol.

    Args:
      symbol: Stock symbol (e.g., "AAPL")

    Returns:
      Current market price or None if unavailable.
    """
    try:
      if not self.is_connected:
        await self.connect()

      # Create contract if it's different from our main symbol
      if symbol != self.symbol:
        contract = Stock(symbol, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)
      else:
        contract = self.contract

      # Request ticker snapshot (better than streaming data)
      ticker = self.ib.reqTickers(contract)[0]
      
      # Wait for ticker to be populated
      for _ in range(10):  # Try for up to 5 seconds
        self.ib.sleep(0.5)
        if ticker.marketPrice() and ticker.marketPrice() > 0:
          return float(ticker.marketPrice())
        if ticker.last and ticker.last > 0:
          return float(ticker.last)
        if ticker.close and ticker.close > 0:
          return float(ticker.close)
      
      self.logger.warning(f"No valid price data for {symbol} after 5 seconds")
      return None
        
    except Exception as e:
      self.logger.error(f"Error fetching market price for {symbol}: {e}")
      return None

  async def create_order(self,
                         side: str,
                         quantity: int,
                         order_type: str = 'market',
                         limit_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """
    Place a buy/sell order.

    Args:
      side: 'buy' or 'sell'
      quantity: Number of shares (must be positive integer)
      order_type: 'market' or 'limit'
      limit_price: Required if order_type is 'limit'

    Returns:
      Order information dict or None on failure.
    """
    try:
      # Validate market hours
      if not self.can_trade_now():
        market_status = self.get_market_status()
        time_until_open = self.get_time_until_market_opens()
        self.logger.error(f"Cannot trade: Market is {market_status}. Opens in {time_until_open}")
        return None

      # check if connected
      if not self.is_connected:
        await self.connect()

      # Validate inputsm can only be buy or sell
      side = side.lower()
      if side not in ['buy', 'sell']:
        self.logger.error(f"Invalid side: {side}. Must be 'buy' or 'sell'")
        return None

      if quantity <= 0:
        self.logger.error(f"Invalid quantity: {quantity}. Must be positive integer")
        return None

      # Create order
      action = 'BUY' if side == 'buy' else 'SELL'
      
      if order_type.lower() == 'market':
        order = MarketOrder(action, quantity)
        self.logger.info(f"Placing market order: {action} {quantity} {self.symbol}")
      elif order_type.lower() == 'limit':
        if limit_price is None:
          self.logger.error("Limit price required for limit orders")
          return None
        order = LimitOrder(action, quantity, limit_price)
        self.logger.info(f"Placing limit order: {action} {quantity} {self.symbol} @ ${limit_price}")
      else:
        self.logger.error(f"Invalid order type: {order_type}")
        return None

      # Place order
      trade = self.ib.placeOrder(self.contract, order)
      
      # Wait for order to be submitted
      self.ib.sleep(1)
      
      order_info = {
        'order_id': trade.order.orderId,
        'symbol': self.symbol,
        'side': side,
        'quantity': quantity,
        'order_type': order_type,
        'status': trade.orderStatus.status,
        'filled': trade.orderStatus.filled,
        'remaining': trade.orderStatus.remaining
      }
      
      if order_type.lower() == 'limit':
        order_info['limit_price'] = limit_price
      
      self.logger.success(f"Order placed successfully: {order_info}")
      return order_info
      
    except Exception as e:
      self.logger.error(f"Error creating order: {e}")
      return None

  async def cancel_order(self, order_id: str) -> bool:
    """
    Cancel an order by its ID.

    Args:
      order_id: Order identifier from the broker.

    Returns:
      True if cancelled successfully, False otherwise.
    """
    try:
      #check if connected
      if not self.is_connected:
        await self.connect()

      # Find the trade by order ID
      trades = self.ib.trades()
      for trade in trades:
        if str(trade.order.orderId) == str(order_id):
          self.ib.cancelOrder(trade.order)
          self.logger.success(f"Order {order_id} cancelled")
          return True
      
      self.logger.warning(f"Order {order_id} not found")
      return False
      
    except Exception as e:
      self.logger.error(f"Error cancelling order {order_id}: {e}")
      return False

  async def fetch_open_orders(self) -> List[Dict[str, Any]]:
    """
    Fetch all open orders for this symbol.

    Returns:
      List of open order dictionaries.
    """
    try:
      # check if connected
      if not self.is_connected:
        await self.connect()

      # get open trades
      trades = self.ib.openTrades()
      open_orders = []
      
      for trade in trades:
        # Filter for our symbol
        if trade.contract.symbol == self.symbol:
          order_info = {
            'order_id': trade.order.orderId,
            'symbol': self.symbol,
            'side': 'buy' if trade.order.action == 'BUY' else 'sell',
            'quantity': trade.order.totalQuantity,
            'order_type': trade.order.orderType.lower(),
            'status': trade.orderStatus.status,
            'filled': trade.orderStatus.filled,
            'remaining': trade.orderStatus.remaining
          }
          
          if hasattr(trade.order, 'lmtPrice') and trade.order.lmtPrice:
            order_info['limit_price'] = trade.order.lmtPrice
          
          open_orders.append(order_info)
      
      return open_orders
      
    except Exception as e:
      self.logger.error(f"Error fetching open orders: {e}")
      return []

  async def close(self):
    """
    Cleanup method to close IBKR connection.
    """
    try:
      await self.disconnect()
      self.logger.info(f"IBKR trader {self.account_identifier} closed successfully")
    except Exception as e:
      self.logger.error(f"Error closing IBKR trader {self.account_identifier}: {e}")


async def main():
  """
  Test function to verify IBKR trader functionality.
  """
  print("=" * 60)
  print("IBKR Trader Test")
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
    
    # Test 2: Fetch market price
    print("\n[TEST 2] Fetching market price for PLTR...")
    price = await trader.get_market_price("PLTR")
    print(f"✓ Current price: ${price}")
    
    # Test 3: Fetch positions
    print("\n[TEST 3] Fetching positions...")
    positions = await trader.fetch_positions()
    print(f"✓ Positions:")
    print(f"  Symbol: {positions['symbol']}")
    print(f"  Quantity: {positions['quantity']}")
    print(f"  Avg Cost: ${positions['avg_cost']:.2f}")
    print(f"  Market Value: ${positions['market_value']:.2f}")
    print(f"  Unrealized P&L: ${positions['unrealized_pnl']:.2f} ({positions['unrealized_pnl_pct']:.2f}%)")
    
    # Test 4: Fetch account value
    print("\n[TEST 4] Fetching account value...")
    account = await trader.fetch_account_value()
    print(f"✓ Account:")
    print(f"  Cash: ${account['cash']:.2f}")
    print(f"  Buying Power: ${account['buying_power']:.2f}")
    print(f"  Total Value: ${account['total_value']:.2f}")
    
    # Test 5: Market status
    print("\n[TEST 5] Checking market status...")
    status = trader.get_market_status()
    can_trade = trader.can_trade_now()
    time_until_open = trader.get_time_until_market_opens()
    print(f"✓ Market Status: {status}")
    print(f"  Can trade now: {can_trade}")
    if time_until_open:
      print(f"  Time until open: {time_until_open}")
    
    # Test 6: Fetch open orders
    print("\n[TEST 6] Fetching open orders...")
    orders = await trader.fetch_open_orders()
    print(f"✓ Open orders: {len(orders)}")
    for order in orders:
      print(f"  Order #{order['order_id']}: {order['side'].upper()} {order['quantity']} @ {order['status']}")
    
    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)
    
  except Exception as e:
    print(f"\n✗ Error during testing: {e}")
    import traceback
    traceback.print_exc()
  
  finally:
    # Cleanup
    print("\nClosing connection...")
    await trader.close()
    print("✓ Connection closed")


if __name__ == "__main__":
  asyncio.run(main())
