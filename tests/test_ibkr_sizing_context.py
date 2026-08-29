"""
IBKR Layer 3 — fetching the balance an order is sized from.

Layer 3 used to braid two independent questions into one if/elif chain: "do we need a
balance?" (quantity mode does not; percentage does) and "is this a dry run?" (which only
decides what happens when the gateway is unreachable). Because they were braided, the
same fetch was written twice and every edit had to be made in both copies — including
the account-currency fix, which had to touch four sites.

`_fetch_sizing_context` answers only the second question. These tests pin every path
through the old code so the split cannot change behaviour, and cover the two places it
deliberately does.
"""

import types

import pytest

from src.traders.stock.ibkr_trader import IBKRTrader


class _Recorder:
  def __init__(self):
    self.messages = []
    self.warnings = []

  def warning(self, msg, *a, **k):
    self.warnings.append(str(msg))
    self.messages.append(str(msg))

  def __getattr__(self, _name):
    return lambda msg="", *a, **k: self.messages.append(str(msg))


def make_trader(cash=None, shares=None, cash_error=None, position_error=None):
  """A trader whose balance lookups return or raise exactly what a test needs."""
  trader = IBKRTrader.__new__(IBKRTrader)
  trader.account_id = "DU123456"
  trader.account_currency = "USD"
  trader.fractional_shares = False
  trader.is_connected = True
  trader.logger = _Recorder()
  trader.ib = types.SimpleNamespace()

  async def fetch_cash():
    if cash_error:
      raise cash_error
    return cash

  async def fetch_positions():
    if position_error:
      raise position_error
    return {"quantity": shares}

  trader._fetch_cash_balance = fetch_cash
  trader.fetch_positions = fetch_positions
  return trader


class TestHappyPaths:
  async def test_a_buy_reads_cash(self):
    trader = make_trader(cash=5000.0)
    ctx = await trader._fetch_sizing_context("buy", {})
    assert ctx["cash_available"] == 5000.0
    assert "shares_owned" not in ctx

  async def test_a_sell_reads_the_position(self):
    trader = make_trader(shares=42)
    ctx = await trader._fetch_sizing_context("sell", {})
    assert ctx["shares_owned"] == 42
    assert "cash_available" not in ctx

  async def test_the_existing_context_is_preserved(self):
    """Layer 2 already put market status and price in there."""
    trader = make_trader(cash=100.0)
    ctx = await trader._fetch_sizing_context("buy", {"current_price": 110.0})
    assert ctx["current_price"] == 110.0
    assert ctx["cash_available"] == 100.0

  async def test_a_missing_position_quantity_reads_as_zero(self):
    """fetch_positions omits 'quantity' when there is no position."""
    trader = make_trader()
    trader.fetch_positions = lambda: _returns({})
    ctx = await trader._fetch_sizing_context("sell", {})
    assert ctx["shares_owned"] == 0


async def _returns(value):
  return value


class TestLiveFailsClosed:
  """
  A live order must never size against a balance nobody managed to read. Falling back to
  a simulated figure here would place a real order from an invented number.
  """

  async def test_a_failed_cash_fetch_raises(self):
    trader = make_trader(cash_error=ConnectionError("not connected"))
    with pytest.raises(RuntimeError, match="Failed to fetch balance for sizing"):
      await trader._fetch_sizing_context("buy", {}, dry_run=False)

  async def test_a_failed_position_fetch_raises(self):
    """
    The old code only guarded the buy path; a live sell let the raw exception past.
    Both now fail the same way.
    """
    trader = make_trader(position_error=ConnectionError("not connected"))
    with pytest.raises(RuntimeError, match="Failed to fetch balance for sizing"):
      await trader._fetch_sizing_context("sell", {}, dry_run=False)

  async def test_nothing_is_written_into_the_context_on_failure(self):
    trader = make_trader(cash_error=ConnectionError("not connected"))
    ctx = {"current_price": 110.0}
    with pytest.raises(RuntimeError):
      await trader._fetch_sizing_context("buy", ctx, dry_run=False)
    assert "cash_available" not in ctx

  async def test_a_disconnection_updates_the_connection_flag(self):
    """
    _handle_ib_exception flips is_connected so the health loop reconnects. The old code
    called it on the buy path only.
    """
    trader = make_trader(position_error=ConnectionError("not connected"))
    with pytest.raises(RuntimeError):
      await trader._fetch_sizing_context("sell", {}, dry_run=False)
    assert trader.is_connected is False


class TestDryRunFallsBack:
  """A dry run should still show something when the gateway is down."""

  async def test_a_dry_run_prefers_the_real_balance(self):
    trader = make_trader(cash=1234.0)
    ctx = await trader._fetch_sizing_context("buy", {}, dry_run=True)
    assert ctx["cash_available"] == 1234.0
    assert trader.logger.warnings == []

  async def test_a_dry_run_buy_falls_back_when_the_gateway_is_down(self):
    trader = make_trader(cash_error=ConnectionError("not connected"))
    ctx = await trader._fetch_sizing_context("buy", {}, dry_run=True)
    assert ctx["cash_available"] == 10_000.0
    assert any("simulated values" in w for w in trader.logger.warnings)

  async def test_a_dry_run_sell_falls_back_when_the_gateway_is_down(self):
    trader = make_trader(position_error=ConnectionError("not connected"))
    ctx = await trader._fetch_sizing_context("sell", {}, dry_run=True)
    assert ctx["shares_owned"] == 10
    assert any("simulated values" in w for w in trader.logger.warnings)

  async def test_a_dry_run_never_raises_on_a_gateway_failure(self):
    trader = make_trader(cash_error=RuntimeError("gateway exploded"))
    ctx = await trader._fetch_sizing_context("buy", {}, dry_run=True)
    assert ctx["cash_available"] == 10_000.0


class TestReportedUnits:
  async def test_cash_is_reported_in_the_account_currency(self):
    trader = make_trader(cash=5000.0)
    trader.account_currency = "EUR"
    await trader._fetch_sizing_context("buy", {})
    logged = " ".join(trader.logger.messages)
    assert "EUR" in logged and "$" not in logged


class TestExplicitQuantityIsValidated:
  """
  Quantity mode has nothing to size, but plenty to check.

  It used to skip the balance fetch entirely and send the order straight to the broker.
  The crypto traders have always validated both directions in the same situation.
  """

  def _trader(self, cash=None, shares=None, price=100.0):
    from src.traders.stock.base_stock_trader import BaseStockTrader

    holder = types.SimpleNamespace(
      account_currency="USD",
      logger=_Recorder(),
    )
    ctx = {"current_price": price}
    if cash is not None:
      ctx["cash_available"] = cash
    if shares is not None:
      ctx["shares_owned"] = shares
    return BaseStockTrader._validate_explicit_quantity, holder, ctx

  def test_an_affordable_buy_passes(self):
    check, holder, ctx = self._trader(cash=1000.0, price=100.0)
    check(holder, "buy", 5, ctx)          # 500 of 1000

  def test_an_unaffordable_buy_is_refused(self):
    """Previously sent to IBKR and rejected there, with a worse message."""
    check, holder, ctx = self._trader(cash=1000.0, price=100.0)
    with pytest.raises(ValueError, match="Insufficient cash"):
      check(holder, "buy", 20, ctx)       # 2000 of 1000

  def test_a_buy_of_exactly_the_balance_passes(self):
    check, holder, ctx = self._trader(cash=1000.0, price=100.0)
    check(holder, "buy", 10, ctx)

  def test_a_sell_within_the_position_passes(self):
    check, holder, ctx = self._trader(shares=100)
    check(holder, "sell", 40, ctx)

  def test_selling_the_whole_position_passes(self):
    check, holder, ctx = self._trader(shares=100)
    check(holder, "sell", 100, ctx)

  def test_an_oversized_sell_is_refused(self):
    """
    The dangerous one. A margin-enabled account does not reject this — it opens a short
    for the excess, which is the opposite of the intended trade.
    """
    check, holder, ctx = self._trader(shares=10)
    with pytest.raises(ValueError, match="short position"):
      check(holder, "sell", 100, ctx)

  def test_selling_with_no_position_is_refused(self):
    check, holder, ctx = self._trader(shares=0)
    with pytest.raises(ValueError, match="Insufficient position"):
      check(holder, "sell", 1, ctx)

  def test_the_buy_error_names_the_account_currency(self):
    check, holder, ctx = self._trader(cash=100.0, price=450.0)
    holder.account_currency = "EUR"
    with pytest.raises(ValueError) as exc:
      check(holder, "buy", 1, ctx)
    assert "EUR" in str(exc.value) and "$" not in str(exc.value)

  def test_missing_context_is_reported_not_ignored(self):
    check, holder, _ = self._trader()
    with pytest.raises(ValueError, match="missing in context"):
      check(holder, "buy", 1, {"current_price": 100.0})


class TestCreateOrderWiring:
  """
  The helper is only worth anything if create_order calls it.

  Checking _validate_explicit_quantity in isolation is not enough: reverting Layer 3 to
  skip the fetch in quantity mode leaves every unit test above green while sending
  unchecked orders to the broker. These drive the real create_order.
  """

  def _trader(self, cash=None, shares=None, price=100.0):
    trader = make_trader(cash=cash, shares=shares)
    trader.symbol = "AAPL"
    trader.calls = []

    inner_cash, inner_positions = trader._fetch_cash_balance, trader.fetch_positions

    async def record_cash():
      trader.calls.append("cash")
      return await inner_cash()

    async def record_positions():
      trader.calls.append("positions")
      return await inner_positions()

    trader._fetch_cash_balance = record_cash
    trader.fetch_positions = record_positions

    async def resolve(side, dry_run=False):
      return {"can_trade": True, "market_status": "open", "current_price": price,
              "cash_available": None, "shares_owned": None}

    trader._resolve_market_and_balance = resolve
    return trader

  async def test_a_quantity_buy_fetches_the_balance(self):
    trader = self._trader(cash=1000.0)
    await trader.create_order(side="buy", quantity=5, params={"dry_run": True})
    assert trader.calls == ["cash"], "quantity mode must not skip the balance fetch"

  async def test_a_quantity_sell_fetches_the_position(self):
    trader = self._trader(shares=100)
    await trader.create_order(side="sell", quantity=10, params={"dry_run": True})
    assert trader.calls == ["positions"]

  async def test_an_affordable_quantity_buy_still_goes_through(self):
    trader = self._trader(cash=1000.0)
    order = await trader.create_order(side="buy", quantity=5, params={"dry_run": True})
    assert order["quantity"] == 5
    assert order["status"] == "simulated"

  async def test_an_unaffordable_quantity_buy_is_refused_before_the_broker(self):
    trader = self._trader(cash=100.0, price=450.0)
    with pytest.raises(ValueError, match="Insufficient cash"):
      await trader.create_order(side="buy", quantity=10, params={"dry_run": True})

  async def test_an_oversized_quantity_sell_is_refused_before_the_broker(self):
    """The short-position case, driven end to end."""
    trader = self._trader(shares=10)
    with pytest.raises(ValueError, match="short position"):
      await trader.create_order(side="sell", quantity=100, params={"dry_run": True})

  async def test_percentage_mode_is_unaffected(self):
    trader = self._trader(cash=1000.0, price=100.0)
    order = await trader.create_order(side="buy", spend_percentage=0.5,
                                      params={"dry_run": True})
    assert trader.calls == ["cash"]
    assert order["quantity"] == 5      # 50% of 1000 at 100
