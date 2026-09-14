"""
Reusing an already-fetched balance in _resolve_market_and_balance.

The webhook handler fetches the balance once to validate a signal (the
insufficient-balance check) before ever reaching create_order, while the per-bot
execution lock is held for the whole request — so nothing else can change that
balance in between. create_order's own _resolve_market_and_balance used to fetch it
again unconditionally: 3-4s of extra latency on every order, for a second read that
could only ever disagree with the first because of something outside Tradleware
entirely (a manual withdrawal on the exchange itself).
"""
import types

import pytest

from src.traders.crypto.base_crypto_trader import BaseCryptoTrader

MARKET = {'base': 'BTC', 'quote': 'USDT', 'limits': {}}

# Every concrete exchange class that has its own create_order/known_balance wiring —
# checked below so a copy-paste miss on any one of them is caught, not assumed.
EXCHANGE_MODULES = [
  "binance_trader", "coinbase_trader", "cryptocom_trader",
  "ir_trader", "kraken_trader", "okx_trader",
]


class _Recorder:
  def __getattr__(self, _name):
    return lambda *a, **k: None


def _holder(fetch_balance_return=None):
  calls = {"fetch_balance": 0}

  async def fetch_balance():
    calls["fetch_balance"] += 1
    return fetch_balance_return or {"free": {"USDT": 1000.0}, "total": {"USDT": 1000.0}}

  holder = types.SimpleNamespace(
    logger=_Recorder(),
    exchange_id="testex",
    account_identifier="testbot",
    exchange=types.SimpleNamespace(
      load_markets=lambda reload=True: True,
      markets={"BTC/USDT": MARKET},
      market=lambda symbol: MARKET,
    ),
    fetch_balance=fetch_balance,
  )
  holder._safe_api_call = BaseCryptoTrader._safe_api_call.__get__(holder)
  return holder, calls


class TestKnownBalanceIsReused:
  async def test_a_known_balance_skips_the_fetch(self):
    holder, calls = _holder()
    known = {"free": {"USDT": 500.0}, "total": {"USDT": 500.0}}
    ctx = await BaseCryptoTrader._resolve_market_and_balance(
      holder, "BTC/USDT", known_balance=known)
    assert calls["fetch_balance"] == 0
    assert ctx["free"] == known["free"]
    assert ctx["total"] == known["total"]

  async def test_without_a_known_balance_it_still_fetches(self):
    """Every other caller — a dashboard's manual conversion, etc. — is unaffected."""
    holder, calls = _holder()
    ctx = await BaseCryptoTrader._resolve_market_and_balance(holder, "BTC/USDT")
    assert calls["fetch_balance"] == 1
    assert ctx["free"] == {"USDT": 1000.0}

  async def test_a_falsy_known_balance_still_raises_like_a_failed_fetch(self):
    """`known_balance={}` is not 'not given' — only None means that — but an empty
    balance dict must fail the same way a failed live fetch always has."""
    holder, _ = _holder()
    with pytest.raises(RuntimeError, match="Could not fetch balance"):
      await BaseCryptoTrader._resolve_market_and_balance(holder, "BTC/USDT", known_balance={})


class TestEveryExchangeWiresItThrough:
  """
  Six near-identical manual edits (one create_order signature + one call site per
  exchange) is exactly the kind of change where one file gets missed. Checked from the
  source rather than assumed.
  """

  def _source(self, module_name):
    from pathlib import Path
    import importlib
    mod = importlib.import_module(f"src.traders.crypto.{module_name}")
    return Path(mod.__file__).read_text(encoding="utf-8")

  @pytest.mark.parametrize("module_name", EXCHANGE_MODULES)
  def test_create_order_accepts_known_balance(self, module_name):
    src = self._source(module_name)
    assert "known_balance: Dict[str, Any] = None" in src

  @pytest.mark.parametrize("module_name", EXCHANGE_MODULES)
  def test_create_order_forwards_it_to_resolve(self, module_name):
    src = self._source(module_name)
    assert "_resolve_market_and_balance(symbol, known_balance=known_balance)" in src
