"""
Flood-resistant reporting of webhook rejections.

The property has two halves, and both are load-bearing: nothing genuine is ever hidden —
the first of each distinct problem is reported immediately — while a flood cannot
produce unbounded log lines or notifications.
"""

import logging

import pytest

from src.misc.rejection_reporter import (MAX_REPORTED_PER_WINDOW, MAX_TRACKED_KEYS,
                                         RejectionReporter)
from conftest import signal_payload


@pytest.fixture
def reporter():
  return RejectionReporter(logging.getLogger("reporter-test"), summary_interval_s=300)


class TestFirstOccurrenceIsAlwaysReported:
  def test_a_new_problem_is_logged_immediately(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    assert reporter.record("invalid API key", "203.0.113.9") is True
    assert "invalid API key" in caplog.text
    assert "203.0.113.9" in caplog.text

  def test_each_distinct_reason_gets_its_own_first_report(self, reporter):
    assert reporter.record("invalid API key", "203.0.113.9") is True
    assert reporter.record("malformed JSON body", "203.0.113.9") is True

  def test_each_distinct_source_gets_its_own_first_report(self, reporter):
    assert reporter.record("invalid API key", "203.0.113.9") is True
    assert reporter.record("invalid API key", "198.51.100.4") is True

  def test_the_detail_is_included_on_the_first_report_only(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    reporter.record("stale timestamp", "203.0.113.9", "412s old, limit is 300s")
    assert "412s old" in caplog.text
    caplog.clear()
    reporter.record("stale timestamp", "203.0.113.9", "412s old, limit is 300s")
    assert caplog.text == ""


class TestRepeatsAreCollapsed:
  def test_a_repeat_is_not_logged(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    reporter.record("invalid API key", "203.0.113.9")
    caplog.clear()
    for _ in range(500):
      assert reporter.record("invalid API key", "203.0.113.9") is False
    assert caplog.text == "", "a flood produced log output"
    assert reporter.pending() == 500

  def test_many_sources_cannot_each_buy_a_log_line(self, reporter, caplog):
    """Otherwise rotating the source address defeats the whole thing."""
    caplog.set_level(logging.DEBUG)
    logged = sum(reporter.record("invalid API key", f"203.0.113.{n}")
                 for n in range(200))
    assert logged == MAX_REPORTED_PER_WINDOW

  def test_tracking_is_bounded(self, reporter):
    """Attacker-chosen sources must not grow memory without limit."""
    for n in range(MAX_TRACKED_KEYS * 4):
      reporter.record("invalid API key", f"10.0.{n // 256}.{n % 256}")
    assert len(reporter._suppressed) <= MAX_TRACKED_KEYS
    assert reporter.pending() == MAX_TRACKED_KEYS * 4 - MAX_REPORTED_PER_WINDOW


class TestSummary:
  def test_one_line_covers_everything(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    for _ in range(300):
      reporter.record("invalid API key", "203.0.113.9")
    for _ in range(120):
      reporter.record("malformed JSON body", "198.51.100.4")
    caplog.clear()

    assert reporter.flush() is True
    lines = [record for record in caplog.records]
    assert len(lines) == 1, "the summary must not itself be a flood"
    assert "418" in caplog.text                      # 420 recorded, 2 first-reports
    assert "203.0.113.9" in caplog.text
    assert "198.51.100.4" in caplog.text

  def test_sources_are_ranked_by_volume(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    for _ in range(10):
      reporter.record("reason-a", "quiet-source")
    for _ in range(500):
      reporter.record("reason-b", "loud-source")
    caplog.clear()
    reporter.flush()
    text = caplog.text
    assert text.index("loud-source") < text.index("quiet-source")

  def test_a_long_tail_is_summarised_not_listed(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    for n in range(40):
      for _ in range(3):
        reporter.record("invalid API key", f"203.0.113.{n}")
    caplog.clear()
    reporter.flush()
    assert "other source/reason combination(s)" in caplog.text

  def test_nothing_is_emitted_when_there_is_nothing_to_report(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    assert reporter.flush() is False
    assert caplog.text == ""

  def test_a_single_first_report_needs_no_summary(self, reporter, caplog):
    reporter.record("invalid API key", "203.0.113.9")
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    assert reporter.flush() is False

  def test_the_window_resets_after_a_flush(self, reporter, caplog):
    caplog.set_level(logging.DEBUG)
    for _ in range(50):
      reporter.record("invalid API key", "203.0.113.9")
    reporter.flush()
    caplog.clear()
    assert reporter.record("invalid API key", "203.0.113.9") is True, \
      "the problem should be reported afresh in a new window"
    assert reporter.pending() == 0


class TestDestination:
  def test_a_bot_specific_rejection_logs_to_that_bot(self, reporter, caplog):
    """So it still shows in the bot's Recent Logs tab on the dashboard."""
    caplog.set_level(logging.DEBUG)
    bot_logger = logging.getLogger("SomeBot")
    reporter.record("invalid API key", "203.0.113.9", logger=bot_logger)
    assert any(record.name == "SomeBot" for record in caplog.records)


class TestWindowTiming:
  def test_not_due_before_the_interval(self):
    reporter = RejectionReporter(logging.getLogger("t"), summary_interval_s=300)
    assert reporter.due() is False

  def test_due_once_the_interval_has_passed(self):
    reporter = RejectionReporter(logging.getLogger("t"), summary_interval_s=1)
    reporter._window_started -= 2
    assert reporter.due() is True

  def test_interval_has_a_floor(self):
    assert RejectionReporter(logging.getLogger("t"), 0).summary_interval_s >= 1


class TestWebhookFloodEndToEnd:
  """The behaviour that actually protects the log file and the notifications."""

  async def test_a_flood_of_bad_keys_produces_one_log_line(self, client_factory,
                                                           webhook_url, crypto_trader,
                                                           caplog):
    caplog.set_level(logging.DEBUG)
    async with client_factory() as client:
      for _ in range(50):
        response = await client.post(webhook_url,
                                     json=signal_payload(api_key="wrong"))
        assert response.status_code == 401, "rejection behaviour must not change"
    rejections = [r for r in caplog.records if "invalid API key" in r.getMessage()]
    assert len(rejections) == 1, f"{len(rejections)} lines written for 50 attempts"

  async def test_the_flood_is_still_counted(self, client_factory, webhook_url,
                                            crypto_trader, app):
    async with client_factory() as client:
      for _ in range(50):
        await client.post(webhook_url, json=signal_payload(api_key="wrong"))
    assert app.rejection_reporter.pending() == 49

  async def test_a_valid_signal_is_unaffected_by_the_flood(self, client_factory,
                                                           webhook_url, crypto_trader):
    """The limiter must never be able to stop real trading."""
    async with client_factory() as client:
      for _ in range(50):
        await client.post(webhook_url, json=signal_payload(api_key="wrong"))
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200
    assert len(crypto_trader.orders) == 1

  async def test_nothing_is_logged_before_authentication(self, client_factory,
                                                         webhook_url, crypto_trader,
                                                         caplog):
    """
    The 'webhook received' line used to be written for every request from anyone who
    knew the path, carrying three attacker-chosen fields.
    """
    caplog.set_level(logging.DEBUG)
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(api_key="wrong",
                                                         ticker="INJECTED-TICKER"))
    assert "INJECTED-TICKER" not in caplog.text
    assert "Webhook received" not in caplog.text

  async def test_an_authenticated_signal_is_still_announced(self, client_factory,
                                                            webhook_url, crypto_trader,
                                                            caplog):
    caplog.set_level(logging.DEBUG)
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload())
    assert "Webhook received" in caplog.text

  async def test_a_malformed_body_is_not_echoed_into_the_log(self, client_factory,
                                                             webhook_url,
                                                             crypto_trader, caplog):
    caplog.set_level(logging.DEBUG)
    payload = b'{"junk": "' + b'FILLER' * 200 + b'" oops'
    async with client_factory() as client:
      response = await client.post(webhook_url, content=payload,
                                   headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert "FILLER" not in caplog.text, "attacker-chosen text reached the log file"

  async def test_replay_floods_are_collapsed_too(self, client_factory, webhook_url,
                                                 crypto_trader, caplog):
    """A captured request can be replayed without knowing the key."""
    caplog.set_level(logging.DEBUG)
    captured = signal_payload()
    async with client_factory() as client:
      await client.post(webhook_url, json=captured)
      caplog.clear()
      for _ in range(30):
        response = await client.post(webhook_url, json=captured)
        assert response.status_code == 409
    duplicates = [r for r in caplog.records if "duplicate signal" in r.getMessage()]
    assert len(duplicates) == 1
