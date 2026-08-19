"""
Dashboard authentication: the login flow, the session cookie, and credential comparison.

Covers three fixes that interact:
  * a session cookie marked Secure, which browsers discard on plain HTTP
  * constant-time credential comparison, done on bytes so non-ASCII cannot crash it
  * trusted-IP access, which bypasses the cookie entirely
"""

import urllib.parse

import pytest

from conftest import DASHBOARD_PASSWORD, DASHBOARD_USERNAME

LOGIN_FORM = {"username": DASHBOARD_USERNAME, "password": DASHBOARD_PASSWORD}


async def post_login(client, username=DASHBOARD_USERNAME, password=DASHBOARD_PASSWORD):
  return await client.post(
    "/login",
    content=urllib.parse.urlencode({"username": username, "password": password}),
    headers={"content-type": "application/x-www-form-urlencoded"},
  )


def session_cookie(response):
  """The raw Set-Cookie header for the session, or '' when none was issued."""
  return next((value for value in response.headers.get_list("set-cookie")
               if value.startswith("session=")), "")


class TestLoginOverHTTPS:
  async def test_valid_credentials_sign_in(self, client_factory):
    async with client_factory(scheme="https") as client:
      response = await post_login(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

  async def test_wrong_password_is_refused(self, client_factory):
    async with client_factory(scheme="https") as client:
      response = await post_login(client, password="wrong")
    assert response.headers["location"].startswith("/login?error=")

  async def test_wrong_username_is_refused(self, client_factory):
    async with client_factory(scheme="https") as client:
      response = await post_login(client, username="someone-else")
    assert response.headers["location"].startswith("/login?error=")

  async def test_session_carries_through_to_the_dashboard(self, client_factory):
    """End to end: sign in, then reach a protected page with the cookie."""
    async with client_factory(scheme="https") as client:
      await post_login(client)
      response = await client.get("/")
    assert response.status_code == 200

  async def test_logout_ends_the_session(self, client_factory):
    async with client_factory(scheme="https") as client:
      await post_login(client)
      await client.get("/logout")
      response = await client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


class TestSessionCookieFlags:
  async def test_cookie_is_hardened(self, client_factory, app):
    async with client_factory(scheme="https") as client:
      response = await post_login(client)
    cookie = session_cookie(response).lower()
    assert cookie, "no session cookie was issued"
    assert "; secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"max-age={app.SESSION_MAX_AGE_S}" in cookie

  async def test_lifetime_is_not_starlettes_fourteen_day_default(self, client_factory):
    async with client_factory(scheme="https") as client:
      response = await post_login(client)
    assert "max-age=1209600" not in session_cookie(response).lower()


class TestLoginOverPlainHTTP:
  """A Secure cookie is dropped by the browser, so this must fail loudly."""

  async def test_refused_with_an_explanation(self, client_factory):
    async with client_factory(scheme="http") as client:
      response = await post_login(client)
    location = urllib.parse.unquote(response.headers["location"])
    assert location.startswith("/login?error=")
    assert "HTTPS" in location

  async def test_no_session_cookie_is_issued(self, client_factory):
    async with client_factory(scheme="http") as client:
      response = await post_login(client)
    assert session_cookie(response) == ""

  async def test_allowed_once_enforcement_is_off(self, client_factory, app,
                                                 reconfigure_session):
    reconfigure_session(https_only=False)
    async with client_factory(scheme="http") as client:
      response = await post_login(client)
    assert response.headers["location"] == "/"
    assert "; secure" not in session_cookie(response).lower()


class TestLoginBehindATLSProxy:
  @pytest.fixture(autouse=True)
  def _trust_the_proxy(self, app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks("172.18.0.0/16")

  async def test_proxy_terminated_tls_is_accepted(self, client_factory):
    async with client_factory(peer="172.18.0.5", scheme="http") as client:
      response = await post_login(client)
      response = await client.post(
        "/login", content=urllib.parse.urlencode(LOGIN_FORM),
        headers={"content-type": "application/x-www-form-urlencoded",
                 "X-Forwarded-Proto": "https"})
    assert response.headers["location"] == "/"

  async def test_spoofed_proto_from_an_untrusted_peer_is_refused(self, client_factory):
    async with client_factory(peer="203.0.113.9", scheme="http") as client:
      response = await client.post(
        "/login", content=urllib.parse.urlencode(LOGIN_FORM),
        headers={"content-type": "application/x-www-form-urlencoded",
                 "X-Forwarded-Proto": "https"})
    assert response.headers["location"].startswith("/login?error=")


class TestTrustedIPBypass:
  """The Raspberry Pi kiosk path: no login, no cookie, plain HTTP is fine."""

  async def test_trusted_peer_reaches_the_dashboard_over_http(self, client_factory, app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    async with client_factory(peer="192.168.1.50", scheme="http") as client:
      response = await client.get("/")
    assert response.status_code == 200
    assert session_cookie(response) == "", "trusted IP access must not need a cookie"

  async def test_untrusted_peer_is_sent_to_login(self, client_factory, app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    async with client_factory(peer="203.0.113.9", scheme="http") as client:
      response = await client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

  async def test_login_page_redirects_an_already_trusted_client(self, client_factory,
                                                                app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    async with client_factory(peer="192.168.1.50", scheme="http") as client:
      response = await client.get("/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


class TestCredentialComparison:
  """
  compare_digest raises TypeError on non-ASCII str arguments. Comparing as bytes keeps
  a password with an accent in it working instead of turning it into a 500.
  """

  @pytest.mark.parametrize("username,password", [
    ("ünicode", "wrong"),
    (DASHBOARD_USERNAME, "pässwörd"),
    ("日本語", "🔐"),
  ], ids=["non-ascii-username", "non-ascii-password", "multibyte"])
  async def test_non_ascii_input_is_refused_not_crashed(self, client_factory,
                                                        username, password):
    async with client_factory(scheme="https") as client:
      response = await post_login(client, username=username, password=password)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?error=")

  async def test_an_accented_password_actually_works(self, client_factory, app):
    app.DASHBOARD_USERNAME = "admin"
    app.DASHBOARD_PASSWORD = "Sécurité2026!"
    async with client_factory(scheme="https") as client:
      good = await post_login(client, "admin", "Sécurité2026!")
      bad = await post_login(client, "admin", "Sécurité2026")
    assert good.headers["location"] == "/"
    assert bad.headers["location"].startswith("/login?error=")

  async def test_empty_credentials_are_refused(self, client_factory):
    """
    Rejected by form validation before the comparison runs: FastAPI's Form(...)
    treats an empty value as a missing field, so this is a 422 rather than a redirect.
    Either way it must never sign anyone in.
    """
    async with client_factory(scheme="https") as client:
      response = await post_login(client, username="", password="")
    assert response.status_code == 422
    assert "set-cookie" not in response.headers


class TestDefaultCredentialBanner:
  async def test_shown_when_defaults_are_in_place(self, client_factory, app):
    app.USING_DEFAULT_CREDENTIALS = True
    async with client_factory(scheme="https") as client:
      response = await client.get("/login")
    assert "Default credentials are not changed!" in response.text

  async def test_hidden_once_credentials_are_changed(self, client_factory, app):
    app.USING_DEFAULT_CREDENTIALS = False
    async with client_factory(scheme="https") as client:
      response = await client.get("/login")
    assert "Default credentials are not changed!" not in response.text

  async def test_login_page_never_contains_the_password(self, client_factory, app):
    async with client_factory(scheme="https") as client:
      response = await client.get("/login")
    assert app.DASHBOARD_PASSWORD not in response.text
