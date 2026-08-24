"""
Untrusted text reaching the dashboard log pane.

`refreshLogs` builds each line into an innerHTML template. A rejected webhook echoes
its own `order_size` into the bot's log (app.py, "Invalid order_size value: '...'"),
and ccxt error strings embed raw exchange response bodies — so markup in either used
to execute in the operator's authenticated dashboard. That is an escalation, not just
a defacement: injected script shares the dashboard origin, so it can set the
X-Tradleware-Request header itself and drive /convert and the other CSRF-guarded
endpoints using nothing but a bot's webhook API key.

The fix escapes at the sink (escapeHtml in main.js), which covers every field rather
than the ones anyone thought to sanitise. These tests pin both halves: that the
payload really does reach the buffer, and that the renderer neutralises it.
"""

import re
from collections import deque
from pathlib import Path

import pytest

from conftest import signal_payload
from src.traders.crypto.base_crypto_trader import BaseCryptoTrader

MAIN_JS = Path(__file__).resolve().parents[1] / "src/ui/static/js/main.js"
TRUSTED = "192.168.1.50"   # matches the trusted peer the dashboard tests use

PAYLOADS = [
  "<img src=x onerror=alert(1)>",
  "<script>alert(1)</script>",
  "</span><img src=x onerror=alert(1)><span>",
  "\" onmouseover=\"alert(1)",
  "<svg/onload=alert(1)>",
]


@pytest.fixture(scope="module")
def source():
  return MAIN_JS.read_text(encoding="utf-8")


class TestTheSink:
  """main.js must not interpolate log text into innerHTML unescaped."""

  def test_an_escape_helper_exists(self, source):
    assert "function escapeHtml(" in source

  def test_every_innerHTML_interpolation_is_escaped(self, source):
    """Catches a future template that forgets the helper."""
    unescaped = []
    for line in source.splitlines():
      if "innerHTML" not in line and "<span class=\"log-" not in line:
        continue
      for expr in re.findall(r"\$\{([^}]*)\}", line):
        if "escapeHtml(" not in expr and expr.strip() != "safe":
          unescaped.append(expr.strip())
    assert unescaped == [], f"unescaped interpolations: {unescaped}"

  def test_the_log_renderer_emits_the_escaped_value(self, source):
    """Classification reads the raw line; only the escaped copy is rendered."""
    block = source[source.index("const coloredLogs"):source.index("debugLogElement.innerHTML")]
    assert "const safe = escapeHtml(log);" in block
    assert "${log}" not in block, "raw log still interpolated into a span"


@pytest.fixture
def signed_in(app):
  """Authenticate by trusted IP so this file does not depend on the cookie flow."""
  app.TRUSTED_IPS = [TRUSTED]
  return app


@pytest.fixture
def buffered_trader(crypto_trader):
  """
  Give the stub the real buffer wiring.

  The plain stub has no log_buffer, so it would swallow the payload and the test would
  pass for the wrong reason. Bind the genuine base-class methods instead of
  reimplementing them, so this exercises the same formatting the dashboard receives.
  """
  crypto_trader.log_buffer = deque(maxlen=50)
  crypto_trader.get_recent_logs = lambda: BaseCryptoTrader.get_recent_logs(crypto_trader)

  original_error = crypto_trader.logger.error

  def error_with_buffer(msg, *args, **kwargs):
    BaseCryptoTrader._add_to_buffer(crypto_trader, "ERROR", str(msg))
    return original_error(msg, *args, **kwargs)

  crypto_trader.logger.error = error_with_buffer
  return crypto_trader


class TestTheSource:
  """The webhook really does echo attacker text into the bot's log buffer."""

  @pytest.mark.parametrize("payload", PAYLOADS)
  async def test_a_rejected_order_size_reaches_the_log_buffer(
      self, client_factory, webhook_url, buffered_trader, payload):
    async with client_factory() as client:
      response = await client.post(
        webhook_url, json=signal_payload(order_size=payload, order_size_type="quantity"))

    assert response.status_code == 400
    logged = " ".join(str(entry) for entry in buffered_trader.log_buffer)
    assert payload in logged, "payload did not reach the buffer; test no longer proves anything"

  @pytest.mark.parametrize("payload", PAYLOADS)
  async def test_the_endpoint_serves_it_verbatim(self, client_factory, webhook_url,
                                                 buffered_trader, signed_in, payload):
    """
    /logs returns the raw text — escaping belongs at the sink, not here. Encoding it
    server-side would double-escape once the client escapes too, and would corrupt the
    plain-text log for any other consumer.
    """
    async with client_factory(peer=TRUSTED) as client:
      await client.post(webhook_url,
                        json=signal_payload(order_size=payload, order_size_type="quantity"))
      logs = await client.get("/logs/fakebot")

    assert logs.status_code == 200
    assert payload in " ".join(logs.json()["logs"])


@pytest.fixture(scope="module")
def escape(source):
  """
  A Python mirror of escapeHtml, pinned to the real JS so it cannot drift.

  Running the shipped JS would need a JS engine in the test env; asserting each mapping
  is present in the source and then modelling it here keeps the check honest without one.
  """
  body = source[source.index("function escapeHtml("):]
  body = body[:body.index("\n}") + 2]

  # Keys are quoted with either ' or " in JS depending on the character.
  table = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
  for key, value in table.items():
    pattern = rf"""['"]{re.escape(key)}['"]:\s*'{re.escape(value)}'"""
    assert re.search(pattern, body), f"missing mapping {key!r} -> {value!r} in:\n{body}"

  assert "/[&<>\"']/g" in body, f"replace() must cover all five characters:\n{body}"
  return lambda text: "".join(table.get(ch, ch) for ch in str(text))


class TestEscapeSemantics:
  """The helper's own contract, checked against the browser's rules."""

  @pytest.mark.parametrize("payload", PAYLOADS)
  def test_no_tag_survives_escaping(self, escape, payload):
    assert "<" not in escape(payload) and ">" not in escape(payload)

  def test_ampersand_is_escaped_first(self, escape):
    """Escaping '<' before '&' would turn '&lt;' into '&amp;lt;' — and '&amp;' into '&'."""
    assert escape("&lt;img&gt;") == "&amp;lt;img&amp;gt;"

  def test_quotes_are_escaped_so_attribute_context_cannot_break_out(self, escape):
    assert escape('" onmouseover="x') == "&quot; onmouseover=&quot;x"

  def test_ordinary_log_text_is_untouched(self, escape):
    line = "[09:30:36] ERROR: Rejected webhook from 1.2.3.4: invalid API key"
    assert escape(line) == line

  def test_the_day_divider_is_untouched(self, escape):
    """Escaping must not mangle the separator the renderer matches on."""
    assert escape("── 2026-08-24 ──") == "── 2026-08-24 ──"
