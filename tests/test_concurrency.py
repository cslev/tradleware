"""
Per-bot execution serialisation.

Without the lock, two signals arriving together both read the same balance and both
order against it: two "buy 50%" signals spent the whole 1000 USDT where 750 was
intended, and both returned 200 with no error anywhere.
"""

import asyncio
import time

import pytest

from conftest import FakeCryptoTrader, signal_payload

LATENCY = 0.05   # simulated exchange round trip, long enough for requests to overlap


@pytest.fixture
def slow_trader(app):
  """A bot whose exchange calls take long enough for a race to be observable."""
  trader = FakeCryptoTrader(balance=1000.0, latency=LATENCY)
  app.traders["fakebot"] = trader
  return trader


class TestConcurrentSignals:
  async def test_two_signals_do_not_share_a_pre_trade_balance(self, client_factory,
                                                              webhook_url, slow_trader):
    """The reported bug: 50% then 50% must spend 750, not 1000."""
    async with client_factory() as client:
      statuses = await asyncio.gather(
        client.post(webhook_url, json=signal_payload(order_size=50, dry_run=False)),
        client.post(webhook_url, json=signal_payload(order_size=50, dry_run=False)),
      )
    assert [r.status_code for r in statuses] == [200, 200]
    assert slow_trader.balance == pytest.approx(250.0)
    assert 1000.0 - slow_trader.balance == pytest.approx(750.0)

  async def test_the_exchange_never_sees_overlapping_calls(self, client_factory,
                                                           webhook_url, slow_trader):
    async with client_factory() as client:
      await asyncio.gather(*(
        client.post(webhook_url, json=signal_payload(order_size=10, dry_run=False))
        for _ in range(4)))
    assert slow_trader.max_in_flight == 1

  async def test_the_second_signal_sizes_from_the_settled_balance(
      self, client_factory, webhook_url, slow_trader):
    """
    The handler also re-reads the balance after a fill to report the new portfolio, so
    assert the property rather than the exact event transcript.
    """
    async with client_factory() as client:
      await asyncio.gather(
        client.post(webhook_url, json=signal_payload(order_size=50, dry_run=False)),
        client.post(webhook_url, json=signal_payload(order_size=50, dry_run=False)),
      )
    spends = [event for event in slow_trader.events if event.startswith("spend")]
    assert spends == ["spend 500", "spend 250"]
    assert slow_trader.events.count("read 1000") == 1, \
      "both requests saw the full pre-trade balance"


class TestCrossPathSerialisation:
  """A dashboard conversion and an inbound signal hit the same balance."""

  async def test_convert_and_signal_do_not_overlap(self, client_factory, webhook_url,
                                                   slow_trader, app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    async with client_factory(peer="192.168.1.50") as dashboard, \
               client_factory() as webhook:
      results = await asyncio.gather(
        dashboard.post("/convert/fakebot"),
        webhook.post(webhook_url, json=signal_payload(order_size=50, dry_run=False)),
      )
    assert [r.status_code for r in results] == [200, 200]
    assert slow_trader.max_in_flight == 1

  async def test_the_signal_sees_the_settled_balance(self, client_factory, webhook_url,
                                                     slow_trader, app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    async with client_factory(peer="192.168.1.50") as dashboard, \
               client_factory() as webhook:
      await asyncio.gather(
        dashboard.post("/convert/fakebot"),
        webhook.post(webhook_url, json=signal_payload(order_size=50, dry_run=False)),
      )
    assert slow_trader.events[0].startswith("convert")
    assert "read 0" in slow_trader.events


class TestBotsStayIndependent:
  async def test_different_bots_run_in_parallel(self, client_factory, webhook_url, app):
    first = FakeCryptoTrader(balance=1000.0, latency=LATENCY)
    second = FakeCryptoTrader(balance=1000.0, latency=LATENCY)
    app.traders["bot-one"] = first
    app.traders["bot-two"] = second

    started = time.monotonic()
    async with client_factory() as client:
      results = await asyncio.gather(
        client.post(webhook_url, json=signal_payload(trader_id="bot-one",
                                                     order_size=10, dry_run=False)),
        client.post(webhook_url, json=signal_payload(trader_id="bot-two",
                                                     order_size=10, dry_run=False)),
      )
    elapsed = time.monotonic() - started

    assert [r.status_code for r in results] == [200, 200]
    # Two exchange calls each. Serialised would be ~4x LATENCY; parallel is ~2x.
    assert elapsed < LATENCY * 3.5, "different bots were serialised against each other"

  async def test_each_bot_gets_its_own_lock(self, app):
    locks = app.get_trader_lock.__globals__["_TRADER_LOCKS"]
    first = app.get_trader_lock("bot-one")
    second = app.get_trader_lock("bot-two")
    assert first is not second
    assert app.get_trader_lock("bot-one") is first   # stable across calls
    assert set(locks) == {"bot-one", "bot-two"}


class TestLockLifecycle:
  async def test_a_busy_bot_gives_up_rather_than_queueing_forever(self, client_factory,
                                                                  webhook_url, app):
    app.TRADER_LOCK_TIMEOUT_S = 1
    app.traders["fakebot"] = FakeCryptoTrader(balance=1000.0, latency=5.0)

    async with client_factory() as client:
      first = asyncio.create_task(
        client.post(webhook_url, json=signal_payload(dry_run=False)))
      await asyncio.sleep(0.1)                       # let it take the lock
      second = await client.post(webhook_url, json=signal_payload(dry_run=False))
      first.cancel()
    assert second.status_code == 503

  async def test_the_lock_is_released_when_a_trade_fails(self, client_factory,
                                                         webhook_url, app):
    class BrokenTrader(FakeCryptoTrader):
      async def fetch_balance(self):
        raise RuntimeError("exchange unreachable")

    app.traders["fakebot"] = BrokenTrader()
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code >= 400
    assert app.get_trader_lock("fakebot").locked() is False

  async def test_the_lock_is_released_after_a_normal_trade(self, client_factory,
                                                           webhook_url, crypto_trader,
                                                           app):
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload())
    assert app.get_trader_lock("fakebot").locked() is False
