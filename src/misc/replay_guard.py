"""
Webhook replay protection.

Trading signals authenticate with a bearer key carried inside the JSON body, so a
single captured request would otherwise be a permanent trading capability: the same
bytes replay forever and place a real order every time. This module provides the two
defences the webhook handler applies before any order is placed:

  * a freshness window — the signal's own timestamp must be close to now
  * an idempotency cache — a body that was already accepted is never accepted twice

The cache is persisted to disk so that a restart (or `docker-compose down && up`)
does not reopen the window for signals accepted just before it.
"""

# Standard library imports
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

# Values above this many seconds are unix milliseconds rather than unix seconds
# (10 digits covers every second-precision timestamp until the year 2286)
_MAX_UNIX_SECONDS = 9999999999


def parse_signal_timestamp(raw):
  """
  Parse a webhook `timestamp` field into a timezone-aware UTC datetime.

  Accepts unix seconds, unix milliseconds, either of those as a numeric string, and
  ISO 8601 with or without a trailing 'Z'. ISO values without an offset are read as
  UTC. Values carrying another offset are converted to UTC rather than kept in their
  original zone, so callers can format the result as UTC without relabelling the wrong
  wall-clock time. Returns None when the value is missing or cannot be parsed — the
  caller decides whether that is fatal.
  """
  if raw is None or isinstance(raw, bool) or raw == '':
    return None
  try:
    if isinstance(raw, (int, float)):
      return _from_epoch(raw)
    if isinstance(raw, str):
      text = raw.strip()
      try:
        return _from_epoch(float(text))
      except ValueError:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
          return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
  except (ValueError, OSError, OverflowError):
    return None
  return None


def _from_epoch(value: float) -> datetime:
  """Build an aware UTC datetime from a unix timestamp in seconds or milliseconds."""
  seconds = value / 1000 if abs(value) > _MAX_UNIX_SECONDS else value
  return datetime.fromtimestamp(seconds, tz=timezone.utc)


def signal_fingerprint(trader_id: str, body: bytes) -> str:
  """
  Build the idempotency key for one signal.

  Hashes the exact bytes that were received, so a replay of a captured request always
  produces the same key while a genuinely new signal (different timestamp) does not.
  """
  digest = hashlib.sha256()
  digest.update(str(trader_id).encode('utf-8'))
  digest.update(b'\x00')
  digest.update(body or b'')
  return digest.hexdigest()


class ReplayGuard:
  """
  Remembers recently accepted signal fingerprints and refuses repeats.

  Entries expire `ttl_seconds` after they were accepted, and expired entries are
  dropped before every membership test, so the cache stays small and a stale entry can
  never cause a false rejection. The caller must size `ttl_seconds` so that an entry
  outlives its signal's freshness: a signal accepted while dated in the future goes on
  passing a +/-W freshness check until W past its own timestamp, which can be up to 2W
  after it was accepted. A shorter TTL would make it replayable again in between.

  State is rewritten atomically after every accepted signal. If the path cannot be used
  the guard keeps working in memory and warns once — losing persistence is far better
  than refusing to trade.
  """

  def __init__(self, path, ttl_seconds: int, logger):
    self.path = Path(path) if path else None
    self.ttl_seconds = max(int(ttl_seconds), 1)
    self.logger = logger
    self.persistent = False
    self._lock = asyncio.Lock()
    self._seen = {}
    self._load()

  def _load(self) -> None:
    """Read any previously stored fingerprints and confirm the path is writable."""
    if self.path is None:
      self.logger.warning(
        "Webhook replay cache has no path configured — running in memory only. "
        "Signals accepted before a restart could be replayed after it."
      )
      return
    try:
      self.path.parent.mkdir(parents=True, exist_ok=True)
      if self.path.exists():
        with open(self.path, 'r', encoding='utf-8') as handle:
          stored = json.load(handle)
        if isinstance(stored, dict):
          self._seen = {
            key: float(expiry) for key, expiry in stored.items()
            if isinstance(key, str) and isinstance(expiry, (int, float))
          }
      self._prune(_now_epoch())
      self._write()
      self.persistent = True
      self.logger.info(
        f"Webhook replay cache ready at {self.path} "
        f"({len(self._seen)} live entr{'y' if len(self._seen) == 1 else 'ies'}, "
        f"TTL {self.ttl_seconds}s)."
      )
    except (OSError, ValueError, TypeError) as exc:
      self.logger.warning(
        f"Webhook replay cache at {self.path} is unusable ({exc}) — running in memory "
        "only. Signals accepted before a restart could be replayed after it."
      )

  def _prune(self, now: float) -> None:
    """Drop every fingerprint whose entry has expired."""
    self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}

  def _write(self) -> None:
    """Persist the cache, replacing the file atomically so a crash cannot truncate it."""
    if self.path is None:
      return
    tmp_path = self.path.with_suffix(self.path.suffix + '.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as handle:
      json.dump(self._seen, handle)
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(tmp_path, self.path)

  async def register(self, fingerprint: str) -> bool:
    """
    Record a signal fingerprint, returning False when it has been seen before.

    Check and insert happen under one lock so two concurrent copies of the same
    request cannot both pass.
    """
    async with self._lock:
      now = _now_epoch()
      self._prune(now)
      if fingerprint in self._seen:
        return False
      self._seen[fingerprint] = now + self.ttl_seconds
      if self.persistent:
        try:
          self._write()
        except OSError as exc:
          self.persistent = False
          self.logger.warning(
            f"Webhook replay cache could no longer be written ({exc}) — continuing in "
            "memory only."
          )
      return True


def _now_epoch() -> float:
  """Current time as a unix timestamp, in UTC."""
  return datetime.now(timezone.utc).timestamp()
