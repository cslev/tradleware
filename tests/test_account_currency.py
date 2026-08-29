"""
Account currency for stock bots.

IB reports TotalCashValue once per currency an account holds. The lookup used to match
`'USD'` literally at four sites, so a EUR account read a balance of zero and every order
was sized against nothing — the failure looks identical to an empty account.

`account_currency` (bot config, default 'USD') now names the row to use, and the lookup
lives in one place so the four copies cannot drift.
"""

import types
from collections import deque

import pytest

from src.traders.stock.base_stock_trader import BaseStockTrader
from src.traders.stock.ibkr_trader import IBKRTrader


def summary_row(account, tag, currency, value):
  """One row shaped like ib_async's AccountValue."""
  return types.SimpleNamespace(account=account, tag=tag, currency=currency, value=str(value))


class _Recorder:
  """Captures log calls without needing a real CustomLogger."""

  def __init__(self):
    self.warnings = []
    self.messages = []

  def warning(self, msg, *a, **k):
    self.warnings.append(str(msg))
    self.messages.append(str(msg))

  def __getattr__(self, _name):
    return lambda msg="", *a, **k: self.messages.append(str(msg))


def make_ibkr(account_currency=None, rows=()):
  """An IBKRTrader with only the attributes _fetch_cash_balance touches."""
  trader = IBKRTrader.__new__(IBKRTrader)
  trader.account_id = "DU123456"
  trader.account_currency = account_currency or "USD"
  trader.logger = _Recorder()
  trader.ib = types.SimpleNamespace(
    accountSummaryAsync=lambda: _returns(list(rows))
  )
  return trader


async def _returns(value):
  return value


class TestConfigDefault:
  def test_it_defaults_to_usd(self):
    """Every config written before this setting existed assumed USD."""
    assert BaseStockTrader.__init__.__doc__ is not None
    cfg = _stock_config()
    trader = _bare_stock(cfg)
    assert trader.account_currency == "USD"

  def test_a_configured_currency_is_used(self):
    trader = _bare_stock(_stock_config(account_currency="EUR"))
    assert trader.account_currency == "EUR"

  def test_it_is_normalised_to_upper_case(self):
    """YAML is hand-written; 'eur' must not silently fail to match IB's 'EUR'."""
    trader = _bare_stock(_stock_config(account_currency="eur"))
    assert trader.account_currency == "EUR"


def _stock_config(**overrides):
  cfg = {
    "id": "etfbot",
    "broker": "ibkr",
    "symbol": "vwce",
    "tradleware_api_key": "tw_live_x",
  }
  cfg.update(overrides)
  return cfg


def _bare_stock(cfg):
  """Instantiate the abstract base far enough to read its config parsing."""

  class _Concrete(BaseStockTrader):
    async def connect(self): ...
    async def disconnect(self): ...
    async def fetch_positions(self): ...
    async def fetch_account_value(self): ...
    async def get_market_price(self, symbol=None): ...
    async def create_order(self, **kwargs): ...
    async def cancel_order(self, order_id): ...
    async def fetch_open_orders(self): ...

  return _Concrete(cfg, logger=_Recorder())


class TestCashLookup:
  async def test_it_reads_the_configured_currency(self):
    trader = make_ibkr("EUR", [
      summary_row("DU123456", "TotalCashValue", "USD", 12.34),
      summary_row("DU123456", "TotalCashValue", "EUR", 5000.00),
    ])
    assert await trader._fetch_cash_balance() == pytest.approx(5000.00)

  async def test_it_ignores_other_currencies(self):
    """The bug: a EUR account matched 'USD' and read 12.34 instead of 5000."""
    trader = make_ibkr("USD", [
      summary_row("DU123456", "TotalCashValue", "USD", 12.34),
      summary_row("DU123456", "TotalCashValue", "EUR", 5000.00),
    ])
    assert await trader._fetch_cash_balance() == pytest.approx(12.34)

  async def test_it_ignores_other_accounts(self):
    """A gateway serving several accounts reports all of them."""
    trader = make_ibkr("USD", [
      summary_row("DU999999", "TotalCashValue", "USD", 99999.00),
      summary_row("DU123456", "TotalCashValue", "USD", 250.00),
    ])
    assert await trader._fetch_cash_balance() == pytest.approx(250.00)

  async def test_it_ignores_other_tags(self):
    trader = make_ibkr("USD", [
      summary_row("DU123456", "NetLiquidation", "USD", 88888.00),
      summary_row("DU123456", "TotalCashValue", "USD", 250.00),
    ])
    assert await trader._fetch_cash_balance() == pytest.approx(250.00)

  async def test_a_missing_currency_says_which_ones_exist(self):
    """
    Zero is indistinguishable from an empty account, so the warning has to name the
    currencies IB actually returned — that is the only clue a typo is the cause.
    """
    trader = make_ibkr("EURO", [        # typo for EUR
      summary_row("DU123456", "TotalCashValue", "EUR", 5000.00),
      summary_row("DU123456", "TotalCashValue", "USD", 12.34),
    ])
    assert await trader._fetch_cash_balance() == 0.0
    warning = " ".join(trader.logger.warnings)
    assert "EURO" in warning
    assert "EUR" in warning and "USD" in warning
    assert "account_currency" in warning

  async def test_an_empty_summary_warns_without_claiming_currencies(self):
    trader = make_ibkr("USD", [])
    assert await trader._fetch_cash_balance() == 0.0
    assert "no TotalCashValue" in " ".join(trader.logger.warnings)

  async def test_gateway_errors_propagate(self):
    """
    Callers decide whether a failed lookup is fatal — it is when placing a live order,
    not when refreshing a card. Swallowing it here would size an order against 0.
    """
    def boom():
      raise ConnectionError("not connected")

    trader = make_ibkr("USD")
    trader.ib = types.SimpleNamespace(accountSummaryAsync=boom)
    with pytest.raises(ConnectionError):
      await trader._fetch_cash_balance()


class TestSizingMessages:
  def test_cash_figures_name_the_currency_not_a_dollar_sign(self):
    """A EUR bot printing '$5000.00' misreports the account it just read."""
    trader = _bare_stock(_stock_config(account_currency="EUR"))
    trader.log_buffer = deque(maxlen=50)
    ctx = {"cash_available": 1000.0, "current_price": 110.0}

    trader._calculate_order_size("buy", 1.0, ctx, fractional_shares=False)

    sized = [m for m in trader.logger.messages if "Buy order sizing" in m]
    assert sized, trader.logger.messages
    assert "EUR" in sized[0]
    assert "$" not in sized[0]

  def test_the_zero_quantity_error_also_names_the_currency(self):
    trader = _bare_stock(_stock_config(account_currency="EUR"))
    trader.log_buffer = deque(maxlen=50)
    ctx = {"cash_available": 100.0, "current_price": 450.0}

    with pytest.raises(ValueError) as exc:
      trader._calculate_order_size("buy", 1.0, ctx, fractional_shares=False)

    assert "EUR" in str(exc.value)
    assert "$" not in str(exc.value)
