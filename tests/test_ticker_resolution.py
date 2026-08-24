"""
Ticker spelling tolerance.

A bot trades exactly one instrument, so `ticker` is an interlock confirming the alert
is pointed at the right bot — it can never usefully vary. TradingView's {{ticker}}
nonetheless expands to the venue-native spelling (BTCUSDC for BINANCE:BTCUSDC), which
never matched the BTC/USDC form Tradleware wants, and the signal was dropped.

Separator and case differences are now accepted with a warning. The received spelling
must be replaced by the configured one before it travels any further: downstream code
does ticker.split('/')[1] *after* create_order has run, so letting 'BTCUSDT' through
raises IndexError once the order is already filled — a real trade reported as a 500.
"""

import logging

import pytest

from conftest import FakeCryptoTrader, signal_payload

from src.ui.app import canonical_ticker


@pytest.fixture
def trader(app):
  """A crypto bot configured for BTC/USDT."""
  bot = FakeCryptoTrader(balance=1000.0)
  app.traders["fakebot"] = bot
  return bot


class TestCanonicalTicker:
  @pytest.mark.parametrize("spelling", ["BTC/USDT", "BTCUSDT", "btcusdt",
                                        "BTC-USDT", "btc_usdt", " BTC / USDT "])
  def test_equivalent_spellings_collapse_to_one_form(self, spelling):
    assert canonical_ticker(spelling) == "BTCUSDT"

  def test_different_instruments_stay_distinct(self):
    assert canonical_ticker("ETHUSDT") != canonical_ticker("BTC/USDT")

  def test_a_perpetual_never_collapses_into_its_spot_pair(self):
    """':' carries meaning in CCXT — BTC/USDT:USDT is a different instrument."""
    assert canonical_ticker("BTC/USDT:USDT") != canonical_ticker("BTC/USDT")

  def test_a_venue_native_perpetual_still_matches_its_configured_form(self):
    """The reason ':' is kept rather than stripped: 'BTCUSDT:USDT' must still resolve."""
    assert canonical_ticker("BTCUSDT:USDT") == canonical_ticker("BTC/USDT:USDT")

  def test_empty_values_do_not_raise(self):
    assert canonical_ticker(None) == ""
    assert canonical_ticker("") == ""


class TestWebhookTickerHandling:
  async def test_the_venue_native_spelling_is_accepted(self, client_factory,
                                                       webhook_url, trader):
    """The reported failure: TradingView sends BTCUSDT, the bot wants BTC/USDT."""
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(ticker="BTCUSDT", dry_run=False))
    assert response.status_code == 200

  async def test_the_exchange_receives_the_configured_spelling(self, client_factory,
                                                               webhook_url, trader):
    """The rebind: without it, 'BTCUSDT' reaches CCXT and split('/')[1] raises."""
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(ticker="BTCUSDT", dry_run=False))
    assert trader.orders[0]["symbol"] == "BTC/USDT"

  @pytest.mark.parametrize("spelling", ["BTCUSDT", "btc-usdt", "BTC_USDT", "btc/usdt"])
  async def test_separator_and_case_variants_all_reach_the_exchange_correctly(
      self, client_factory, webhook_url, trader, spelling):
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(ticker=spelling, dry_run=False))
    assert response.status_code == 200
    assert trader.orders[0]["symbol"] == "BTC/USDT"

  async def test_a_normalised_match_is_warned_about(self, client_factory, webhook_url,
                                                    trader, caplog):
    caplog.set_level(logging.WARNING, logger="FakeCryptoTrader")
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(ticker="BTCUSDT"))
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("BTCUSDT" in m and "BTC/USDT" in m for m in warnings), warnings

  async def test_an_exact_match_is_not_warned_about(self, client_factory, webhook_url,
                                                    trader, caplog):
    caplog.set_level(logging.WARNING, logger="FakeCryptoTrader")
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(ticker="BTC/USDT"))
    assert response.status_code == 200
    # Other warnings (dry-run, for one) are expected; none may be about the ticker.
    assert [r.message for r in caplog.records
            if r.levelno == logging.WARNING and "normalised" in r.message] == []

  async def test_a_different_instrument_is_still_rejected(self, client_factory,
                                                          webhook_url, trader):
    """The interlock has to survive the tolerance."""
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(ticker="ETHUSDT"))
    assert response.status_code == 400
    assert trader.orders == []

  async def test_a_perpetual_is_rejected_for_a_spot_bot(self, client_factory,
                                                        webhook_url, trader):
    """Collapsing BTC/USDT:USDT into BTC/USDT would trade the wrong instrument."""
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(ticker="BTC/USDT:USDT"))
    assert response.status_code == 400
    assert trader.orders == []
