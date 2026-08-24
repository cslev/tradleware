"""
Bot-card log rendering.

The card showed '[09:30:36] ERROR: ...' with no date, so a line could be from this
morning or last week. The date is now emitted once per day as a header rather than
repeated on all fifty rows.

Headers are derived at read time, not stored: the buffer is a deque(maxlen=50), so
storing them would spend slots on separators and — once the oldest entries were
evicted — could leave the first visible line with no date above it.
"""

from collections import deque

import pytest

from src.misc.logger import format_log_buffer


def entry(day, clock, level="INFO", message="msg"):
  return (day, clock, level, message)


class TestFormatLogBuffer:
  def test_an_empty_buffer_renders_nothing(self):
    assert format_log_buffer(deque()) == []

  def test_a_single_day_is_dated_once(self):
    out = format_log_buffer([
      entry("2026-08-24", "09:00:01", "INFO", "one"),
      entry("2026-08-24", "09:00:02", "ERROR", "two"),
      entry("2026-08-24", "09:00:03", "INFO", "three"),
    ])
    assert out == [
      "── 2026-08-24 ──",
      "[09:00:01] INFO: one",
      "[09:00:02] ERROR: two",
      "[09:00:03] INFO: three",
    ]

  def test_the_first_entry_always_gets_a_header(self):
    """A card open on a quiet bot must still say which day its lines are from."""
    out = format_log_buffer([entry("2026-08-24", "09:00:01")])
    assert out[0] == "── 2026-08-24 ──"

  def test_a_new_header_appears_when_the_day_rolls_over(self):
    """A dashboard left open across midnight must not silently continue the day."""
    out = format_log_buffer([
      entry("2026-08-23", "23:59:58", "INFO", "before"),
      entry("2026-08-24", "00:00:03", "INFO", "after"),
    ])
    assert out == [
      "── 2026-08-23 ──",
      "[23:59:58] INFO: before",
      "── 2026-08-24 ──",
      "[00:00:03] INFO: after",
    ]

  def test_a_gap_of_several_days_dates_each_one(self):
    days = ["2026-08-20", "2026-08-22", "2026-08-24"]
    out = format_log_buffer([entry(d, "12:00:00") for d in days])
    assert [line for line in out if line.startswith("──")] == [f"── {d} ──" for d in days]

  def test_the_day_is_never_repeated_within_a_run(self):
    out = format_log_buffer([entry("2026-08-24", f"09:00:{i:02d}") for i in range(10)])
    assert out.count("── 2026-08-24 ──") == 1

  @pytest.mark.parametrize("level", ["INFO", "ERROR", "WARNING", "SUCCESS", "CRITICAL"])
  def test_the_level_marker_the_frontend_colours_on_is_preserved(self, level):
    """main.js keys off '] LEVEL:' — losing that shape would render every line grey."""
    out = format_log_buffer([entry("2026-08-24", "09:00:01", level, "text")])
    assert out[1] == f"[09:00:01] {level}: text"

  def test_plain_strings_pass_through(self):
    """A buffer populated before this format existed must still render."""
    assert format_log_buffer(["[09:00:01] INFO: legacy"]) == ["[09:00:01] INFO: legacy"]


class TestTraderIntegration:
  """
  Both bases are abstract, so these call the methods unbound against a stand-in that
  carries only a log_buffer — enough to exercise the real code without an exchange.
  """

  class _Holder:
    def __init__(self):
      self.log_buffer = deque(maxlen=50)

  @pytest.mark.parametrize("base_path", [
    ("src.traders.crypto.base_crypto_trader", "BaseCryptoTrader"),
    ("src.traders.stock.base_stock_trader", "BaseStockTrader"),
  ])
  def test_a_written_entry_comes_back_dated(self, base_path):
    """_add_to_buffer must store the day, or get_recent_logs has nothing to group on."""
    module, name = base_path
    base = getattr(__import__(module, fromlist=[name]), name)
    holder = self._Holder()

    base._add_to_buffer(holder, "ERROR", "something broke")
    rendered = base.get_recent_logs(holder)

    assert len(rendered) == 2
    assert rendered[0].startswith("── ") and rendered[0].endswith(" ──")
    assert rendered[1].endswith("ERROR: something broke")

  def test_both_trader_bases_render_identically(self):
    """Crypto and stock cards must not drift apart in how they date lines."""
    from src.traders.crypto.base_crypto_trader import BaseCryptoTrader
    from src.traders.stock.base_stock_trader import BaseStockTrader

    rows = [entry("2026-08-23", "23:59:58"), entry("2026-08-24", "00:00:03")]
    crypto, stock = self._Holder(), self._Holder()
    crypto.log_buffer.extend(rows)
    stock.log_buffer.extend(rows)

    assert (BaseCryptoTrader.get_recent_logs(crypto)
            == BaseStockTrader.get_recent_logs(stock))


class TestBufferEviction:
  def test_headers_do_not_consume_buffer_slots(self):
    """Storing separators would cost real entries; 50 appends must keep 50 entries."""
    buffer = deque(maxlen=50)
    for i in range(60):
      buffer.append(entry("2026-08-24", f"09:{i // 60:02d}:{i % 60:02d}"))
    assert len(buffer) == 50
    rendered = format_log_buffer(buffer)
    assert len([line for line in rendered if not line.startswith("──")]) == 50

  def test_the_oldest_surviving_line_still_has_a_date_above_it(self):
    """The reason headers are derived rather than stored."""
    buffer = deque(maxlen=3)
    buffer.append(entry("2026-08-23", "23:00:00"))
    for i in range(3):
      buffer.append(entry("2026-08-24", f"09:00:{i:02d}"))
    rendered = format_log_buffer(buffer)
    assert rendered[0] == "── 2026-08-24 ──"
    assert "── 2026-08-23 ──" not in rendered
