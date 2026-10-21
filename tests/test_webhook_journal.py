"""
A rejected webhook used to leave no trace of what it actually contained.

Five rejection points in handle_webhook — malformed JSON, a non-object body, a missing
or unknown trader_id, a wrong API key — raised an HTTPException after logging only a
short, fixed diagnostic phrase. The request body itself was never kept anywhere, by
deliberate design (rejection_reporter's own comment: echoing it into the shared,
flood-vulnerable log would let an attacker choose what fills it). That left a real gap
once the sender is the operator's own misconfigured TradingView alert rather than an
attacker: there was nothing to look at to see what was actually sent.

This module closes it with a separate, bounded, non-paging destination — the same shape
as access_logger (session 24): own rotating file, no Gotify path, cannot leak into a
root handler. Gated on rejection_reporter's own first-occurrence return value so a flood
cannot write one line per request either.

A sixth, much larger class of rejection — a correctly authenticated signal that is
still wrong in some way (a stale timestamp, a duplicate, a missing field, a bad ticker)
— is not this module's concern. Those are covered by moving the existing full-payload
log line in handle_webhook to fire right after authentication succeeds, instead of only
after every later check had already passed; see TestFullPayloadLoggedOnceAuthenticated.
"""

import json
import logging

import pytest

from src.misc import logger as logger_module
from src.misc.webhook_journal import _render, record_rejected_webhook
from conftest import signal_payload


class TestRedaction:
  """
  _render() decides what is safe to keep. A dict gets exact, key-based redaction; text
  that never parsed as JSON at all gets a best-effort regex pass instead, since there is
  no dict to redact precisely — and either way the real secret must never appear.
  """

  def test_an_api_key_in_a_dict_is_redacted(self):
    rendered = _render({"api_key": "tw_live_realsecret123", "ticker": "BTC/USDT"}, None)
    parsed = json.loads(rendered)
    assert parsed["api_key"] == "***"
    assert "tw_live_realsecret123" not in rendered

  def test_other_fields_in_the_dict_survive(self):
    rendered = _render({"api_key": "x", "ticker": "BTC/USDT", "action": "buy"}, None)
    parsed = json.loads(rendered)
    assert parsed["ticker"] == "BTC/USDT"
    assert parsed["action"] == "buy"

  def test_a_dict_with_no_api_key_field_does_not_raise(self):
    rendered = _render({"ticker": "BTC/USDT"}, None)
    assert json.loads(rendered) == {"ticker": "BTC/USDT"}

  def test_json_that_parsed_to_a_list_is_rendered_without_crashing(self):
    """Valid JSON, not an object — .get() is not available, dumps must still work."""
    rendered = _render([1, 2, 3], None)
    assert json.loads(rendered) == [1, 2, 3]

  def test_malformed_json_still_has_its_api_key_value_redacted(self):
    """
    The case the dict-level path cannot reach: the body never parsed, so there is no
    dict — but the real key can still be sitting right there in the raw text.
    """
    raw = b'{"api_key": "tw_live_realsecret123", "ticker": "BTC' + b'/USDT' * 50
    rendered = _render(None, raw)
    assert "tw_live_realsecret123" not in rendered
    assert '"api_key":"***"' in rendered.replace(" ", "")

  def test_malformed_json_without_an_api_key_shape_is_left_alone(self):
    """The redaction must not fire on text that merely mentions the words."""
    raw = b'{"note": "api_key goes here", not valid json'
    rendered = _render(None, raw)
    assert "api_key goes here" in rendered

  def test_no_data_and_no_body_renders_as_empty(self):
    assert _render(None, None) == ""


class TestTruncation:
  def test_a_short_payload_is_not_marked_as_truncated(self):
    rendered = _render({"ticker": "BTC/USDT"}, None)
    assert "truncated" not in rendered

  def test_a_long_payload_is_capped_and_says_so(self):
    huge = {"ticker": "X" * 5000}
    rendered = _render(huge, None)
    assert len(rendered) < 5000
    assert "truncated" in rendered

  def test_an_oversized_raw_body_does_not_hang_or_crash(self):
    """
    Read before decoding: a pathological body must not be fully turned into text just
    to be thrown away a line later.
    """
    raw = b'{"junk": "' + b"A" * 2_000_000 + b'"'
    rendered = _render(None, raw)
    assert len(rendered) < 3000


class TestNoFloodProtectionOfItsOwn:
  """
  record_rejected_webhook() has no dedup logic of its own — it relies entirely on being
  called only when rejection_reporter.record() returns True. Verified here because that
  is an easy invariant for a future call site to get wrong silently: calling this
  unconditionally compiles and passes type checks, it just reopens the exact flood the
  gating exists to prevent.
  """

  def test_it_writes_every_time_it_is_called(self, caplog):
    """The function itself does not gate — the call site must."""
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    for _ in range(5):
      record_rejected_webhook("test reason", "203.0.113.9", data={"x": 1})
    lines = [r for r in caplog.records if r.name == "RejectedWebhooks"]
    assert len(lines) == 5, "record_rejected_webhook must not dedupe on its own"


class TestLoggerProperties:
  """Same non-functional guarantees as access_logger (session 24), same reasons."""

  def test_it_has_no_gotify_wiring(self):
    from src.misc.webhook_journal import _journal
    assert not hasattr(_journal, "gotify_url")
    assert not hasattr(_journal, "gotify_token")

  def test_it_writes_to_its_own_file(self):
    from src.misc.webhook_journal import _journal
    handler = _journal.handlers[0]
    assert handler.baseFilename.endswith("rejected_webhooks.jsonl")

  def test_it_does_not_propagate_to_root(self):
    """
    caplog cannot tell propagate=True from propagate=False here: pytest's own
    catching_logs attaches directly to any logger that is already non-propagating when
    a test starts. A handler attached to root by hand, as production code might, gets
    no such accommodation — this checks that path directly instead.
    """
    from src.misc.webhook_journal import _journal

    class _Collector(logging.Handler):
      def __init__(self):
        super().__init__()
        self.records = []

      def emit(self, record):
        self.records.append(record)

    root = logging.getLogger()
    collector = _Collector()
    root.addHandler(collector)
    try:
      _journal.info("propagation probe, should not reach root")
    finally:
      root.removeHandler(collector)
    assert not any("propagation probe" in r.getMessage() for r in collector.records)

  def test_each_line_is_valid_json_with_no_prefix(self, caplog):
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    record_rejected_webhook("probe", "1.2.3.4", data={"a": 1})
    line = next(r.getMessage() for r in caplog.records if r.name == "RejectedWebhooks")
    parsed = json.loads(line)  # raises if a human-readable prefix ever sneaks back in
    assert parsed["reason"] == "probe"


class TestEndToEndThroughTheRealWebhook:
  """
  Drives the actual rejection points in handle_webhook, not record_rejected_webhook in
  isolation — proving the five call sites are wired, not just that the function works.
  """

  @staticmethod
  def journal_entries(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records
           if r.name == "RejectedWebhooks"]

  async def test_malformed_json_is_captured(self, client_factory, webhook_url, caplog):
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    payload = b'{"ticker": "BTC/USDT"' + b", oops not valid"
    async with client_factory() as client:
      response = await client.post(webhook_url, content=payload,
                                   headers={"content-type": "application/json"})
    assert response.status_code == 400
    entries = self.journal_entries(caplog)
    assert len(entries) == 1
    assert entries[0]["reason"] == "malformed JSON body"
    assert "BTC/USDT" in entries[0]["payload"]

  async def test_an_unknown_trader_id_is_captured_with_the_id_and_full_payload(
      self, client_factory, webhook_url, caplog):
    """
    The shared rejection_reporter line deliberately never echoes this id (free text,
    no escaping). The journal is JSON-lines, where a string value cannot break out and
    spoof a line, so it is safe to keep the id there for debugging.
    """
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(trader_id="not_a_real_bot"))
    entries = self.journal_entries(caplog)
    assert len(entries) == 1
    assert entries[0]["reason"] == "unknown trader_id"
    assert entries[0]["trader_id"] == "not_a_real_bot"
    assert "not_a_real_bot" in entries[0]["payload"]

  async def test_a_wrong_api_key_is_captured_with_the_key_redacted(
      self, client_factory, webhook_url, crypto_trader, caplog):
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(api_key="wrong-guess-xyz"))
    entries = self.journal_entries(caplog)
    assert len(entries) == 1
    assert "wrong-guess-xyz" not in entries[0]["payload"], \
      "the attacker's guessed key must never be stored"
    assert crypto_trader.tradleware_api_key not in entries[0]["payload"], \
      "the bot's real key must never be stored either"

  async def test_a_flood_of_identical_bad_keys_produces_one_entry(
      self, client_factory, webhook_url, crypto_trader, app, caplog):
    from src.misc.failure_limiter import FailureLimiter
    app.failure_limiter = FailureLimiter(max_failures=10_000, window_s=60)
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    async with client_factory() as client:
      for _ in range(50):
        await client.post(webhook_url, json=signal_payload(api_key="wrong"))
    entries = self.journal_entries(caplog)
    assert len(entries) == 1, f"{len(entries)} entries written for 50 identical attempts"

  async def test_a_successful_signal_writes_nothing_here(
      self, client_factory, webhook_url, crypto_trader, caplog):
    """Scope discipline: this journal is for pre-authentication rejections only."""
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200
    assert self.journal_entries(caplog) == []

  async def test_a_post_authentication_rejection_writes_nothing_here(
      self, client_factory, webhook_url, crypto_trader, caplog):
    """
    An authenticated-but-stale signal is a different problem with a different fix (the
    full payload moved onto trader.logger — see TestFullPayloadLoggedOnceAuthenticated)
    and must not also duplicate into this journal.
    """
    caplog.set_level(logging.DEBUG, logger="RejectedWebhooks")
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(timestamp=1))  # ancient
    assert self.journal_entries(caplog) == []


class TestFullPayloadLoggedOnceAuthenticated:
  """
  The companion fix: once a sender is authenticated, the full payload (redacted of the
  API key) is logged to that bot's own logger immediately — not only after the
  replay/freshness checks further down, which was the previous behaviour and meant any
  of those later rejections left no record of what was actually sent.
  """

  async def test_a_stale_signal_still_leaves_the_payload_on_the_bots_logger(
      self, client_factory, webhook_url, crypto_trader, caplog):
    caplog.set_level(logging.DEBUG, logger="FakeCryptoTrader")
    async with client_factory() as client:
      response = await client.post(
        webhook_url, json=signal_payload(timestamp=1, ticker="CANARY/USDT"))
    assert response.status_code == 400
    payload_lines = [r.getMessage() for r in caplog.records
                     if "Webhook received payload" in r.getMessage()]
    assert payload_lines, "the payload was never logged for a rejected, authenticated signal"
    assert "CANARY/USDT" in payload_lines[0]

  async def test_the_logged_payload_still_redacts_the_api_key(
      self, client_factory, webhook_url, crypto_trader, caplog):
    caplog.set_level(logging.DEBUG, logger="FakeCryptoTrader")
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(timestamp=1))
    payload_lines = [r.getMessage() for r in caplog.records
                     if "Webhook received payload" in r.getMessage()]
    assert payload_lines
    assert crypto_trader.tradleware_api_key not in payload_lines[0]

  async def test_a_missing_order_size_also_leaves_the_payload_behind(
      self, client_factory, webhook_url, crypto_trader, caplog):
    """A second, independent rejection point further down the same function."""
    caplog.set_level(logging.DEBUG, logger="FakeCryptoTrader")
    payload = signal_payload(ticker="SECONDCASE/USDT")
    del payload["order_size"]
    async with client_factory() as client:
      response = await client.post(webhook_url, json=payload)
    assert response.status_code == 400
    payload_lines = [r.getMessage() for r in caplog.records
                     if "Webhook received payload" in r.getMessage()]
    assert payload_lines
    assert "SECONDCASE/USDT" in payload_lines[0]
