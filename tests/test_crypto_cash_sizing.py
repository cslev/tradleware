"""
Cash-denominated sizing for crypto bots.

For crypto, `cash` means the pair's QUOTE currency — USDT for BTC/USDT. Same user-facing
meaning as the stock side ("the currency you pay with"), so one payload works on either
broker family.

The execution path already existed: a percentage market buy returns a quote *cost* from
`_calculate_order_size` and the trader converts it via createMarketBuyOrderWithCost. Cash
mode reuses it with the cost pinned rather than derived — which is why the risk here is
not the arithmetic but the fifteen places each trader asked "is this cost-denominated?"
by testing `spend_percentage is not None`.

`is_cost_denominated()` answers that in one place. The third of its three uses is the
subtle one: it decides whether to skip `_safe_amount_to_precision`, and applying
base-asset precision to a quote cost silently rounds 300 USDT to BTC's eight decimals.
"""

import inspect
import re
from pathlib import Path

import pytest

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader

CRYPTO_DIR = Path(__file__).resolve().parents[1] / "src/traders/crypto"
TRADERS = sorted(p for p in CRYPTO_DIR.glob("*_trader.py")
                 if p.name != "base_crypto_trader.py")


class TestCostDenominationHelper:
  @pytest.mark.parametrize("order_type,side,pct,amount,expected", [
    ('market', 'buy',  0.5,  None, True),    # percentage market buy → quote cost
    ('market', 'buy',  None, 300., True),    # cash market buy       → quote cost
    ('market', 'buy',  None, None, False),   # quantity mode         → base amount
    ('market', 'sell', 0.5,  None, False),   # every sell            → base amount
    ('market', 'sell', None, 300., False),
    ('limit',  'buy',  0.5,  None, False),   # maker_limit already divided by price
    ('limit',  'buy',  None, 300., False),
  ])
  def test_it_identifies_quote_denominated_orders(self, order_type, side, pct,
                                                  amount, expected):
    assert BaseCryptoTrader.is_cost_denominated(order_type, side, pct, amount) is expected

  def test_it_is_static_so_concurrent_signals_cannot_share_state(self):
    """
    One trader instance serves concurrent signals for its bot. Storing this on `self`
    would let one order read a flag another had just set.
    """
    attr = inspect.getattr_static(BaseCryptoTrader, 'is_cost_denominated')
    assert isinstance(attr, staticmethod)


class TestEveryTraderUsesTheHelper:
  """The sweep: no trader may still ask the question inline."""

  @pytest.mark.parametrize("path", TRADERS, ids=lambda p: p.name)
  def test_no_inline_cost_denomination_test_remains(self, path):
    source = path.read_text(encoding="utf-8")
    stragglers = [line.strip() for line in source.splitlines()
                  if "side == 'buy' and spend_percentage is not None" in line]
    assert stragglers == [], stragglers

  @pytest.mark.parametrize("path", TRADERS, ids=lambda p: p.name)
  def test_the_helper_is_called_everywhere_the_inline_test_used_to_be(self, path):
    source = path.read_text(encoding="utf-8")
    assert source.count("is_cost_denominated(") >= 2, (
      "each trader asks this for execution, dry-run simulation and precision"
    )

  @pytest.mark.parametrize("path", TRADERS, ids=lambda p: p.name)
  def test_create_order_accepts_spend_amount_keyword_only(self, path):
    """
    Keyword-only on purpose: _validate_order_params is called positionally at every
    site, so a positional parameter in the wrong slot would shift arguments silently
    rather than raising.
    """
    source = path.read_text(encoding="utf-8")
    signature = source[source.index("async def create_order"):]
    signature = signature[:signature.index("-> ")]
    assert "*," in signature, "spend_amount must be keyword-only"
    assert "spend_amount" in signature

  @pytest.mark.parametrize("path", TRADERS, ids=lambda p: p.name)
  def test_spend_amount_is_passed_to_both_the_validator_and_the_sizer(self, path):
    source = path.read_text(encoding="utf-8")
    assert source.count("spend_amount=spend_amount") >= 2, (
      "expected pass-through to _validate_order_params and _calculate_order_size"
    )


class TestValidator:
  def _validate(self, **kwargs):
    return BaseCryptoTrader._validate_order_params(
      _Holder(), kwargs.pop("symbol", "BTC/USDT"), kwargs.pop("side", "buy"), **kwargs)

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

  def test_no_mode_at_all_is_refused(self):
    with pytest.raises(ValueError, match="exactly one"):
      self._validate()

  @pytest.mark.parametrize("bad", [0, -1, -300.0])
  def test_a_non_positive_amount_is_refused(self, bad):
    with pytest.raises(ValueError, match="must be positive"):
      self._validate(spend_amount=bad)


class _Holder:
  """Enough of a trader for _validate_order_params, which touches only these."""
  VALID_ORDER_SIDES = ['buy', 'sell']
  VALID_ORDER_TYPES = ['market', 'maker_limit']
  MIN_SPEND_PERCENTAGE = 0.0
  MAX_SPEND_PERCENTAGE = 1.0

  class _Logger:
    def __getattr__(self, _name):
      return lambda *a, **k: None

  logger = _Logger()


class TestSizing:
  """`_calculate_order_size` against a stubbed market, no exchange involved."""

  def _ctx(self, quote_free=10_000.0, base_free=2.0):
    return {
      'base': 'BTC', 'quote': 'USDT',
      'amount_limits': {'min': None, 'max': None},
      'cost_limits': {'min': None, 'max': None},
      'free': {'USDT': quote_free, 'BTC': base_free},
      'total': {'USDT': quote_free, 'BTC': base_free},
    }

  def _trader(self):
    """
    A stand-in carrying only what _calculate_order_size touches. The base class is
    abstract, so the methods are called unbound against this.
    """
    import types
    return types.SimpleNamespace(
      logger=_Holder._Logger(),
      _safe_amount_to_precision=lambda _symbol, amount: amount,
      _get_maker_buy_price=lambda _symbol, _ticker: 100.0,
      _safe_api_call=None,
    )

  async def test_a_market_buy_returns_the_pinned_cost_in_quote(self):
    """The whole point: amount_to_trade is the cost, not a base amount."""
    order_type, amount, price = await BaseCryptoTrader._calculate_order_size(
      self._trader(), 'BTC/USDT', 'buy', self._ctx(),
      order_execution_strategy='market', spend_amount=300.0)
    assert (order_type, amount, price) == ('market', 300.0, None)

  async def test_spending_more_than_the_quote_balance_is_refused(self):
    with pytest.raises(ValueError, match="Insufficient USDT balance"):
      await BaseCryptoTrader._calculate_order_size(
        self._trader(), 'BTC/USDT', 'buy', self._ctx(quote_free=200.0),
        order_execution_strategy='market', spend_amount=300.0)

  async def test_a_cash_sell_is_refused(self):
    with pytest.raises(ValueError, match="buy-only"):
      await BaseCryptoTrader._calculate_order_size(
        self._trader(), 'BTC/USDT', 'sell', self._ctx(),
        order_execution_strategy='market', spend_amount=300.0)

  async def test_no_sizing_mode_raises_instead_of_ordering_zero(self):
    """
    The chain used to fall through to amount_to_trade = 0.0 — a silently sized order
    rather than a refused one.
    """
    with pytest.raises(ValueError, match="No sizing mode supplied"):
      await BaseCryptoTrader._calculate_order_size(
        self._trader(), 'BTC/USDT', 'buy', self._ctx(),
        order_execution_strategy='market')

  async def test_percentage_mode_is_unchanged(self):
    order_type, amount, _ = await BaseCryptoTrader._calculate_order_size(
      self._trader(), 'BTC/USDT', 'buy', self._ctx(quote_free=1000.0),
      order_execution_strategy='market', spend_percentage=0.25)
    assert (order_type, amount) == ('market', 250.0)

  async def test_the_exchange_minimum_notional_still_applies(self):
    ctx = self._ctx()
    ctx['cost_limits'] = {'min': 10.0, 'max': None}
    with pytest.raises(ValueError, match="below exchange minimum"):
      await BaseCryptoTrader._calculate_order_size(
        self._trader(), 'BTC/USDT', 'buy', ctx,
        order_execution_strategy='market', spend_amount=5.0)


class TestDiagnosticsNameEverySizingMode:
  """
  A log line that lists the sizing parameters must list all of them.

  Adding a mode and forgetting the diagnostics is invisible to every other test: the
  code is correct, only the output lies. It showed up in real use as
  `[CREATE ORDER] quantity=None, spend_percentage=None` on a cash order — a line
  reporting that nothing was requested, for a request that was about to be executed.
  """

  SOURCES = sorted(
    list((CRYPTO_DIR).glob("*.py")) +
    list((CRYPTO_DIR.parent / "stock").glob("*.py")) +
    [CRYPTO_DIR.parents[1] / "ui/app.py"]
  )

  @pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
  def test_param_summaries_include_spend_amount(self, path):
    summary = re.compile(r"(quantity=\{.*spend_percentage=\{|spend_percentage=\{.*quantity=\{)")
    offenders = [
      line.strip() for line in path.read_text(encoding="utf-8").splitlines()
      if summary.search(line) and "spend_amount" not in line
    ]
    assert offenders == [], (
      f"these lines name some sizing modes but not spend_amount: {offenders}"
    )
