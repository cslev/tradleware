"""
Cross-site request forgery protection on state-changing endpoints.

`/convert` spends a bot's entire fiat balance and is authenticated. The session cookie
is covered by SameSite=lax, but the TRUSTED_IPS path carries no cookie for SameSite to
govern — so a page on any other site, loaded in a browser sitting on a trusted address,
could fire the request and have it succeed. It could not read the reply, but the
conversion still happened.

The guard is a required custom header. What actually stops a browser is the CORS
preflight that a custom header forces and Tradleware never answers; these tests check
the server half of that contract, since the browser half cannot be exercised here.
"""

import logging

import pytest

from conftest import DASHBOARD_HEADERS

TRUSTED = "192.168.1.50"


@pytest.fixture
def signed_in(app, crypto_trader):
  app.TRUSTED_IPS = [TRUSTED]
  return app


class TestConvertRequiresTheDashboardHeader:
  async def test_a_bare_cross_site_post_is_refused(self, client_factory, signed_in,
                                                   crypto_trader):
    """Exactly what a malicious page can send: a simple POST, no custom headers."""
    async with client_factory(peer=TRUSTED) as client:
      response = await client.post("/convert/fakebot",
                                   headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert crypto_trader.converted == [], "the conversion ran anyway"

  async def test_the_dashboard_request_still_works(self, client_factory, signed_in,
                                                   crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.post("/convert/fakebot", headers=DASHBOARD_HEADERS)
    assert response.status_code == 200
    assert crypto_trader.converted

  async def test_a_wrong_header_value_is_refused(self, client_factory, signed_in,
                                                 crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.post("/convert/fakebot",
                                   headers={"X-Tradleware-Request": "0"})
    assert response.status_code == 403

  async def test_it_applies_to_cookie_sessions_too(self, client_factory, app,
                                                   crypto_trader):
    """Belt and braces: SameSite already covers this path, but do not rely on it alone."""
    import urllib.parse
    app.TRUSTED_IPS = []
    from conftest import DASHBOARD_PASSWORD, DASHBOARD_USERNAME
    async with client_factory(scheme="https") as client:
      await client.post(
        "/login",
        content=urllib.parse.urlencode({"username": DASHBOARD_USERNAME,
                                        "password": DASHBOARD_PASSWORD}),
        headers={"content-type": "application/x-www-form-urlencoded"})
      without = await client.post("/convert/fakebot")
      with_header = await client.post("/convert/fakebot", headers=DASHBOARD_HEADERS)
    assert without.status_code == 403
    assert with_header.status_code == 200

  async def test_authentication_is_still_checked_first(self, client_factory, app,
                                                       crypto_trader):
    """An unauthenticated caller gets 401, not a hint about the header."""
    app.TRUSTED_IPS = []
    async with client_factory(peer="203.0.113.9") as client:
      response = await client.post("/convert/fakebot", headers=DASHBOARD_HEADERS)
    assert response.status_code == 401

  async def test_the_refusal_is_logged_with_the_origin(self, client_factory, signed_in,
                                                       crypto_trader, caplog):
    caplog.set_level(logging.DEBUG)
    async with client_factory(peer=TRUSTED) as client:
      await client.post("/convert/fakebot",
                        headers={"Origin": "https://evil.example"})
    assert "evil.example" in caplog.text
    assert "hard refresh" in caplog.text, "the message should not only blame an attack"


class TestReadOnlyEndpointsAreUnaffected:
  """
  These need no header: a cross-site page can send the request but cannot read the
  reply, and requiring one would only add preflights to the dashboard's own polling.
  """

  @pytest.mark.parametrize("path", [
    "/", "/balance/fakebot", "/price/fakebot", "/logs/fakebot",
  ])
  async def test_get_requests_work_without_the_header(self, client_factory, signed_in,
                                                      crypto_trader, path):
    import types
    crypto_trader.get_recent_logs = lambda: []
    crypto_trader.exchange = types.SimpleNamespace(fetch_ticker=lambda pair: None)

    async def safe(method, *args, **kwargs):
      return {"last": 1.0}

    crypto_trader._safe_api_call = safe
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get(path)
    assert response.status_code == 200


class TestNoPermissiveCORS:
  """
  The header check is only half the defence. The half that actually stops a browser is
  the CORS preflight failing, which happens because Tradleware answers no preflight at
  all. Add a permissive CORS middleware — an easy thing to reach for when wiring up an
  integration — and the browser starts allowing the cross-site request, with nothing in
  the CSRF code changing to warn you. These fail loudly if that ever happens.
  """

  async def test_a_preflight_is_not_approved(self, client_factory, signed_in,
                                             crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.request("OPTIONS", "/convert/fakebot", headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-tradleware-request",
      })
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-headers" not in response.headers
    assert "access-control-allow-credentials" not in response.headers

  async def test_no_cors_headers_on_an_ordinary_response(self, client_factory,
                                                         signed_in, crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert not [h for h in response.headers if h.lower().startswith("access-control")]

  def test_no_cors_middleware_is_installed(self, app):
    installed = [str(middleware.cls) for middleware in app.app.user_middleware]
    assert not any("CORS" in name for name in installed), \
      f"a CORS middleware would undo the CSRF protection: {installed}"


class TestTheDashboardSendsIt:
  def test_the_convert_button_sets_the_header(self):
    """The server guard is useless if the dashboard's own request lacks it."""
    from pathlib import Path
    js = Path("src/ui/static/js/main.js").read_text()
    convert_call = js[js.index("/convert/"):js.index("/convert/") + 500]
    assert "X-Tradleware-Request" in convert_call

  def test_the_header_name_matches_the_server(self, app):
    from pathlib import Path
    js = Path("src/ui/static/js/main.js").read_text()
    assert app.CSRF_HEADER in js
