"""
Unit tests for src/misc/replay_guard.py.

Two invariants here are easy to break by accident and produce no visible symptom:
the fingerprint TTL must outlive the signal's own freshness, and every accepted
timestamp representation must resolve to the same UTC instant regardless of the host
timezone.
"""

import asyncio
import json
import logging
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from src.misc.replay_guard import (ReplayGuard, parse_signal_timestamp,
                                   signal_fingerprint)

INSTANT = datetime(2026, 8, 17, 10, 31, 44, tzinfo=timezone.utc)
EPOCH = int(INSTANT.timestamp())


@pytest.fixture
def guard(tmp_path):
  return ReplayGuard(tmp_path / "replay.json", 300, logging.getLogger("guard-test"))


class TestTimestampParsing:
  @pytest.mark.parametrize("raw", [
    "2026-08-17T10:31:44Z",
    "2026-08-17T10:31:44.000Z",
    "2026-08-17T10:31:44+00:00",
    "2026-08-17T18:31:44+08:00",
    "2026-08-17T06:31:44-04:00",
    "2026-08-17T10:31:44",          # naive, read as UTC
    EPOCH,
    EPOCH * 1000,                   # milliseconds
    str(EPOCH),
    float(EPOCH),
  ], ids=["iso-z", "iso-z-millis", "iso-offset-zero", "iso-offset-plus8",
          "iso-offset-minus4", "iso-naive", "unix-s", "unix-ms", "unix-str",
          "unix-float"])
  def test_every_form_resolves_to_the_same_instant(self, raw):
    assert parse_signal_timestamp(raw) == INSTANT

  def test_result_is_always_timezone_aware(self):
    for raw in (EPOCH, "2026-08-17T10:31:44Z", "2026-08-17T10:31:44"):
      assert parse_signal_timestamp(raw).tzinfo is not None

  def test_offsets_are_normalised_to_utc(self):
    """
    The handler formats this value as "%H:%M:%S UTC". Keeping the sender's offset
    would print their local wall clock under a UTC label.
    """
    parsed = parse_signal_timestamp("2026-08-17T18:31:44+08:00")
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-17 10:31:44"

  @pytest.mark.parametrize("raw", [
    None, "", "   ", "not-a-time", True, False, [], {}, "2026-13-45T99:99:99Z",
  ], ids=["none", "empty", "blank", "garbage", "true", "false", "list", "dict",
          "impossible-date"])
  def test_unusable_values_return_none_rather_than_raising(self, raw):
    assert parse_signal_timestamp(raw) is None

  def test_booleans_are_not_treated_as_epoch_numbers(self):
    """bool is a subclass of int; True must not silently mean 1970-01-01T00:00:01Z."""
    assert parse_signal_timestamp(True) is None


class TestFingerprint:
  def test_identical_bytes_give_the_same_key(self):
    body = b'{"a": 1}'
    assert signal_fingerprint("bot", body) == signal_fingerprint("bot", body)

  def test_different_bodies_differ(self):
    assert signal_fingerprint("bot", b'{"a": 1}') != signal_fingerprint("bot", b'{"a": 2}')

  def test_same_body_for_different_bots_differs(self):
    body = b'{"a": 1}'
    assert signal_fingerprint("bot-a", body) != signal_fingerprint("bot-b", body)

  def test_whitespace_changes_the_key(self):
    """Byte-exact: a re-serialised body is a different signal, not a replay."""
    assert signal_fingerprint("bot", b'{"a":1}') != signal_fingerprint("bot", b'{"a": 1}')


class TestRegistration:
  async def test_first_use_is_accepted_and_repeats_are_not(self, guard):
    assert await guard.register("fingerprint-a") is True
    assert await guard.register("fingerprint-a") is False
    assert await guard.register("fingerprint-a") is False

  async def test_distinct_fingerprints_are_independent(self, guard):
    assert await guard.register("a") is True
    assert await guard.register("b") is True

  async def test_concurrent_duplicates_cannot_both_pass(self, guard):
    """Check and insert happen under one lock."""
    results = await asyncio.gather(*(guard.register("same") for _ in range(20)))
    assert results.count(True) == 1
    assert results.count(False) == 19

  async def test_entries_expire(self, tmp_path):
    quick = ReplayGuard(tmp_path / "quick.json", 1, logging.getLogger("expiry"))
    assert await quick.register("short-lived") is True
    await asyncio.sleep(1.1)
    assert await quick.register("short-lived") is True

  async def test_expired_entries_never_cause_a_false_rejection(self, tmp_path):
    quick = ReplayGuard(tmp_path / "quick.json", 1, logging.getLogger("expiry"))
    await quick.register("old")
    await asyncio.sleep(1.1)
    assert await quick.register("brand-new") is True


class TestPersistence:
  async def test_survives_a_restart(self, tmp_path):
    path = tmp_path / "replay.json"
    first = ReplayGuard(path, 300, logging.getLogger("restart"))
    await first.register("remembered")
    assert first.persistent is True

    second = ReplayGuard(path, 300, logging.getLogger("restart"))   # new process
    assert await second.register("remembered") is False
    assert await second.register("never-seen") is True

  async def test_state_is_written_as_readable_json(self, tmp_path):
    path = tmp_path / "replay.json"
    guard = ReplayGuard(path, 300, logging.getLogger("json"))
    await guard.register("abc")
    stored = json.loads(path.read_text())
    assert "abc" in stored
    assert isinstance(stored["abc"], (int, float))

  async def test_expired_entries_are_dropped_on_load(self, tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"ancient": 1.0, "also-ancient": 2.0}))
    guard = ReplayGuard(path, 300, logging.getLogger("prune"))
    assert json.loads(path.read_text()) == {}
    assert await guard.register("ancient") is True

  async def test_corrupt_state_file_does_not_stop_the_guard(self, tmp_path):
    path = tmp_path / "replay.json"
    path.write_text("this is not json")
    guard = ReplayGuard(path, 300, logging.getLogger("corrupt"))
    assert await guard.register("still-works") is True

  async def test_unwritable_path_degrades_to_memory(self, tmp_path):
    guard = ReplayGuard("/proc/definitely/not/writable/replay.json", 300,
                        logging.getLogger("degraded"))
    assert guard.persistent is False
    assert await guard.register("k") is True
    assert await guard.register("k") is False   # still refuses replays in memory

  async def test_no_path_at_all_degrades_to_memory(self):
    guard = ReplayGuard(None, 300, logging.getLogger("nopath"))
    assert guard.persistent is False
    assert await guard.register("k") is True
    assert await guard.register("k") is False


class TestTTLOutlivesFreshness:
  """
  A signal dated in the future is accepted and stays fresh until timestamp + window,
  which can be 2x the window after acceptance. A shorter TTL made it replayable in the
  gap between the fingerprint expiring and the signal going stale.
  """

  def test_app_sizes_the_ttl_at_twice_the_window(self, app):
    assert app.replay_guard.ttl_seconds == app.WEBHOOK_MAX_AGE_S * 2

  async def test_fingerprint_outlives_one_window(self, tmp_path):
    window = 2
    guard = ReplayGuard(tmp_path / "edge.json", window * 2, logging.getLogger("edge"))
    assert await guard.register("future-dated") is True
    await asyncio.sleep(window + 0.5)          # past the old, too-short expiry
    assert await guard.register("future-dated") is False


TIMEZONE_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {repo!r})
    from datetime import datetime, timezone
    from src.misc.replay_guard import parse_signal_timestamp
    forms = {forms!r}
    print(json.dumps([parse_signal_timestamp(f).isoformat() for f in forms]))
""")


class TestTimezoneIndependence:
  """
  The parser this replaced mixed naive-local and aware values, so the freshness check
  would have measured age against the host's UTC offset — or raised outright.
  """

  FORMS = ["2026-08-17T10:31:44Z", "2026-08-17T18:31:44+08:00", EPOCH, EPOCH * 1000,
           str(EPOCH), "2026-08-17T10:31:44"]

  @pytest.mark.parametrize("tz", ["UTC", "Asia/Singapore", "America/New_York",
                                  "Australia/Sydney", "Pacific/Kiritimati"])
  def test_parsing_is_identical_in_every_host_timezone(self, tz, request):
    repo_root = str(request.config.rootpath)
    script = TIMEZONE_PROBE.format(repo=repo_root, forms=self.FORMS)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, env={"TZ": tz, "PATH": "/usr/bin:/bin"},
                            check=True, cwd=repo_root)
    parsed = json.loads(result.stdout)
    assert parsed == [INSTANT.isoformat()] * len(self.FORMS), f"differs under TZ={tz}"

  def test_freshness_verdict_does_not_depend_on_representation(self):
    now = datetime.now(timezone.utc)
    window = 300
    for offset, expected_fresh in [(-30, True), (-299, True), (-400, False),
                                   (30, True), (400, False)]:
      moment = now + timedelta(seconds=offset)
      as_iso = parse_signal_timestamp(moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
      as_unix = parse_signal_timestamp(int(moment.timestamp()))
      for parsed in (as_iso, as_unix):
        age = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        assert (age <= window) is expected_fresh, f"offset {offset}s"
