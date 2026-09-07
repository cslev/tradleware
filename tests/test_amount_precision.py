"""
Rounding order amounts when the exchange publishes no precision.

CCXT reports `precision: {'amount': None}` for Independent Reserve, so
`amount_to_precision` raises `AssertionError: precision should not be None`. The old
fallback returned the raw float and swallowed the reason, which is how an order went out
as 0.32264221993296116 SOL — seventeen decimals, more than any venue accepts.

IR happened to accept that one and reject it for an unrelated reason (the fee), so the
precision problem stayed invisible. It is fixed here on its own terms.
"""

import math
import types
from pathlib import Path

import pytest

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader

CRYPTO_DIR = Path(BaseCryptoTrader.__module__.replace('.', '/')).parent
UNROUNDED = 0.32264221993296116     # the amount from the incident


class _Recorder:
  def __init__(self):
    self.messages = []

  def __getattr__(self, _name):
    return lambda msg="", *a, **k: self.messages.append(str(msg))


def precise(amount, exchange_result=None, raises=None):
  """Call the real helper against an exchange that either works or does not."""
  def to_precision(_symbol, value):
    if raises:
      raise raises
    return exchange_result if exchange_result is not None else value

  holder = types.SimpleNamespace(
    exchange=types.SimpleNamespace(amount_to_precision=to_precision),
    logger=_Recorder(),
    FALLBACK_AMOUNT_DECIMALS=BaseCryptoTrader.FALLBACK_AMOUNT_DECIMALS,
  )
  return BaseCryptoTrader._safe_amount_to_precision(holder, 'SOL/SGD', amount), holder.logger


class TestExchangeProvidesPrecision:
  def test_the_exchange_value_is_used(self):
    result, _ = precise(UNROUNDED, exchange_result='0.3226')
    assert result == 0.3226

  def test_nothing_is_logged_on_the_normal_path(self):
    _, logger = precise(UNROUNDED, exchange_result='0.3226')
    assert logger.messages == []

  def test_the_result_is_a_float_not_a_string(self):
    """ccxt returns a string; passing that to an order builder is a different bug."""
    result, _ = precise(UNROUNDED, exchange_result='0.3226')
    assert isinstance(result, float)


class TestExchangePublishesNoPrecision:
  """The Independent Reserve case."""

  IR_ERROR = AssertionError("precision should not be None")

  def test_the_raw_float_is_never_returned(self):
    """The whole bug: seventeen decimals reaching the exchange."""
    result, _ = precise(UNROUNDED, raises=self.IR_ERROR)
    assert result != UNROUNDED
    assert len(str(result).split('.')[-1]) <= BaseCryptoTrader.FALLBACK_AMOUNT_DECIMALS

  def test_it_rounds_to_the_fallback_precision(self):
    result, _ = precise(UNROUNDED, raises=self.IR_ERROR)
    assert result == pytest.approx(0.32264221, abs=1e-12)

  def test_it_rounds_down_never_up(self):
    """
    Rounding up can push a balance-consuming order past what is available — the same
    failure _reserve_fee_headroom exists to prevent.
    """
    result, _ = precise(UNROUNDED, raises=self.IR_ERROR)
    assert result <= UNROUNDED

  @pytest.mark.parametrize("amount", [0.1, 1.0, 21.0, 0.004, 1234.56789012345])
  def test_rounding_down_holds_for_any_amount(self, amount):
    result, _ = precise(amount, raises=self.IR_ERROR)
    assert result <= amount

  def test_an_already_short_amount_is_unchanged(self):
    result, _ = precise(0.5, raises=self.IR_ERROR)
    assert result == 0.5

  def test_the_reason_is_logged_rather_than_swallowed(self):
    """A bare `except` is what kept this invisible for months."""
    _, logger = precise(UNROUNDED, raises=self.IR_ERROR)
    said = " ".join(logger.messages)
    assert "AssertionError" in said
    assert "precision should not be None" in said
    assert "SOL/SGD" in said

  @pytest.mark.parametrize("error", [
    AssertionError("precision should not be None"),
    TypeError("unsupported operand"),
    AttributeError("no attribute 'markets'"),
    KeyError("SOL/SGD"),
  ])
  def test_any_failure_mode_still_yields_a_usable_amount(self, error):
    result, _ = precise(UNROUNDED, raises=error)
    assert isinstance(result, float) and result > 0


class TestEveryTraderUsesTheHelper:
  """
  IR bypassed the shared helper with its own try/except, which is how it kept the raw
  float. The others called `exchange.amount_to_precision` unguarded — fine on venues
  that publish precision, and a crash on any that do not.
  """

  def _traders(self):
    import src.traders.crypto.base_crypto_trader as mod
    d = Path(mod.__file__).parent
    return sorted(p for p in d.glob('*_trader.py') if p.name != 'base_crypto_trader.py')

  def test_no_trader_calls_the_exchange_directly(self):
    offenders = [p.name for p in self._traders()
                 if "self.exchange.amount_to_precision(" in p.read_text(encoding='utf-8')]
    assert offenders == [], offenders

  def test_no_trader_keeps_its_own_silent_fallback(self):
    """`except Exception: pass` around precision is what hid the reason."""
    for p in self._traders():
      text = p.read_text(encoding='utf-8')
      assert "amount_to_precision(symbol, base_amount)\n      except" not in text, p.name

  def test_the_base_class_is_the_only_place_that_calls_ccxt(self):
    import src.traders.crypto.base_crypto_trader as mod
    text = Path(mod.__file__).read_text(encoding='utf-8')
    assert text.count("self.exchange.amount_to_precision(") == 1
