"""Durable, append-only record of every order Tradleware attempts to place.

Not a log — a JSONL journal. Each record is self-describing (crypto and stock
orders carry different fields) and every write is fsync'd, so a crash right
after a fill cannot lose that row. Never rotates: unlike the log files, this
is the only durable record of what was traded, and losing rows to rotation is
the exact failure this exists to fix. See current_state.md for the full design.
"""
import json
import os
import threading
from pathlib import Path

_JOURNAL_PATH = Path(__file__).resolve().parent.parent / "logs" / "orders.jsonl"
_write_lock = threading.Lock()


def record_order(**fields) -> None:
  """Append one JSON line describing an order attempt, durably."""
  line = json.dumps(fields, default=str) + "\n"
  with _write_lock:
    _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_JOURNAL_PATH, "a", encoding="utf-8") as journal_file:
      journal_file.write(line)
      journal_file.flush()
      os.fsync(journal_file.fileno())
