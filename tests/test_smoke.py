"""
Foundational checks on the test harness itself.

If these fail, every other result in the suite is suspect: they confirm the app was
imported with the pinned test configuration rather than the developer's real .env, and
that nothing reaches the network.
"""

import pytest
import requests

from conftest import DASHBOARD_PASSWORD, DASHBOARD_USERNAME, WEBHOOK_PATH


def test_app_uses_pinned_test_configuration(app):
  """The real .env must not leak into the suite."""
  assert app.DASHBOARD_USERNAME == DASHBOARD_USERNAME
  assert app.DASHBOARD_PASSWORD == DASHBOARD_PASSWORD
  assert app.WEBHOOK_PATH == WEBHOOK_PATH


def test_public_ip_lookup_did_not_touch_the_network(app):
  """_fetch_public_ip() runs at import; it must have failed closed, not dialled out."""
  assert app.SERVER_PUBLIC_IP == "unavailable"


def test_outbound_get_is_blocked():
  with pytest.raises(requests.exceptions.ConnectionError):
    requests.get("https://example.invalid")


async def test_app_answers_requests(client_factory, app):
  """The ASGI stack is wired up and the peer address reaches the app."""
  app.TRUSTED_IPS = ["10.9.9.9"]
  async with client_factory(peer="10.9.9.9") as client:
    response = await client.get("/")
  assert response.status_code == 200


class TestStateIsolation:
  """The autouse fixture must undo whatever a test changes."""

  def test_mutates_settings(self, app, crypto_trader):
    app.TRUSTED_IPS = ["1.2.3.4"]
    app.WEBHOOK_REQUIRE_HTTPS = False
    assert "fakebot" in app.traders

  def test_sees_none_of_it(self, app):
    assert app.TRUSTED_IPS == []
    assert app.WEBHOOK_REQUIRE_HTTPS is True
    assert app.traders == {}
