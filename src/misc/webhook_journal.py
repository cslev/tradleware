"""Bounded, non-paging record of webhook requests rejected before a bot's own trust
boundary is established — malformed JSON, an unknown trader_id, a wrong API key.

Distinct from two existing mechanisms that look similar but solve a different problem:

  * rejection_reporter logs a short, flood-collapsed summary line ("Rejected webhook
    from X: reason") and pages Gotify on the first occurrence. Its `detail` parameter
    is explicitly documented to never carry attacker-supplied text, because that line
    is shared with every other log message and read aloud by a notification — exactly
    the wrong place for an arbitrary request body.
  * order_journal.py's orders.jsonl is a permanent, never-rotated record of orders
    Tradleware actually attempted. A malformed body that never resolves to a known,
    authenticated bot is not an order attempt, and giving it permanent retention would
    let anyone who can reach the endpoint grow that file without bound.

This module exists for the question those two cannot answer: "what did the request
actually contain?" It answers it in its own rotating, size-capped, Gotify-disconnected
file — so the answer is available without reintroducing either problem above. Once a
request clears authentication, the full payload is logged directly to that bot's own
logger instead (see handle_webhook in app.py) — that line is the normal, no-longer-attacker-
only case, and needs none of the caution here.
"""
import json
import re
from datetime import datetime, timezone

from src.misc.logger import get_jsonl_file_logger

# Generous for any real webhook payload (TradingView alerts are a few hundred bytes of
# JSON), bounded against a request built specifically to write a huge line.
_MAX_PAYLOAD_CHARS = 2000

# Read before it is even decoded, so a pathological body is not fully turned into text
# just to be thrown away a line later.
_MAX_RAW_BYTES = _MAX_PAYLOAD_CHARS * 4  # UTF-8 worst case, 4 bytes per character

# Best-effort redaction for text that never parsed as JSON, so a near-valid body with
# one stray comma still cannot leak a real api_key value into this file.
_API_KEY_PATTERN = re.compile(r'("api_key"\s*:\s*")[^"]*(")', re.IGNORECASE)

_journal = get_jsonl_file_logger('RejectedWebhooks', 'rejected_webhooks.jsonl')


def _redact_text(text: str) -> str:
  return _API_KEY_PATTERN.sub(r'\1***\2', text)


def _render(data, raw_body: bytes) -> str:
  """
  Build the safest available representation of what was received, redacted and capped.

  Dict-level redaction is used whenever `data` is a dict — exact, not a regex guess.
  Anything else (JSON that parsed to something other than an object, or JSON that did
  not parse at all) falls back to the text-level redaction above.
  """
  if isinstance(data, dict):
    redacted = {**data, 'api_key': '***'} if 'api_key' in data else data
    rendered = json.dumps(redacted, default=str)
  elif data is not None:
    rendered = json.dumps(data, default=str)  # valid JSON, not an object: no api_key possible
  elif raw_body:
    rendered = _redact_text(raw_body[:_MAX_RAW_BYTES].decode('utf-8', errors='replace'))
  else:
    rendered = ''

  if len(rendered) > _MAX_PAYLOAD_CHARS:
    rendered = f"{rendered[:_MAX_PAYLOAD_CHARS]}...<truncated, {len(rendered)} chars total>"
  return rendered


def record_rejected_webhook(reason: str, client_ip: str, trader_id=None,
                            *, data=None, raw_body: bytes = None) -> None:
  """
  Append one JSON line describing a webhook rejected before authentication succeeded.

  Carries no flood protection of its own — call it only when
  `rejection_reporter.record(...)` for the same rejection returned True. That function
  already collapses repeats of the same (reason, source) within its window and returns
  whether it actually logged, so gating on it means a flood writes exactly as many
  journal lines as it writes shared-log lines: one, not one per request. Reusing that
  decision also keeps this file's rotation budget holding distinct history instead of
  being burned through by repeats of a single complaint.

  `reason` should be that same phrase, so the two logs can be cross-referenced. Pass
  `data` when the body parsed as JSON (even if it was not a dict) and `raw_body` when
  it did not — the caller does not need to decide how to redact either; this always does.
  """
  record = {
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "client_ip": client_ip,
    "trader_id": trader_id,
    "reason": reason,
    "payload": _render(data, raw_body),
  }
  _journal.info(json.dumps(record, default=str))
