"""
Flood-resistant reporting of rejected webhook requests.

Every rejected webhook writes a log line and — because rejections are logged at ERROR —
pushes a Gotify notification. Both are unbounded, and anyone who knows the webhook path
can drive them. Two things break as a result:

  * The log file rotates on size, so an attacker's noise *evicts* real history. At a
    modest 100 requests per second the entire retained log is overwritten in about an
    hour, taking the record of their own activity with it.
  * Notifications become useless. Hundreds of thousands of pushes an hour bury the
    alerts that matter, which is how a genuine order failure goes unnoticed.

This module keeps the signal and drops the repetition. The first occurrence of each
distinct problem is always reported in full and immediately, so nothing is hidden and a
one-off misconfiguration still surfaces at once. Repeats are counted and collapsed into
a single periodic summary.

Nothing here changes what the webhook accepts or rejects. It only governs how loudly a
rejection is announced.
"""

# Standard library imports
import time

# Full log lines emitted per window before everything is collapsed into the summary.
# Without this an attacker rotating source addresses would still get one line and one
# notification per address.
MAX_REPORTED_PER_WINDOW = 5

# Distinct (reason, source) pairs counted individually. Beyond this the counts are
# merged into an "other sources" total so memory cannot grow with attacker input.
MAX_TRACKED_KEYS = 64

# Sources named individually in the summary line, most frequent first.
MAX_SOURCES_IN_SUMMARY = 5


class RejectionReporter:
  """
  Reports webhook rejections, collapsing repeats into one periodic summary.

  Safe to call from async handlers without a lock: every method runs to completion
  without awaiting, so the event loop cannot interleave two of them.
  """

  def __init__(self, logger, summary_interval_s: int = 300):
    self.logger = logger
    self.summary_interval_s = max(int(summary_interval_s), 1)
    self._reported = set()      # (reason, source) already logged in full this window
    self._suppressed = {}       # (reason, source) -> count of collapsed repeats
    self._untracked = 0         # repeats beyond MAX_TRACKED_KEYS
    self._window_started = time.monotonic()

  def record(self, reason: str, source: str, detail: str = "", logger=None) -> bool:
    """
    Note a rejection, logging it in full only if it is new in this window.

    `reason` is a short stable phrase used to group like problems. `source` is the
    client address. `detail` is appended to the first report only, and must never
    contain attacker-supplied text — a summary of it, at most.

    `logger` overrides the destination for this one report. Rejections that belong to a
    known bot pass that bot's logger, so they still appear in its Recent Logs tab on
    the dashboard; counting and suppression stay global either way.

    Returns True when the rejection was logged, False when it was collapsed.
    """
    key = (reason, str(source))

    if key in self._reported:
      self._suppressed[key] = self._suppressed.get(key, 0) + 1
      return False

    if len(self._reported) >= MAX_REPORTED_PER_WINDOW:
      if len(self._suppressed) < MAX_TRACKED_KEYS:
        self._suppressed[key] = self._suppressed.get(key, 0) + 1
      else:
        self._untracked += 1
      return False

    self._reported.add(key)
    (logger or self.logger).error(
      f"Rejected webhook from {source}: {reason}.{' ' + detail if detail else ''}"
    )
    return True

  def due(self) -> bool:
    """True when the current window has elapsed and a summary is worth emitting."""
    return time.monotonic() - self._window_started >= self.summary_interval_s

  def pending(self) -> int:
    """How many rejections have been collapsed and not yet summarised."""
    return sum(self._suppressed.values()) + self._untracked

  def flush(self) -> bool:
    """
    Emit one summary for everything collapsed since the last flush, and start a new
    window. Emits nothing when there is nothing to report. Returns True if it logged.

    A single line, not one per source: the summary exists to replace a flood, so it
    must not become one.
    """
    total = self.pending()
    if not total:
      self._start_new_window()
      return False

    elapsed = int(time.monotonic() - self._window_started)
    ranked = sorted(self._suppressed.items(), key=lambda item: item[1], reverse=True)
    named = ", ".join(
      f"{reason} from {source} ({count:,})"
      for (reason, source), count in ranked[:MAX_SOURCES_IN_SUMMARY]
    )
    remainder = len(ranked) - MAX_SOURCES_IN_SUMMARY
    if remainder > 0:
      named += f", and {remainder} other source/reason combination(s)"
    if self._untracked:
      named += f", plus {self._untracked:,} from further sources not tracked separately"

    self.logger.error(
      f"Suppressed {total:,} further webhook rejection(s) in the last {elapsed}s: "
      f"{named}."
    )
    self._start_new_window()
    return True

  def _start_new_window(self) -> None:
    self._reported.clear()
    self._suppressed.clear()
    self._untracked = 0
    self._window_started = time.monotonic()
