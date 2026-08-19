"""
Secrets must not reach the logs or the rendered dashboard.

Four leaks were fixed here: the dashboard password and Gotify token at startup, the
submitted webhook key and the whole webhook payload at request time, and live exchange
keys rendered eight characters at each end on every dashboard load.
"""

import json
import logging

import pytest

from conftest import DASHBOARD_PASSWORD, STARTUP_OUTPUT, signal_payload

EXCHANGE_KEY = "K7QZ3XW9VJ2NMB5RTY8FGH4DCL6PSA0EUI1O"
TRADLEWARE_KEY = "P4WD8NKX2VQ7ZJ5MTB9RGH3FCL6YSA1EUI0O"


def longest_shared_run(secret, text):
  """Length of the longest substring of `secret` that appears anywhere in `text`."""
  return max((length for length in range(1, len(secret) + 1)
              if any(secret[start:start + length] in text
                     for start in range(len(secret) - length + 1))), default=0)


class TestStartupOutput:
  """STARTUP_OUTPUT is what the app printed while conftest imported it."""

  def test_dashboard_password_is_not_printed(self):
    assert DASHBOARD_PASSWORD not in STARTUP_OUTPUT

  def test_password_is_marked_hidden(self):
    assert "(password hidden)" in STARTUP_OUTPUT

  def test_gotify_token_state_is_reported_not_its_value(self):
    assert ("gotify_token: set" in STARTUP_OUTPUT
            or "gotify_token: not set" in STARTUP_OUTPUT)

  def test_a_configured_gotify_token_is_never_printed(self):
    """Constructing a logger with a token must not echo it."""
    import io
    import contextlib
    from src.misc.logger import CustomLogger
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
      CustomLogger("TokenProbe", gotify_url="http://example.invalid",
                   gotify_token="super-secret-gotify-token")
    assert "super-secret-gotify-token" not in buffer.getvalue()


class TestWebhookLogging:
  """
  caplog is pinned to DEBUG in these tests on purpose. At its default WARNING level it
  would capture nothing from these code paths, and every "secret is absent" assertion
  would pass without proving anything.
  """

  async def test_submitted_key_is_not_logged(self, client_factory, webhook_url,
                                             crypto_trader, caplog):
    """A near-miss guess is likely a real secret, and ERROR is pushed to Gotify."""
    caplog.set_level(logging.DEBUG)
    guess = "almost-the-right-key-000"
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(api_key=guess))
    assert response.status_code == 401
    assert caplog.text, "nothing was captured — the assertion below would be vacuous"
    assert guess not in caplog.text

  async def test_payload_log_redacts_the_api_key(self, client_factory, webhook_url,
                                                 crypto_trader, caplog):
    """The handler logs the whole payload for visibility; the key must be masked."""
    caplog.set_level(logging.DEBUG)
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200
    assert "Webhook received payload" in caplog.text
    assert crypto_trader.tradleware_api_key not in caplog.text
    assert '"api_key": "***"' in caplog.text


class TestDashboardMasking:
  @pytest.fixture
  def loaded_dashboard(self, app, crypto_trader, stock_trader):
    crypto_trader.api_key = EXCHANGE_KEY
    crypto_trader.tradleware_api_key = TRADLEWARE_KEY
    stock_trader.tradleware_api_key = TRADLEWARE_KEY
    app.TRUSTED_IPS = ["192.168.1.50"]
    return app

  async def test_mask_secret_shapes(self, app):
    assert app.mask_secret("abcdef0123456789ABCDEF0123456789") == "••••••••6789"
    assert app.mask_secret("organizations/1234/apiKeys/wxyz") == "••••••••wxyz"
    assert app.mask_secret("abcd1234") == "••••••••"       # too short to reveal any
    assert app.mask_secret("abcd12345") == "••••••••2345"
    assert app.mask_secret("") == "Not configured"
    assert app.mask_secret(None) == "Not configured"

  async def test_mask_hides_the_length_of_the_secret(self, app):
    short = app.mask_secret("a" * 20)
    long = app.mask_secret("a" * 200)
    assert len(short) == len(long)

  async def test_no_long_fragment_of_a_key_is_rendered(self, client_factory,
                                                       loaded_dashboard):
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert response.status_code == 200
    for secret in (EXCHANGE_KEY, TRADLEWARE_KEY):
      assert secret not in response.text
      assert longest_shared_run(secret, response.text) <= 4

  async def test_the_masked_form_is_actually_shown(self, client_factory,
                                                   loaded_dashboard):
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert response.text.count("••••••••") >= 3   # crypto key + both webhook keys

  async def test_old_eight_plus_eight_slice_is_gone(self, client_factory,
                                                    loaded_dashboard):
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert f"{EXCHANGE_KEY[:8]}...{EXCHANGE_KEY[-8:]}" not in response.text

  async def test_webhook_example_uses_a_placeholder_not_the_real_key(
      self, client_factory, loaded_dashboard):
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "YOUR_BOT_TRADLEWARE_API_KEY" in response.text


class TestDefaultWebhookPathWarning:
  @pytest.fixture
  def dashboard(self, app, crypto_trader):
    app.TRUSTED_IPS = ["192.168.1.50"]
    return app

  async def test_warned_when_left_at_the_default(self, client_factory, dashboard):
    dashboard.USING_DEFAULT_WEBHOOK_PATH = True
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "Your webhook is on the default path" in response.text
    assert "pwgen -n 14 1" in response.text

  async def test_silent_once_randomised(self, client_factory, dashboard):
    dashboard.USING_DEFAULT_WEBHOOK_PATH = False
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "Your webhook is on the default path" not in response.text
    assert "scanners find it on their own" not in response.text

  def test_flag_tracks_the_configured_path(self, app):
    assert app.USING_DEFAULT_WEBHOOK_PATH is (app.WEBHOOK_PATH == "webhook")
