"""
Logging infrastructure: notification delivery and log file rotation.

Two properties that are invisible until they bite. Gotify used to be posted inline
from async request handlers, so one unauthenticated request froze the whole
application for the length of the round trip. And every CustomLogger builds its own
handler on the same file, which makes naive rotation corrupt itself.
"""

import asyncio
import gzip
import json
import logging
import time
from pathlib import Path

import pytest

from src.misc import logger as logger_module
from src.misc.logger import CustomLogger, RotatingFileHandler
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
