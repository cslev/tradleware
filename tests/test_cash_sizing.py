"""
Cash-denominated order sizing — `order_size_type: "cash"`.

A signal can now say "invest 300" instead of a percentage or a share count, which is
what monthly DCA into an ETF actually wants. Buy only: a cash-denominated sell changes
meaning as price moves and cannot reliably express "close the position", which
`percentage: 100` already does.

Two things are easy to get wrong and are pinned below:

* Rounding. `round(2.72727, 4)` is 2.7273, which at 110 costs 300.003 — over the budget
  it was sized against, and rejected outright when spending a full balance. Cash mode
  floors.
* The residue. Whole shares rarely consume the whole budget, and unlike percentage mode
  it does not self-correct: the order is pinned to the amount and never reads the
  balance, so the remainder accumulates rather than being deployed. It is truncated and
  logged, never silently absorbed.
"""

import types

import pytest

from src.traders.stock.base_stock_trader import BaseStockTrader


class _Recorder:
  def __init__(self):
    self.messages = []
    self.warnings = []

  def warning(self, msg, *a, **k):
    self.warnings.append(str(msg))
    self.messages.append(str(msg))

  def __getattr__(self, _name):
    return lambda msg="", *a, **k: self.messages.append(str(msg))


def size(side="buy", cash=None, shares=None, price=100.0, currency="USD",
         fractional=False, **kwargs):
  """Call the real _calculate_order_size against a minimal holder."""
  holder = types.SimpleNamespace(
    account_currency=currency, logger=_Recorder(),
    MIN_SPEND_PERCENTAGE=0.0, MAX_SPEND_PERCENTAGE=1.0)
  ctx = {"current_price": price}
  if cash is not None:
    ctx["cash_available"] = cash
  if shares is not None:
    ctx["shares_owned"] = shares
  quantity = BaseStockTrader._calculate_order_size(
    holder, side, kwargs.pop("spend_percentage", None), ctx,
    fractional_shares=fractional, **kwargs)
  return quantity, holder.logger


class TestWholeShares:
  def test_a_clean_amount_buys_the_expected_count(self):
    quantity, _ = size(cash=1000.0, price=100.0, spend_amount=300.0)
    assert quantity == 3

  def test_it_truncates_rather_than_overspending(self):
    """300 at 110 is 2.72 shares — 2, not 3. Three would cost 330."""
    quantity, _ = size(cash=1000.0, price=110.0, spend_amount=300.0)
    assert quantity == 2

  def test_the_shortfall_is_logged(self):
    """
    Silence here is how a DCA quietly under-deploys for a year: 300 buys 220 of ETF and
    strands 80 every month, and in cash mode that never catches up.
    """
    _, logger = size(cash=1000.0, price=110.0, spend_amount=300.0)
    assert any("220.00 of 300.00" in w for w in logger.warnings), logger.warnings
    assert any("not deployed" in w for w in logger.warnings)

  def test_an_exact_fit_does_not_warn(self):
    _, logger = size(cash=1000.0, price=100.0, spend_amount=300.0)
    assert not any("not deployed" in w for w in logger.warnings)

  def test_an_amount_below_one_share_is_refused(self):
    """100 into a 450 share truncates to zero — fail loudly, never order nothing."""
    with pytest.raises(ValueError, match="Calculated quantity is 0"):
      size(cash=1000.0, price=450.0, spend_amount=100.0)


class TestFractionalShares:
  def test_it_spends_the_whole_amount(self):
    quantity, _ = size(cash=1000.0, price=110.0, spend_amount=300.0, fractional=True)
    assert quantity == pytest.approx(2.7272, abs=1e-9)

  def test_it_floors_rather_than_rounding(self):
    """
    round(2.72727, 4) = 2.7273, which costs 300.003 — over budget. Floor gives 2.7272.
    """
    quantity, _ = size(cash=300.0, price=110.0, spend_amount=300.0, fractional=True)
    assert quantity * 110.0 <= 300.0, "sized order must never exceed the amount requested"

  def test_a_full_balance_order_stays_within_the_balance(self):
    """The case rounding up breaks: the broker rejects it for insufficient cash."""
    quantity, _ = size(cash=1000.0, price=3.0, spend_amount=1000.0, fractional=True)
    assert quantity * 3.0 <= 1000.0

  def test_no_residue_warning_when_the_amount_is_fully_deployed(self):
    _, logger = size(cash=1000.0, price=110.0, spend_amount=300.0, fractional=True)
    assert not any("not deployed" in w for w in logger.warnings)


class TestGuards:
  def test_spending_more_than_the_balance_is_refused(self):
    with pytest.raises(ValueError, match="Insufficient cash"):
      size(cash=200.0, price=100.0, spend_amount=500.0)

  def test_spending_exactly_the_balance_is_allowed(self):
    quantity, _ = size(cash=500.0, price=100.0, spend_amount=500.0)
    assert quantity == 5

  def test_a_cash_sell_is_refused(self):
    """Buy-only by design — percentage 100 is how a position gets closed."""
    with pytest.raises(ValueError, match="buy-only"):
      size(side="sell", shares=100, spend_amount=300.0)

  def test_the_sell_error_names_the_alternative(self):
    with pytest.raises(ValueError) as exc:
      size(side="sell", shares=100, spend_amount=300.0)
    assert "percentage" in str(exc.value) and "quantity" in str(exc.value)

  def test_errors_name_the_account_currency(self):
    with pytest.raises(ValueError) as exc:
      size(cash=200.0, price=100.0, spend_amount=500.0, currency="EUR")
    assert "EUR" in str(exc.value) and "$" not in str(exc.value)


class TestPercentageModeIsUnchanged:
  """The existing mode must behave identically — it shares the branch."""

  def test_percentage_still_sizes_from_the_balance(self):
    quantity, _ = size(cash=1000.0, price=100.0, spend_percentage=0.5)
    assert quantity == 5

  def test_percentage_still_reports_its_basis(self):
    _, logger = size(cash=1000.0, price=100.0, spend_percentage=0.5)
    assert any("50.0% of 1000.00" in m for m in logger.messages), logger.messages

  def test_percentage_never_warns_about_residue(self):
    """Percentage self-corrects — a bigger balance simply buys more next time."""
    _, logger = size(cash=1000.0, price=110.0, spend_percentage=0.3)
    assert not any("not deployed" in w for w in logger.warnings)

  def test_percentage_sells_still_work(self):
    quantity, _ = size(side="sell", shares=100, spend_percentage=0.25)
    assert quantity == 25


class TestValidator:
  def _validate(self, **kwargs):
    holder = types.SimpleNamespace(
      account_currency="USD", logger=_Recorder(),
      VALID_ORDER_SIDES=['buy', 'sell'], VALID_ORDER_TYPES=['market', 'maker_limit'],
      MIN_SPEND_PERCENTAGE=0.0, MAX_SPEND_PERCENTAGE=1.0)
    return BaseStockTrader._validate_order_params(holder, kwargs.pop("side", "buy"), **kwargs)

  def test_a_cash_amount_alone_is_accepted(self):
    self._validate(spend_amount=300.0)

  @pytest.mark.parametrize("pair", [
    {"spend_amount": 300.0, "spend_percentage": 0.5},
    {"spend_amount": 300.0, "quantity": 5},
    {"spend_percentage": 0.5, "quantity": 5},
  ])
  def test_two_modes_at_once_are_refused(self, pair):
    with pytest.raises(ValueError, match="more than one"):
      self._validate(**pair)

  def test_all_three_at_once_are_refused(self):
    with pytest.raises(ValueError, match="more than one"):
      self._validate(spend_amount=300.0, spend_percentage=0.5, quantity=5)

  def test_no_mode_at_all_is_refused(self):
    with pytest.raises(ValueError, match="exactly one"):
      self._validate()

  @pytest.mark.parametrize("bad", [0, -1, -300.0])
  def test_a_non_positive_amount_is_refused(self, bad):
    with pytest.raises(ValueError, match="must be positive"):
      self._validate(spend_amount=bad)


class TestWebhookAcceptsCash:
  """
  End-to-end through the real webhook handler.

  The unit tests above would all pass with `cash` missing from the allow-list, or with
  the branch chain still using `else` as the quantity handler — in which case
  `{"order_size_type": "cash", "order_size": 300}` would execute as 300 shares.
  """

  async def test_a_cash_signal_reaches_the_trader_as_spend_amount(
      self, client_factory, webhook_url, stock_trader):
    from conftest import signal_payload
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key,
        trader_id="fakestock", ticker=stock_trader.symbol,
        order_size=300, order_size_type="cash", dry_run=False))

    assert stock_trader.orders, "no order reached the trader"
    order = stock_trader.orders[0]
    assert order.get("spend_amount") == 300.0
    assert order.get("quantity") is None, "cash must not arrive as a share count"
    assert order.get("spend_percentage") is None

  async def test_cash_is_not_silently_treated_as_a_share_count(
      self, client_factory, webhook_url, stock_trader):
    """The `else`-fall-through regression, stated as its own assertion."""
    from conftest import signal_payload
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key,
        trader_id="fakestock", ticker=stock_trader.symbol,
        order_size=300, order_size_type="cash", dry_run=False))
    assert stock_trader.orders[0].get("quantity") != 300

  @pytest.mark.parametrize("bad", [0, -5])
  async def test_a_non_positive_cash_amount_is_rejected(
      self, client_factory, webhook_url, stock_trader, bad):
    from conftest import signal_payload
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key,
        trader_id="fakestock", ticker=stock_trader.symbol,
        order_size=bad, order_size_type="cash"))
    assert response.status_code == 400
    assert stock_trader.orders == []

  async def test_an_unknown_order_size_type_is_still_rejected(
      self, client_factory, webhook_url, stock_trader):
    from conftest import signal_payload
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key,
        trader_id="fakestock", ticker=stock_trader.symbol,
        order_size=10, order_size_type="notional"))
    assert response.status_code == 400
    assert stock_trader.orders == []

  async def test_the_existing_modes_still_work(self, client_factory, webhook_url,
                                               stock_trader):
    from conftest import signal_payload
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key,
        trader_id="fakestock", ticker=stock_trader.symbol,
        order_size=50, order_size_type="percentage", dry_run=False))
    assert stock_trader.orders[0].get("spend_percentage") == 0.5
    assert stock_trader.orders[0].get("spend_amount") is None

  async def test_a_crypto_bot_accepts_cash_sizing(self, client_factory, webhook_url,
                                                  crypto_trader):
    """
    Cash sizing is a webhook-API field, not a stock feature — the same payload must work
    whichever broker family the bot belongs to. Crypto was refused by an explicit
    stopgap until the traders were wired up; that stopgap is gone.
    """
    from conftest import signal_payload
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(
        order_size=300, order_size_type="cash", dry_run=False))

    assert response.status_code == 200, response.text
    assert crypto_trader.orders, "no order reached the crypto trader"
    order = crypto_trader.orders[0]
    assert order.get("spend_amount") == 300.0
    assert order.get("quantity") is None
    assert order.get("spend_percentage") is None

  async def test_the_same_payload_works_for_both_broker_families(
      self, client_factory, webhook_url, crypto_trader, stock_trader):
    """Broker-transparent signals: only api_key/trader_id/ticker differ."""
    from conftest import signal_payload
    sizing = {"order_size": 300, "order_size_type": "cash", "dry_run": False}

    async with client_factory() as client:
      crypto = await client.post(webhook_url, json=signal_payload(**sizing))
      stock = await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key, trader_id="fakestock",
        ticker=stock_trader.symbol, **sizing))

    assert (crypto.status_code, stock.status_code) == (200, 200)
    assert crypto_trader.orders[0]["spend_amount"] == 300.0
    assert stock_trader.orders[0]["spend_amount"] == 300.0


class TestResidueWarningIsMaterial:
  """
  The warning exists to surface cash that is not being deployed. A penny of floor
  rounding is not that, and a warning on every order is how the real ones stop being
  read — the earlier fixed 0.01 threshold fired on ordinary fractional buys.
  """

  def test_fractional_rounding_does_not_warn(self):
    """300 at 165 floors to 1.8181 shares — 1.3 cents short, and nothing to do about it."""
    _, logger = size(cash=5000.0, price=165.0, spend_amount=300.0, fractional=True)
    assert logger.warnings == [], logger.warnings

  @pytest.mark.parametrize("price", [7.0, 165.0, 999.99])
  def test_no_fractional_price_produces_a_warning(self, price):
    """Whatever the price, fractional sizing deploys essentially all of it."""
    _, logger = size(cash=100_000.0, price=price, spend_amount=300.0, fractional=True)
    assert logger.warnings == [], f"price {price}: {logger.warnings}"

  def test_a_material_whole_share_shortfall_still_warns(self):
    """300 at 110 strands 80 — over a quarter of the budget."""
    _, logger = size(cash=5000.0, price=110.0, spend_amount=300.0)
    assert any("80.00 not deployed" in w for w in logger.warnings), logger.warnings

  def test_the_whole_share_warning_names_the_remedy(self):
    _, logger = size(cash=5000.0, price=110.0, spend_amount=300.0)
    assert any("fractional_shares" in w for w in logger.warnings)

  def test_a_trivial_whole_share_shortfall_stays_quiet(self):
    """300 at 3 buys 100 shares exactly — nothing stranded, nothing to say."""
    _, logger = size(cash=5000.0, price=3.0, spend_amount=300.0)
    assert logger.warnings == []


class TestExecutionLogging:
  """
  The handler logs what it is about to do before calling the trader. Those lines were
  `if percentage / else`, with the else assuming a share count — so a cash order printed
  "None DOGE", and on a stock bot without fractional shares the else called int(None)
  and raised before the order was ever placed.
  """

  async def test_a_crypto_cash_buy_logs_the_amount_not_none(
      self, client_factory, webhook_url, crypto_trader, caplog):
    import logging
    from conftest import signal_payload
    caplog.set_level(logging.INFO, logger="FakeCryptoTrader")

    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(
        order_size=25, order_size_type="cash", dry_run=False))

    executing = [r.message for r in caplog.records if "Executing BUY order" in r.message]
    assert executing, [r.message for r in caplog.records]
    assert "None" not in executing[0], executing[0]
    assert "25.00" in executing[0]

  async def test_a_stock_cash_buy_does_not_crash_without_fractional_shares(
      self, client_factory, webhook_url, stock_trader):
    """
    int(None) raised a TypeError here. It only stayed hidden because the bot under test
    had fractional_shares enabled.
    """
    from conftest import signal_payload
    stock_trader.fractional_shares = False
    try:
      async with client_factory() as client:
        response = await client.post(webhook_url, json=signal_payload(
          api_key=stock_trader.tradleware_api_key, trader_id="fakestock",
          ticker=stock_trader.symbol,
          order_size=300, order_size_type="cash", dry_run=False))
      assert response.status_code == 200, response.text
      assert stock_trader.orders[0]["spend_amount"] == 300.0
    finally:
      stock_trader.fractional_shares = True

  async def test_a_stock_cash_buy_logs_the_amount(self, client_factory, webhook_url,
                                                  stock_trader, caplog):
    import logging
    from conftest import signal_payload
    caplog.set_level(logging.INFO, logger="FakeStockTrader")

    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(
        api_key=stock_trader.tradleware_api_key, trader_id="fakestock",
        ticker=stock_trader.symbol,
        order_size=300, order_size_type="cash", dry_run=False))

    executing = [r.message for r in caplog.records if "Executing BUY order" in r.message]
    assert executing, [r.message for r in caplog.records]
    assert "None" not in executing[0], executing[0]

  async def test_quantity_mode_logging_is_unchanged(self, client_factory, webhook_url,
                                                    crypto_trader, caplog):
    import logging
    from conftest import signal_payload
    caplog.set_level(logging.INFO, logger="FakeCryptoTrader")

    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(
        order_size=5, order_size_type="quantity", dry_run=False))

    executing = [r.message for r in caplog.records if "Executing BUY order" in r.message]
    assert executing and "5" in executing[0] and "BTC" in executing[0]
