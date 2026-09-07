"""
Reserving the exchange fee out of a quote-denominated order.

Exchanges that charge the taker fee *on top of* the order require
`balance >= cost * (1 + taker)`, so `percentage: 100` can never succeed — the request is
arithmetically impossible, not merely tight.

Observed on Independent Reserve: a 43.44 SGD balance produced an order costing exactly
43.440000 SGD, and IR refused it with

    {"ErrorCode":"ValidationError","Message":"Available SGD balance is too small"}

which reads as though the balance were the problem when in fact the order was 0.5% too
large. CCXT reports `taker: 0.005` for that market, so the fee needed 0.2172 SGD that
had already been spent.
"""

import types

import pytest

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader

IR_TAKER = 0.005          # what ccxt reports for independentreserve
BALANCE = 43.44           # the balance from the incident


class _Recorder:
  def __init__(self):
    self.messages = []

  def __getattr__(self, _name):
    return lambda msg="", *a, **k: self.messages.append(str(msg))


def reserve(cost, available=BALANCE, taker=IR_TAKER, quote="SGD"):
  holder = types.SimpleNamespace(
    logger=_Recorder(), DEFAULT_TAKER_FEE=BaseCryptoTrader.DEFAULT_TAKER_FEE)
  market = {} if taker is None else {'taker': taker}
  trimmed = BaseCryptoTrader._reserve_fee_headroom(
    holder, cost, available, quote, market)
  return trimmed, holder.logger


class TestTheIncident:
  def test_spending_the_whole_balance_is_trimmed(self):
    """The exact case that failed: 100% of 43.44 at a 0.5% fee."""
    trimmed, _ = reserve(BALANCE)
    assert trimmed < BALANCE
    assert trimmed == pytest.approx(BALANCE / (1 + IR_TAKER))

  def test_the_trimmed_order_plus_its_fee_fits_the_balance(self):
    """The property that matters — anything else is still refused by the exchange."""
    trimmed, _ = reserve(BALANCE)
    assert trimmed * (1 + IR_TAKER) <= BALANCE + 1e-9

  def test_the_reserved_amount_matches_the_fee(self):
    trimmed, _ = reserve(BALANCE)
    assert BALANCE - trimmed == pytest.approx(0.2161, abs=1e-3)

  def test_the_adjustment_is_logged(self):
    """Surface it — the number in the log must match what was sent."""
    _, logger = reserve(BALANCE)
    said = " ".join(logger.messages)
    assert "Reserving" in said and "taker fee" in said
    assert "43.44" in said


class TestOrdersThatAlreadyFitAreUntouched:
  @pytest.mark.parametrize("pct", [0.1, 0.5, 0.9, 0.99])
  def test_a_partial_order_is_not_trimmed(self, pct):
    cost = BALANCE * pct
    trimmed, logger = reserve(cost)
    assert trimmed == cost
    assert logger.messages == [], "must not log when nothing changed"

  def test_the_largest_order_that_fits_is_left_alone(self):
    """Exactly at the boundary — trimming here would shrink a valid order."""
    cost = BALANCE / (1 + IR_TAKER)
    trimmed, _ = reserve(cost)
    assert trimmed == cost

  def test_a_cash_amount_well_under_the_balance_is_untouched(self):
    trimmed, _ = reserve(10.0)
    assert trimmed == 10.0


class TestFeeSources:
  def test_the_exchange_reported_fee_is_used(self):
    """Not a hardcoded guess — different venues charge differently."""
    trimmed, _ = reserve(BALANCE, taker=0.001)
    assert trimmed == pytest.approx(BALANCE / 1.001)

  def test_a_zero_fee_exchange_still_fits(self):
    trimmed, _ = reserve(BALANCE, taker=0.0)
    assert trimmed == BALANCE

  def test_a_missing_fee_falls_back_to_the_default(self):
    """Overshooting the reserve costs a fraction of one order; undershooting costs all of it."""
    trimmed, _ = reserve(BALANCE, taker=None)
    assert trimmed == pytest.approx(BALANCE / (1 + BaseCryptoTrader.DEFAULT_TAKER_FEE))

  def test_the_default_is_generous_enough_for_common_venues(self):
    """0.5% IR, 0.6% Coinbase taker, 0.26% Kraken — all under the default."""
    assert BaseCryptoTrader.DEFAULT_TAKER_FEE >= 0.006


class TestBothSizingPathsUseIt:
  """Cash mode has the same hole: spend_amount == balance passes the guard, then fails."""

  def _source(self):
    from pathlib import Path
    import src.traders.crypto.base_crypto_trader as mod
    return Path(mod.__file__).read_text(encoding="utf-8")

  def test_the_percentage_path_reserves_headroom(self):
    src = self._source()
    block = src[src.index("### SPEND PERCENTAGE MODE ###"):]
    assert "_reserve_fee_headroom(" in block[:block.index("elif side == 'sell'")]

  def test_the_cash_path_reserves_headroom(self):
    src = self._source()
    assert src.count("_reserve_fee_headroom(") >= 3, (
      "expected the definition plus both the cash and percentage call sites"
    )

  def test_neither_path_still_assigns_the_raw_cost(self):
    src = self._source()
    assert "spend_cost = available_quote * spend_percentage" not in src
    assert "\n          spend_cost = spend_amount\n" not in src
