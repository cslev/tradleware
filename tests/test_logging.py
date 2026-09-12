"""
Logging infrastructure: notification delivery and log file rotation.

Two properties that are invisible until they bite. Gotify used to be posted inline
from async request handlers, so one unauthenticated request froze the whole
application for the length of the round trip. And every CustomLogger builds its own
handler on the same file, which makes naive rotation corrupt itself.
"""

import asyncio
import csv
import gzip
import io
import json
import logging
import time
from pathlib import Path

import pytest

from src.misc import logger as logger_module
from src.misc.logger import CustomLogger, RotatingFileHandler, get_csv_file_logger
from conftest import signal_payload


class TestGotifyDeliveryIsNonBlocking:
  async def test_a_slow_notification_does_not_stall_the_event_loop(self, use_gotify,
                                                                   app):
    """
    Measured with a heartbeat rather than by timing a request: a blocking call would
    also delay any timestamp taken after it, hiding the stall.
    """
    use_gotify.response_delay = 1.5
    ticks = [time.monotonic()]

    async def heartbeat():
      while True:
        await asyncio.sleep(0.05)
        ticks.append(time.monotonic())

    beat = asyncio.create_task(heartbeat())
    ticks.append(time.monotonic())
    app.logger.error("an error that pushes a notification")
    await asyncio.sleep(0.2)
    ticks.append(time.monotonic())
    beat.cancel()

    largest_gap = max(later - earlier for earlier, later in zip(ticks, ticks[1:]))
    assert largest_gap < 0.3, f"event loop stalled for {largest_gap:.2f}s"

  async def test_a_rejected_webhook_returns_immediately(self, use_gotify,
                                                        client_factory, webhook_url,
                                                        crypto_trader):
    use_gotify.response_delay = 1.5
    started = time.monotonic()
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(api_key="wrong"))
    elapsed = time.monotonic() - started
    assert response.status_code == 401
    assert elapsed < 0.5, f"the handler waited {elapsed:.2f}s for Gotify"

  async def test_a_flood_of_rejects_cannot_freeze_trading(self, use_gotify,
                                                          client_factory, webhook_url,
                                                          crypto_trader):
    use_gotify.response_delay = 0.5
    started = time.monotonic()
    async with client_factory() as client:
      await asyncio.gather(*(
        client.post(webhook_url, json=signal_payload(api_key="wrong"))
        for _ in range(20)))
    assert time.monotonic() - started < 1.0


class TestGotifyDelivery:
  def test_notifications_actually_arrive(self, use_gotify, app):
    app.logger.error("a delivered error")
    assert logger_module.flush_gotify_queue(timeout=10) is True
    messages = [payload.get("message", "") for payload in use_gotify.received]
    assert any("a delivered error" in message for message in messages)

  def test_order_is_preserved(self, use_gotify, app):
    for index in range(5):
      app.logger.error(f"ordered-{index}")
    logger_module.flush_gotify_queue(timeout=10)
    seen = [payload["message"] for payload in use_gotify.received
            if payload.get("message", "").startswith("ordered-")]
    assert seen == [f"ordered-{index}" for index in range(5)]

  def test_a_full_backlog_drops_instead_of_blocking(self, use_gotify, app):
    use_gotify.response_delay = 0.2
    dropped_before = logger_module._gotify_dropped
    started = time.monotonic()
    for index in range(160):                     # the queue holds 100
      app.logger.error(f"flood-{index}")
    assert time.monotonic() - started < 1.0, "enqueueing blocked the caller"
    assert logger_module._gotify_dropped > dropped_before

  def test_the_worker_survives_an_unreachable_server(self, app, gotify_server):
    app.logger.gotify_url = gotify_server.url
    app.logger.gotify_token = "test-token"
    gotify_server.shutdown()                     # the server disappears mid-flight
    app.logger.error("into the void")
    logger_module.flush_gotify_queue(timeout=5)
    assert logger_module._gotify_worker.is_alive()
    app.logger.error("still logging afterwards")

  def test_no_notification_without_a_url_or_token(self, app, gotify_server):
    app.logger.gotify_url = None
    app.logger.gotify_token = None
    app.logger.error("goes nowhere")
    logger_module.flush_gotify_queue(timeout=2)
    assert gotify_server.received == []


class TestLogRotation:
  """
  Every CustomLogger writes to the same file. Independent RotatingFileHandlers would
  each track the size on their own and rename the file out from under the others,
  which keep writing to the orphaned inode.
  """

  LOG_NAME = "rotation_test.log"

  @pytest.fixture
  def logs_dir(self, monkeypatch):
    directory = Path(logger_module.__file__).resolve().parent.parent / "logs"
    for stale in directory.glob(f"{self.LOG_NAME}*"):
      stale.unlink()
    monkeypatch.setenv("LOG_MAX_BYTES", "20000")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "3")
    logger_module._file_handlers.pop(self.LOG_NAME, None)
    yield directory
    logger_module._file_handlers.pop(self.LOG_NAME, None)
    for created in directory.glob(f"{self.LOG_NAME}*"):
      created.unlink()

  def make_logger(self, name):
    instance = CustomLogger(name, logfile_name=self.LOG_NAME)
    instance.gotify_url = None
    instance.gotify_token = None
    return instance

  def rotated_files(self, logs_dir):
    return sorted(path.name for path in logs_dir.glob(f"{self.LOG_NAME}*"))

  def test_the_file_rotates_and_is_capped(self, logs_dir):
    log = self.make_logger("RotationA")
    for index in range(300):
      log.info(f"filler {index} " + "x" * 120)

    files = self.rotated_files(logs_dir)
    assert len(files) > 1, "never rotated"
    assert len(files) <= 4, f"more than backupCount + 1 files: {files}"
    assert (logs_dir / self.LOG_NAME).stat().st_size <= 20000 * 1.2

  def test_all_loggers_share_one_handler(self, logs_dir):
    loggers = [self.make_logger(f"Rotation{suffix}") for suffix in "BCD"]
    handlers = {id(handler) for log in loggers for handler in log.logger.handlers
                if isinstance(handler, RotatingFileHandler)}
    assert len(handlers) == 1

  def test_the_shared_handler_does_not_impose_its_level(self, logs_dir):
    log = self.make_logger("RotationE")
    handler = next(h for h in log.logger.handlers
                   if isinstance(h, RotatingFileHandler))
    assert handler.level == logging.NOTSET

  def test_interleaved_writers_all_survive_rotation(self, logs_dir):
    loggers = [self.make_logger(f"Rotation{suffix}") for suffix in "FGH"]
    for index in range(200):
      for tag, log in zip("FGH", loggers):
        log.info(f"{tag}-{index} " + "x" * 100)

    body = ""
    for path in sorted(logs_dir.glob(f"{self.LOG_NAME}*")):
      if path.suffix == ".gz":
        body += gzip.open(path, "rt", encoding="utf-8", errors="replace").read()
      else:
        body += path.read_text(encoding="utf-8", errors="replace")
    for tag in "FGH":
      assert f"{tag}-199" in body, f"writer {tag} lost its last line"
    assert len(self.rotated_files(logs_dir)) <= 4

  def test_rotated_files_are_gzipped_and_readable(self, logs_dir):
    log = self.make_logger("RotationI")
    for index in range(300):
      log.info(f"filler {index} " + "x" * 120)

    archives = sorted(logs_dir.glob(f"{self.LOG_NAME}.*.gz"))
    assert archives, "nothing was compressed"
    text = gzip.open(archives[0], "rt", encoding="utf-8").read()
    assert "[Rotation" in text
    assert not (logs_dir / archives[0].name[:-3]).exists(), "uncompressed leftover"

  def test_the_active_file_stays_plain_for_tailing(self, logs_dir):
    log = self.make_logger("RotationJ")
    for index in range(300):
      log.info(f"filler {index} " + "x" * 120)
    assert (logs_dir / self.LOG_NAME).exists()
    assert not (logs_dir / f"{self.LOG_NAME}.gz").exists()

  def test_emoji_survive_the_host_locale(self, logs_dir):
    log = self.make_logger("RotationK")
    log.info("probe 🚀 ✅ ❌ 📥")
    assert "probe 🚀" in (logs_dir / self.LOG_NAME).read_text(encoding="utf-8")


class TestCSVAccessLog:
  """
  get_csv_file_logger: one CSV row per record, for events meant to be grepped, awked
  or loaded with pandas rather than read by eye — access_logger's unauthenticated-hit
  lines being the motivating case.

  The one property that matters most is the one easiest to get wrong silently: a
  hand-joined "field,field,field" line looks fine until a field contains a comma or a
  quote, at which point it corrupts that row and shifts every column after it. Several
  candidate fields here (a request path, a message) are attacker-controlled, so this
  is proven by round-tripping through csv.reader, not just by not-crashing.
  """

  LOG_NAME = "csv_test.log"

  @pytest.fixture
  def logs_dir(self, monkeypatch):
    directory = Path(logger_module.__file__).resolve().parent.parent / "logs"
    for stale in directory.glob(f"{self.LOG_NAME}*"):
      stale.unlink()
    monkeypatch.setenv("LOG_MAX_BYTES", "20000")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "3")
    logger_module._file_handlers.pop(self.LOG_NAME, None)
    yield directory
    logger_module._file_handlers.pop(self.LOG_NAME, None)
    for created in directory.glob(f"{self.LOG_NAME}*"):
      created.unlink()

  def make_logger(self, name, fields=("client_ip", "method", "path")):
    return get_csv_file_logger(name, self.LOG_NAME, fields=fields)

  def rows(self, logs_dir):
    """Every row in the current file, parsed back through csv.reader."""
    text = (logs_dir / self.LOG_NAME).read_text(encoding="utf-8")
    return list(csv.reader(io.StringIO(text)))

  def test_the_header_names_every_column(self, logs_dir):
    self.make_logger("CSVHeader").info("probe", extra={"client_ip": "1.1.1.1"})
    header, *_ = self.rows(logs_dir)
    assert header == ["timestamp", "level", "client_ip", "method", "path", "message"]

  def test_a_plain_row_carries_its_fields_in_order(self, logs_dir):
    self.make_logger("CSVPlain").info(
      "Unauthenticated access attempt",
      extra={"client_ip": "203.0.113.9", "method": "GET", "path": "/"}
    )
    _, row = self.rows(logs_dir)
    assert row[2:] == ["203.0.113.9", "GET", "/", "Unauthenticated access attempt"]

  def test_a_comma_in_a_field_round_trips_to_the_original_value(self, logs_dir):
    """The failure a hand-joined line could not survive."""
    poisoned = "/balance/bot,1"
    self.make_logger("CSVComma").info(
      "hit", extra={"client_ip": "1.2.3.4", "method": "GET", "path": poisoned}
    )
    _, row = self.rows(logs_dir)
    assert row[4] == poisoned
    assert len(row) == 6, f"a comma inside the field split it into extra columns: {row}"

  def test_a_quote_in_a_field_round_trips_to_the_original_value(self, logs_dir):
    poisoned = 'path with "quotes" in it'
    self.make_logger("CSVQuote").info(
      "hit", extra={"client_ip": "1.2.3.4", "method": "GET", "path": poisoned}
    )
    _, row = self.rows(logs_dir)
    assert row[4] == poisoned

  def test_a_comma_in_the_message_itself_also_round_trips(self, logs_dir):
    self.make_logger("CSVMsgComma").info("hit, with a comma in the message itself")
    _, row = self.rows(logs_dir)
    assert row[-1] == "hit, with a comma in the message itself"

  def test_a_call_missing_extra_fields_does_not_raise(self, logs_dir):
    """Logging a scanner hit must never itself be the thing that throws."""
    self.make_logger("CSVMissing").info("no extra supplied at all")
    _, row = self.rows(logs_dir)
    assert row[2:5] == ["", "", ""]
    assert row[-1] == "no extra supplied at all"

  def test_the_header_appears_exactly_once_across_repeated_construction(self, logs_dir):
    """Same file requested by name twice must return the shared handler, not reopen it."""
    self.make_logger("CSVOnceA").info("first")
    self.make_logger("CSVOnceB").info("second")
    header_lines = [r for r in self.rows(logs_dir) if r and r[0] == "timestamp"]
    assert len(header_lines) == 1

  def test_a_preexisting_nonempty_file_is_not_given_a_second_header(self, logs_dir):
    """
    The restart case: the process-local handler cache is gone, but the file on disk
    already has content, so the header must not be written again.
    """
    (logs_dir / self.LOG_NAME).write_text(
      "timestamp,level,client_ip,method,path,message\n"
      "2026-01-01 00:00:00,INFO,9.9.9.9,GET,/,pre-existing line\n",
      encoding="utf-8",
    )
    logger_module._file_handlers.pop(self.LOG_NAME, None)  # simulate a fresh process
    self.make_logger("CSVRestart").info(
      "after restart", extra={"client_ip": "1.1.1.1", "method": "GET", "path": "/"}
    )
    header_lines = [r for r in self.rows(logs_dir) if r and r[0] == "timestamp"]
    assert len(header_lines) == 1

  def test_rows_are_not_separated_by_blank_lines(self, logs_dir):
    """
    csv.writer's own line terminator must be suppressed: the handler's stream already
    adds one newline per emit, and stacking both produces a blank row between every
    real one.
    """
    log = self.make_logger("CSVNoBlank")
    log.info("one")
    log.info("two")
    lines = (logs_dir / self.LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert "" not in lines


class TestLogConfigParsing:
  @pytest.mark.parametrize("value", ["not-a-number", "", "  ", None])
  def test_unparseable_sizes_fall_back(self, monkeypatch, value):
    if value is None:
      monkeypatch.delenv("LOG_MAX_BYTES", raising=False)
    else:
      monkeypatch.setenv("LOG_MAX_BYTES", value)
    assert logger_module._int_env("LOG_MAX_BYTES", 1234) == 1234

  @pytest.mark.parametrize("value,expected", [
    ("true", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("nonsense", False),
  ])
  def test_boolean_settings(self, monkeypatch, value, expected):
    monkeypatch.setenv("LOG_COMPRESS_ROTATED", value)
    assert logger_module._bool_env("LOG_COMPRESS_ROTATED", True) is expected
