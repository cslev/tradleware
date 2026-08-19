"""
Per-source throttling of failed webhook authentication.

The webhook key is free-form text in a YAML file, and the endpoint evaluates a guess as
fast as it arrives. Against a one-million-word list that is a couple of hours' work, and
entropy checks cannot catch a key that is long enough but predictable. Throttling turns
that into weeks.

The design rule is that an attacker must not be able to use this to stop real trading.
Three things enforce it:

  * Counting is per source address, so an attacker cannot exhaust a budget shared with
    the genuine signal source. TradingView never sends a wrong key, so its address never
    accumulates failures in the first place, and an attacker cannot aim the throttle at
    a chosen bot — only at their own address.
  * A successful authentication clears that address immediately, so a source that was
    briefly misconfigured recovers the moment it is fixed.
  * The window is short and self-healing. There is no durable ban and nothing an
    operator has to unblock by hand.

The trade-off, stated plainly: once a source is over the limit it is refused *before*
its key is checked, so a valid signal from that same address is also refused until the
window clears. Checking the key first would remove the trade-off and the protection
along with it — the guessing would simply continue. Reaching that state requires one
address to send more failures in a window than any working configuration produces, and
it costs at most one window.

Exemptions are the caller's business — the webhook handler excuses loopback and
configured trusted addresses, so a local script or the kiosk can never be locked out.
"""

# Standard library imports
import time

# Distinct sources tracked at once. Beyond this the oldest entries are discarded, so
# attacker-chosen addresses cannot grow memory without bound. An attacker with enough
# addresses to overflow this is not being slowed by per-address limits anyway.
MAX_TRACKED_SOURCES = 1024


class FailureLimiter:
  """
  Counts recent authentication failures per source and reports when to stop answering.

  A fixed window rather than a rolling one: it costs a single timestamp and integer per
  source, and the worst case — a source getting up to twice the limit across a window
  boundary — is irrelevant at these thresholds.

  Safe to call from async handlers without a lock: no method awaits, so the event loop
  cannot interleave two of them.
  """

  def __init__(self, max_failures: int = 20, window_s: int = 60):
    self.max_failures = max(int(max_failures), 1)
    self.window_s = max(int(window_s), 1)
    self._failures = {}     # source -> [window_started, count]

  def is_blocked(self, source: str) -> bool:
    """True when this source has failed too often to be worth answering again yet."""
    entry = self._failures.get(str(source))
    if entry is None:
      return False
    window_started, count = entry
    if time.monotonic() - window_started >= self.window_s:
      return False        # the window has rolled over; the count no longer applies
    return count >= self.max_failures

  def record_failure(self, source: str) -> int:
    """Note a failed attempt and return the count within the current window."""
    key = str(source)
    now = time.monotonic()
    entry = self._failures.get(key)
    if entry is None or now - entry[0] >= self.window_s:
      if len(self._failures) >= MAX_TRACKED_SOURCES:
        self._forget_oldest()
      self._failures[key] = [now, 1]
      return 1
    entry[1] += 1
    return entry[1]

  def clear(self, source: str) -> None:
    """Forget a source's failures, called when it authenticates successfully."""
    self._failures.pop(str(source), None)

  def seconds_until_clear(self, source: str) -> int:
    """How long until a blocked source is answered again. Zero when not blocked."""
    entry = self._failures.get(str(source))
    if entry is None:
      return 0
    remaining = self.window_s - (time.monotonic() - entry[0])
    return max(int(remaining) + 1, 0) if remaining > 0 else 0

  def _forget_oldest(self) -> None:
    """Drop expired entries, or the single oldest if none have expired."""
    now = time.monotonic()
    live = {source: entry for source, entry in self._failures.items()
            if now - entry[0] < self.window_s}
    if live:
      self._failures = live
    if len(self._failures) >= MAX_TRACKED_SOURCES:
      oldest = min(self._failures, key=lambda source: self._failures[source][0])
      del self._failures[oldest]
