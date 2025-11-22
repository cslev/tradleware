"""
Traders module - contains crypto and stock trader implementations.
"""

# Import crypto traders for backward compatibility
from .crypto.okx_trader import OKXTrader
from .crypto.ir_trader import IRTrader
from .crypto.cryptocom_trader import CryptocomTrader

__all__ = ['OKXTrader', 'IRTrader', 'CryptocomTrader']
