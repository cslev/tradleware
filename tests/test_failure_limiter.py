"""
Throttling of failed webhook authentication.

The point of the throttle is to make guessing impractical. The constraint on it is that
it must never be able to stop real trading — which is the easier property to break, so
most of these tests are about that half.
"""

import logging

import pytest

from src.misc.failure_limiter import MAX_TRACKED_SOURCES, FailureLimiter
from conftest import signal_payload

ATTACKER = "203.0.113.9"


@pytest.fixture
def limiter():
  return FailureLimiter(max_failures=3, window_s=60)


class TestCounting:
  def test_an_unseen_source_is_not_blocked(self, limiter):
    assert limiter.is_blocked(ATTACKER) is False

  def test_blocking_starts_at_the_limit(self, limiter):
    for expected in (1, 2):
      assert limiter.record_failure(ATTACKER) == expected
      assert limiter.is_blocked(ATTACKER) is False
    assert limiter.record_failure(ATTACKER) == 3
    assert limiter.is_blocked(ATTACKER) is True

  def test_sources_are_counted_separately(self, limiter):
    for _ in range(5):
      limiter.record_failure(ATTACKER)
    assert limiter.is_blocked(ATTACKER) is True
    assert limiter.is_blocked("198.51.100.4") is False

  def test_a_success_clears_the_count(self, limiter):
    for _ in range(5):
      limiter.record_failure(ATTACKER)
    assert limiter.is_blocked(ATTACKER) is True
    limiter.clear(ATTACKER)
    assert limiter.is_blocked(ATTACKER) is False

  def test_clearing_an_unknown_source_is_harmless(self, limiter):
    limiter.clear("never-seen")

  def test_the_window_expires(self):
    limiter = FailureLimiter(max_failures=2, window_s=1)
    limiter.record_failure(ATTACKER)
    limiter.record_failure(ATTACKER)
    assert limiter.is_blocked(ATTACKER) is True
    limiter._failures[ATTACKER][0] -= 2        # pretend the window elapsed
    assert limiter.is_blocked(ATTACKER) is False

  def test_retry_after_is_reported(self, limiter):
    for _ in range(3):
      limiter.record_failure(ATTACKER)
    assert 0 < limiter.seconds_until_clear(ATTACKER) <= 61

  def test_retry_after_is_zero_when_not_blocked(self, limiter):
    assert limiter.seconds_until_clear("never-seen") == 0

  def test_configuration_has_floors(self):
    limiter = FailureLimiter(max_failures=0, window_s=0)
    assert limiter.max_failures >= 1
    assert limiter.window_s >= 1


class TestMemoryIsBounded:
  def test_tracking_does_not_grow_without_limit(self):
    """Sources are attacker-chosen, so the table needs a ceiling."""
    limiter = FailureLimiter(max_failures=3, window_s=60)
    for n in range(MAX_TRACKED_SOURCES + 500):
      limiter.record_failure(f"10.{n // 65536}.{(n // 256) % 256}.{n % 256}")
    assert len(limiter._failures) <= MAX_TRACKED_SOURCES


class TestItNeverStopsRealTrading:
  """The property that matters more than the throttling itself."""

  async def test_a_valid_signal_is_never_throttled(self, client_factory, webhook_url,
                                                   crypto_trader, app):
    """A source that authenticates is answered no matter how noisy the endpoint is."""
    app.failure_limiter = FailureLimiter(max_failures=3, window_s=60)
    async with client_factory(peer="198.51.100.7") as attacker:
      for _ in range(10):
        await attacker.post(webhook_url, json=signal_payload(api_key="wrong"))
    async with client_factory(peer="203.0.113.50") as genuine:
      response = await genuine.post(webhook_url, json=signal_payload())
    assert response.status_code == 200
    assert len(crypto_trader.orders) == 1

  async def test_one_success_restores_a_source(self, client_factory, webhook_url,
                                               crypto_trader, app):
    """A briefly misconfigured alert must not be stuck waiting out the window."""
    app.failure_limiter = FailureLimiter(max_failures=5, window_s=60)
    async with client_factory(peer="198.51.100.8") as client:
      for _ in range(4):                       # up to but not past the limit
        await client.post(webhook_url, json=signal_payload(api_key="wrong"))
      fixed = await client.post(webhook_url, json=signal_payload())
      assert fixed.status_code == 200
      # the counter is cleared, so a fresh run of failures starts from zero again
      for _ in range(4):
        response = await client.post(webhook_url, json=signal_payload(api_key="wrong"))
        assert response.status_code == 401, "still counting from before the success"

  async def test_loopback_is_never_throttled(self, client_factory, webhook_url,
                                             crypto_trader, app):
    app.failure_limiter = FailureLimiter(max_failures=2, window_s=60)
    async with client_factory(peer="127.0.0.1") as client:
      for _ in range(10):
        response = await client.post(webhook_url, json=signal_payload(api_key="wrong"))
        assert response.status_code == 401, "a local script locked itself out"

  async def test_a_trusted_address_is_never_throttled(self, client_factory, webhook_url,
                                                      crypto_trader, app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    app.failure_limiter = FailureLimiter(max_failures=2, window_s=60)
    async with client_factory(peer="192.168.1.50") as client:
      for _ in range(10):
        response = await client.post(webhook_url, json=signal_payload(api_key="wrong"))
        assert response.status_code == 401, "the kiosk locked itself out"


class TestItThrottlesGuessing:
  async def test_an_attacker_is_cut_off(self, client_factory, webhook_url,
                                        crypto_trader, app):
    app.failure_limiter = FailureLimiter(max_failures=3, window_s=60)
    codes = []
    async with client_factory(peer=ATTACKER) as client:
      for _ in range(8):
        response = await client.post(webhook_url, json=signal_payload(api_key="wrong"))
        codes.append(response.status_code)
    assert codes[:3] == [401, 401, 401]
    assert set(codes[3:]) == {429}, f"guessing continued: {codes}"

  async def test_the_response_says_when_to_come_back(self, client_factory, webhook_url,
                                                     crypto_trader, app):
    app.failure_limiter = FailureLimiter(max_failures=2, window_s=60)
    async with client_factory(peer=ATTACKER) as client:
      for _ in range(3):
        response = await client.post(webhook_url, json=signal_payload(api_key="wrong"))
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0

  async def test_a_blocked_source_is_refused_before_the_body_is_read(
      self, client_factory, webhook_url, crypto_trader, app):
    """Turning away a guesser must be cheaper than serving one."""
    app.failure_limiter = FailureLimiter(max_failures=2, window_s=60)
    async with client_factory(peer=ATTACKER) as client:
      for _ in range(3):
        await client.post(webhook_url, json=signal_payload(api_key="wrong"))
      response = await client.post(webhook_url, content=b"not json at all",
                                   headers={"content-type": "application/json"})
    assert response.status_code == 429, "the body was parsed before the throttle"

  async def test_the_throttle_itself_does_not_flood_the_log(self, client_factory,
                                                            webhook_url, crypto_trader,
                                                            app, caplog):
    caplog.set_level(logging.DEBUG)
    app.failure_limiter = FailureLimiter(max_failures=2, window_s=60)
    async with client_factory(peer=ATTACKER) as client:
      for _ in range(40):
        await client.post(webhook_url, json=signal_payload(api_key="wrong"))
    throttle_lines = [r for r in caplog.records
                      if "too many failed attempts" in r.getMessage()]
    assert len(throttle_lines) == 1

  async def test_other_bots_are_unaffected_by_one_attacker(self, client_factory,
                                                           webhook_url, app,
                                                           crypto_trader):
    """Throttling is per source, not per bot, so it cannot be aimed at a bot."""
    from conftest import FakeCryptoTrader
    app.traders["otherbot"] = FakeCryptoTrader()
    app.failure_limiter = FailureLimiter(max_failures=2, window_s=60)
    async with client_factory(peer=ATTACKER) as attacker:
      for _ in range(5):
        await attacker.post(webhook_url,
                            json=signal_payload(trader_id="fakebot", api_key="wrong"))
    async with client_factory(peer="203.0.113.77") as genuine:
      response = await genuine.post(webhook_url, json=signal_payload(
        trader_id="otherbot", api_key="tw_live_9f2b7c4e"))
    assert response.status_code == 200
