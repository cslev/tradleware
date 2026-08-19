"""
Webhook request handling: transport, authentication, freshness and single use.

The API key travels inside the request body, so a captured request is a reusable
trading capability unless the transport is encrypted, the signal is recent, and the
same bytes are refused the second time.
"""

import time

import pytest

from conftest import signal_payload

TRUSTED_PROXY_CIDR = "172.18.0.0/16"


class TestTransportSecurity:
  async def test_plain_http_is_refused(self, client_factory, webhook_url,
                                       crypto_trader):
    async with client_factory(scheme="http") as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 403
    assert "HTTPS" in response.json()["detail"]

  async def test_spoofed_forwarded_proto_is_refused(self, client_factory, webhook_url,
                                                    crypto_trader, app):
    app.TRUSTED_PROXIES = []
    async with client_factory(scheme="http") as client:
      response = await client.post(webhook_url, json=signal_payload(),
                                   headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 403

  async def test_direct_tls_is_accepted(self, client_factory, webhook_url,
                                        crypto_trader):
    async with client_factory(scheme="https") as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200

  async def test_trusted_proxy_reporting_https_is_accepted(self, client_factory,
                                                           webhook_url, crypto_trader,
                                                           app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks(TRUSTED_PROXY_CIDR)
    async with client_factory(peer="172.18.0.5", scheme="http") as client:
      response = await client.post(webhook_url, json=signal_payload(),
                                   headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200

  async def test_trusted_proxy_reporting_http_is_refused(self, client_factory,
                                                         webhook_url, crypto_trader,
                                                         app):
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks(TRUSTED_PROXY_CIDR)
    async with client_factory(peer="172.18.0.5", scheme="http") as client:
      response = await client.post(webhook_url, json=signal_payload(),
                                   headers={"X-Forwarded-Proto": "http"})
    assert response.status_code == 403

  async def test_checked_before_the_body_is_parsed(self, client_factory, webhook_url,
                                                   crypto_trader):
    """By the time we could parse it, the credential has already crossed the wire."""
    async with client_factory(scheme="http") as client:
      response = await client.post(webhook_url, content=b"not json at all",
                                   headers={"content-type": "application/json"})
    assert response.status_code == 403

  async def test_can_be_disabled_for_lan_only_setups(self, client_factory, webhook_url,
                                                     crypto_trader, app):
    app.WEBHOOK_REQUIRE_HTTPS = False
    async with client_factory(scheme="http") as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200


class TestApiKeyAuthentication:
  async def test_correct_key_is_accepted(self, client_factory, webhook_url,
                                         crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200

  @pytest.mark.parametrize("key", [
    "wrong-key", "", None, "tw_live_9f2b7c4", "tw_live_9f2b7c4ex",
    "tw_live_ünicode", 12345, ["a"], {"k": "v"}, True,
  ], ids=["wrong", "empty", "missing", "prefix", "suffix", "non-ascii", "int", "list",
          "dict", "bool"])
  async def test_bad_keys_are_refused_without_crashing(self, client_factory,
                                                       webhook_url, crypto_trader, key):
    """Hostile types must produce 401, never an unhandled 500."""
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(api_key=key))
    assert response.status_code == 401

  async def test_no_order_is_placed_when_auth_fails(self, client_factory, webhook_url,
                                                    crypto_trader):
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(api_key="wrong"))
    assert crypto_trader.orders == []

  async def test_unknown_trader_is_rejected(self, client_factory, webhook_url,
                                            crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(trader_id="no-such-bot"))
    assert response.status_code == 404


class TestRequestShape:
  async def test_non_object_json_body_is_a_400_not_a_500(self, client_factory,
                                                         webhook_url, crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=["not", "an", "object"])
    assert response.status_code == 400

  async def test_malformed_json_is_rejected(self, client_factory, webhook_url,
                                            crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, content=b"{oops",
                                   headers={"content-type": "application/json"})
    assert response.status_code == 400


class TestFreshnessWindow:
  async def test_current_timestamp_is_accepted(self, client_factory, webhook_url,
                                               crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload())
    assert response.status_code == 200

  async def test_stale_timestamp_is_refused(self, client_factory, webhook_url,
                                            crypto_trader, app):
    stale = int(time.time()) - app.WEBHOOK_MAX_AGE_S - 60
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(timestamp=stale))
    assert response.status_code == 400
    assert "freshness" in response.json()["detail"].lower()

  async def test_future_timestamp_is_refused(self, client_factory, webhook_url,
                                             crypto_trader, app):
    """A forged far-future timestamp would otherwise stay valid indefinitely."""
    ahead = int(time.time()) + app.WEBHOOK_MAX_AGE_S + 60
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(timestamp=ahead))
    assert response.status_code == 400

  async def test_inside_the_window_is_accepted(self, client_factory, webhook_url,
                                               crypto_trader, app):
    recent = int(time.time()) - app.WEBHOOK_MAX_AGE_S + 30
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(timestamp=recent))
    assert response.status_code == 200

  @pytest.mark.parametrize("timestamp", ["not-a-time", None, "", True])
  async def test_unusable_timestamp_is_refused(self, client_factory, webhook_url,
                                               crypto_trader, timestamp):
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(timestamp=timestamp))
    assert response.status_code == 400

  async def test_no_order_is_placed_for_a_stale_signal(self, client_factory,
                                                       webhook_url, crypto_trader, app):
    stale = int(time.time()) - app.WEBHOOK_MAX_AGE_S - 60
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(timestamp=stale))
    assert crypto_trader.orders == []

  def test_window_cannot_be_switched_off(self, app):
    """0 or a negative value is raised to the floor, never disabled."""
    import os
    for value in ("0", "-1", "5", "not-a-number"):
      os.environ["WEBHOOK_MAX_AGE_S"] = value
      seconds, _note = app._read_freshness_window()
      assert seconds >= app.WEBHOOK_MAX_AGE_FLOOR_S
    os.environ["WEBHOOK_MAX_AGE_S"] = "300"


class TestSingleUse:
  async def test_a_captured_request_cannot_be_replayed(self, client_factory,
                                                       webhook_url, crypto_trader):
    payload = signal_payload()
    async with client_factory() as client:
      first = await client.post(webhook_url, json=payload)
      second = await client.post(webhook_url, json=payload)
    assert first.status_code == 200
    assert second.status_code == 409
    assert "Duplicate" in second.json()["detail"]
    assert len(crypto_trader.orders) == 1

  async def test_repeated_replays_never_execute(self, client_factory, webhook_url,
                                                crypto_trader):
    payload = signal_payload()
    async with client_factory() as client:
      await client.post(webhook_url, json=payload)
      for _ in range(5):
        assert (await client.post(webhook_url, json=payload)).status_code == 409
    assert len(crypto_trader.orders) == 1

  async def test_distinct_signals_still_get_through(self, client_factory, webhook_url,
                                                    crypto_trader):
    # 10% each, so the bot still has a balance to trade on the third signal
    async with client_factory() as client:
      for _ in range(3):
        response = await client.post(webhook_url, json=signal_payload(order_size=10))
        assert response.status_code == 200
    assert len(crypto_trader.orders) == 3

  async def test_a_refused_delivery_does_not_consume_its_slot(self, client_factory,
                                                              webhook_url,
                                                              crypto_trader, app):
    """Rejected on transport, then accepted later over TLS, then single-use."""
    app.TRUSTED_PROXIES, _ = app._parse_ip_networks(TRUSTED_PROXY_CIDR)
    payload = signal_payload()
    async with client_factory(scheme="http") as insecure:
      assert (await insecure.post(webhook_url, json=payload)).status_code == 403
    async with client_factory(scheme="https") as secure:
      assert (await secure.post(webhook_url, json=payload)).status_code == 200
      assert (await secure.post(webhook_url, json=payload)).status_code == 409

  async def test_failed_auth_does_not_fill_the_replay_cache(self, client_factory,
                                                            webhook_url, crypto_trader):
    """Unauthenticated requests must not be able to grow the cache."""
    payload = signal_payload(api_key="wrong")
    async with client_factory() as client:
      await client.post(webhook_url, json=payload)
      payload["api_key"] = crypto_trader.tradleware_api_key
      response = await client.post(webhook_url, json=payload)
    assert response.status_code == 200, "a rejected guess reserved the fingerprint"
