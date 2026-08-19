"""
Client address resolution.

Regression cover for the auth bypass: get_client_ip() used to return the
X-Forwarded-For header verbatim, so ``curl -H 'X-Forwarded-For: <trusted ip>'`` was
enough to be treated as a trusted client with no proxy deployed anywhere.
"""

import pytest
from starlette.requests import Request

TRUSTED_PROXY_CIDR = "172.18.0.0/16"


def make_request(peer="203.0.113.9", headers=None, scheme="http"):
  """Build a Request with a chosen TCP peer, without going through the HTTP stack."""
  raw_headers = [(key.lower().encode(), value.encode())
                 for key, value in (headers or {}).items()]
  return Request({
    "type": "http", "http_version": "1.1", "method": "GET", "scheme": scheme,
    "path": "/", "raw_path": b"/", "query_string": b"", "root_path": "",
    "headers": raw_headers, "client": (peer, 54321) if peer else None,
    "server": ("testserver", 8080), "session": {},
  })


class TestWithoutATrustedProxy:
  """The default: forwarded headers carry no authority at all."""

  @pytest.mark.parametrize("headers", [
    {"X-Forwarded-For": "192.168.1.50"},
    {"X-Real-IP": "192.168.1.50"},
    {"X-Forwarded-For": "192.168.1.50", "X-Real-IP": "192.168.1.50"},
    {"X-Forwarded-For": "127.0.0.1"},
  ], ids=["xff", "x-real-ip", "both", "loopback-claim"])
  def test_forwarded_headers_are_ignored(self, app, headers):
    app.TRUSTED_PROXIES = []
    assert app.get_client_ip(make_request(headers=headers)) == "203.0.113.9"

  def test_spoofed_header_does_not_authenticate(self, app):
    """The original exploit, at the function that decides access."""
    app.TRUSTED_IPS = ["192.168.1.50"]
    app.TRUSTED_PROXIES = []
    request = make_request(headers={"X-Forwarded-For": "192.168.1.50"})
    assert app.is_authenticated(request) is False

  def test_genuine_peer_still_authenticates(self, app):
    app.TRUSTED_IPS = ["192.168.1.50"]
    app.TRUSTED_PROXIES = []
    assert app.is_authenticated(make_request(peer="192.168.1.50")) is True

  def test_missing_peer_is_not_trusted(self, app):
    app.TRUSTED_IPS = ["unknown"]
    app.TRUSTED_PROXIES = []
    request = make_request(peer=None)
    # The literal string "unknown" must not become a usable identity
    assert app.get_client_ip(request) == "unknown"


class TestBehindATrustedProxy:
  """Headers count, but only the part the proxy itself vouches for."""

  @pytest.fixture(autouse=True)
  def _trust_the_docker_bridge(self, app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks(TRUSTED_PROXY_CIDR)
    app.TRUSTED_IPS = ["192.168.1.50"]

  def test_rightmost_untrusted_hop_wins(self, app):
    """nginx appends, so a client-supplied value sits to the LEFT of the real one."""
    request = make_request(peer="172.18.0.5",
                           headers={"X-Forwarded-For": "192.168.1.50, 203.0.113.9"})
    assert app.get_client_ip(request) == "203.0.113.9"
    assert app.is_authenticated(request) is False

  def test_real_client_is_recovered(self, app):
    request = make_request(peer="172.18.0.5",
                           headers={"X-Forwarded-For": "192.168.1.50"})
    assert app.get_client_ip(request) == "192.168.1.50"
    assert app.is_authenticated(request) is True

  def test_proxy_hops_are_skipped(self, app):
    request = make_request(
      peer="172.18.0.5",
      headers={"X-Forwarded-For": "203.0.113.9, 192.168.1.50, 172.18.0.9"})
    assert app.get_client_ip(request) == "192.168.1.50"

  def test_x_real_ip_is_honoured(self, app):
    request = make_request(peer="172.18.0.5", headers={"X-Real-IP": "192.168.1.50"})
    assert app.get_client_ip(request) == "192.168.1.50"

  @pytest.mark.parametrize("value", ["not-an-ip", "", "   ", "999.999.999.999"])
  def test_malformed_chain_falls_back_to_the_peer(self, app, value):
    request = make_request(peer="172.18.0.5", headers={"X-Forwarded-For": value})
    assert app.get_client_ip(request) == "172.18.0.5"

  def test_all_trusted_chain_falls_back_to_the_peer(self, app):
    request = make_request(peer="172.18.0.5",
                           headers={"X-Forwarded-For": "172.18.0.9"})
    assert app.get_client_ip(request) == "172.18.0.5"


class TestProxyMatching:
  def test_bare_address_and_cidr_both_work(self, app):
    app.TRUSTED_PROXIES, invalid = app._parse_ip_networks("10.0.0.1, 172.18.0.0/16")
    assert invalid == []
    assert app.is_trusted_proxy("10.0.0.1") is True
    assert app.is_trusted_proxy("10.0.0.2") is False
    assert app.is_trusted_proxy("172.18.99.4") is True

  def test_invalid_entries_are_reported_not_silently_dropped(self, app):
    networks, invalid = app._parse_ip_networks("172.18.0.0/16, nonsense, 10.0.0.1")
    assert len(networks) == 2
    assert invalid == ["nonsense"]

  def test_ipv4_mapped_ipv6_peer_is_matched(self, app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks("172.18.0.0/16")
    assert app.is_trusted_proxy("::ffff:172.18.0.5") is True

  def test_nothing_is_trusted_when_unconfigured(self, app):
    app.TRUSTED_PROXIES = []
    assert app.is_trusted_proxy("172.18.0.5") is False
    assert app.is_trusted_proxy("127.0.0.1") is False


class TestLoopbackDetection:
  """is_loopback() only suppresses log noise — it must never grant access."""

  @pytest.mark.parametrize("address,expected", [
    ("127.0.0.1", True),
    ("127.0.0.5", True),      # missed by the old string comparison
    ("::1", True),            # ditto, and the health check can use it
    ("::ffff:127.0.0.1", True),
    ("192.168.1.50", False),
    ("203.0.113.9", False),
    ("unknown", False),
    ("", False),
  ])
  def test_recognises_every_loopback_form(self, app, address, expected):
    assert app.is_loopback(address) is expected

  def test_loopback_alone_grants_nothing(self, app):
    app.TRUSTED_IPS = []
    assert app.is_trusted_ip("127.0.0.1") is False
    assert app.is_authenticated(make_request(peer="127.0.0.1")) is False


class TestRequestSecurity:
  def test_forwarded_proto_from_an_untrusted_peer_is_ignored(self, app):
    app.TRUSTED_PROXIES = []
    request = make_request(headers={"X-Forwarded-Proto": "https"})
    assert app.is_request_secure(request) is False

  def test_forwarded_proto_from_a_trusted_proxy_counts(self, app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks(TRUSTED_PROXY_CIDR)
    request = make_request(peer="172.18.0.5",
                           headers={"X-Forwarded-Proto": "https"})
    assert app.is_request_secure(request) is True

  def test_chained_proxies_use_the_client_facing_leg(self, app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks(TRUSTED_PROXY_CIDR)
    request = make_request(peer="172.18.0.5",
                           headers={"X-Forwarded-Proto": "https,http"})
    assert app.is_request_secure(request) is True

  def test_direct_tls_is_recognised(self, app):
    app.TRUSTED_PROXIES = []
    assert app.is_request_secure(make_request(scheme="https")) is True

  def test_plain_http_is_not_secure(self, app):
    app.TRUSTED_PROXIES = []
    assert app.is_request_secure(make_request(scheme="http")) is False
