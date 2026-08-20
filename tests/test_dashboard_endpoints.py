"""
The endpoints the dashboard's JavaScript polls, and the pages it renders.

None of these carry security logic, which is exactly why they need cover: the security
work re-indented 262 lines of the trade handler and rewrote large parts of the template,
and a break here shows up as an empty card rather than a failing check.
"""

import logging
import types

import pytest

from conftest import (DASHBOARD_HEADERS, FakeCryptoTrader, FakeStockTrader,
                      signal_payload)

TRUSTED = "192.168.1.50"


@pytest.fixture
def signed_in(app):
  """Authenticate by trusted IP so the tests do not depend on the cookie flow."""
  app.TRUSTED_IPS = [TRUSTED]
  return app


@pytest.fixture
def rich_crypto_trader(app):
  """A crypto bot complete enough for /balance and /price."""
  trader = FakeCryptoTrader()
  trader.exchange = types.SimpleNamespace(fetch_ticker=lambda pair: None)

  async def safe_api_call(method, *args, **kwargs):
    return {"last": 61234.5, "bid": 61230.0, "ask": 61240.0}

  trader._safe_api_call = safe_api_call
  trader.log_buffer = ["line one", "line two"]
  trader.get_recent_logs = lambda: list(trader.log_buffer)
  app.traders["fakebot"] = trader
  return trader


@pytest.fixture
def rich_stock_trader(app):
  """A stock bot complete enough for /position."""
  trader = FakeStockTrader()
  trader.can_trade_now = lambda: False
  trader.get_market_status = lambda: "closed"
  trader.get_time_until_market_opens = lambda: "2h 34m"

  # Matches IBKRTrader: async, and the symbol is optional because the endpoint calls it
  # with no argument even though the base class declares the parameter as required
  async def get_market_price(symbol=None):
    return 187.25

  trader.get_market_price = get_market_price

  async def fetch_positions():
    # Same keys IBKRTrader returns; the endpoint indexes them directly
    return {"symbol": "AAPL", "quantity": 10, "avg_cost": 150.0,
            "market_value": 1872.5, "unrealized_pnl": 372.5,
            "unrealized_pnl_pct": 24.8, "cash": 5000.0}

  trader.fetch_positions = fetch_positions
  trader.log_buffer = []
  trader.get_recent_logs = lambda: []
  app.traders["fakestock"] = trader
  return trader


class TestAuthenticationIsRequired:
  @pytest.mark.parametrize("path", [
    "/balance/fakebot", "/price/fakebot", "/position/fakestock", "/logs/fakebot",
  ])
  async def test_unauthenticated_access_is_refused(self, client_factory, path, app,
                                                   crypto_trader):
    app.TRUSTED_IPS = []
    async with client_factory(peer="203.0.113.9") as client:
      response = await client.get(path)
    assert response.status_code == 401

  async def test_convert_requires_authentication(self, client_factory, app,
                                                 crypto_trader):
    app.TRUSTED_IPS = []
    async with client_factory(peer="203.0.113.9") as client:
      response = await client.post("/convert/fakebot", headers=DASHBOARD_HEADERS)
    assert response.status_code == 401


class TestBalanceEndpoint:
  async def test_returns_the_balance(self, client_factory, signed_in,
                                     rich_crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/balance/fakebot")
    assert response.status_code == 200
    assert "balance" in response.json()

  async def test_unknown_bot_is_404(self, client_factory, signed_in,
                                    rich_crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/balance/nope")
    assert response.status_code == 404

  async def test_stock_bots_are_rejected(self, client_factory, signed_in,
                                         rich_stock_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/balance/fakestock")
    assert response.status_code == 400

  async def test_an_exchange_failure_is_a_500_not_a_crash(self, client_factory,
                                                          signed_in, app):
    class Broken(FakeCryptoTrader):
      async def fetch_balance(self):
        raise RuntimeError("exchange down")
    app.traders["broken"] = Broken()
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/balance/broken")
    assert response.status_code == 500


class TestPriceEndpoint:
  async def test_returns_a_price(self, client_factory, signed_in, rich_crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/price/fakebot")
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 61234.5
    assert body["pair"] == "BTC/USDT"

  async def test_stock_bots_are_rejected(self, client_factory, signed_in,
                                         rich_stock_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/price/fakestock")
    assert response.status_code == 400

  async def test_no_ticker_data_is_a_502(self, client_factory, signed_in, app):
    trader = FakeCryptoTrader()
    trader.exchange = types.SimpleNamespace(fetch_ticker=lambda pair: None)

    async def empty(method, *args, **kwargs):
      return None

    trader._safe_api_call = empty
    app.traders["quiet"] = trader
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/price/quiet")
    assert response.status_code == 502


class TestPositionEndpoint:
  async def test_returns_a_position(self, client_factory, signed_in,
                                    rich_stock_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/position/fakestock")
    assert response.status_code == 200

  async def test_unknown_bot_is_404(self, client_factory, signed_in,
                                    rich_stock_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/position/nope")
    assert response.status_code == 404


class TestLogsEndpoint:
  async def test_returns_the_recent_logs(self, client_factory, signed_in,
                                         rich_crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/logs/fakebot")
    assert response.status_code == 200

  async def test_unknown_bot_is_404(self, client_factory, signed_in,
                                    rich_crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/logs/nope")
    assert response.status_code == 404


class TestDashboardRenders:
  """The template gained a masking filter and three conditional banners."""

  async def test_with_a_crypto_bot(self, client_factory, signed_in,
                                   rich_crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert response.status_code == 200
    assert "fakebot" in response.text

  async def test_with_a_stock_bot(self, client_factory, signed_in, rich_stock_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert response.status_code == 200
    assert "fakestock" in response.text
    assert "AAPL" in response.text

  async def test_with_both_kinds_at_once(self, client_factory, signed_in,
                                         rich_crypto_trader, rich_stock_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert response.status_code == 200
    assert "fakebot" in response.text and "fakestock" in response.text

  async def test_with_no_bots_configured(self, client_factory, signed_in):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert response.status_code == 200

  async def test_with_an_unconfigured_trading_pair(self, client_factory, signed_in,
                                                   rich_crypto_trader):
    rich_crypto_trader.trading_pair_valid = False
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert response.status_code == 200
    assert "UNSUPPORTED" in response.text

  async def test_with_a_missing_api_key(self, client_factory, signed_in,
                                        rich_crypto_trader):
    """mask_secret has to cope with an unset key without breaking the page."""
    rich_crypto_trader.tradleware_api_key = None
    async with client_factory(peer=TRUSTED) as client:
      response = await client.get("/")
    assert response.status_code == 200
    assert "Not configured" in response.text

  async def test_the_login_page_renders(self, client_factory, app):
    app.TRUSTED_IPS = []
    async with client_factory(peer="203.0.113.9") as client:
      response = await client.get("/login")
    assert response.status_code == 200
    assert "<form" in response.text


class TestTradingPathsStillWork:
  """
  The whole crypto/stock branch was re-indented under the per-bot execution lock.
  These walk the paths that re-indent covered.
  """

  async def test_a_buy_signal_places_a_buy(self, client_factory, webhook_url,
                                           crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(action="buy", dry_run=False))
    assert response.status_code == 200
    assert crypto_trader.orders[0]["side"] == "buy"

  async def test_a_sell_signal_places_a_sell(self, client_factory, webhook_url, app):
    trader = FakeCryptoTrader()

    async def fetch_balance():
      return {"free": {"USDT": 1000.0, "BTC": 0.5}}

    trader.fetch_balance = fetch_balance
    app.traders["fakebot"] = trader
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(action="sell", dry_run=False))
    assert response.status_code == 200
    assert trader.orders and trader.orders[0]["side"] == "sell"

  @pytest.mark.parametrize("action,expected", [
    ("buy", "buy"), ("long", "buy"), ("sell", "sell"), ("short", "sell"),
  ])
  async def test_tradingview_action_aliases(self, client_factory, webhook_url,
                                            crypto_trader, action, expected):
    async with client_factory() as client:
      response = await client.post(webhook_url,
                                   json=signal_payload(action=action, dry_run=False))
    assert response.status_code == 200
    assert crypto_trader.orders[-1]["side"] == expected

  async def test_dry_run_does_not_reach_a_live_order_path(self, client_factory,
                                                          webhook_url, crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(dry_run=True))
    assert response.status_code == 200
    assert crypto_trader.orders[0]["dry_run"] is True

  async def test_quantity_mode(self, client_factory, webhook_url, crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(
        order_size=0.01, order_size_type="quantity", dry_run=False))
    assert response.status_code == 200
    assert crypto_trader.orders[0]["quantity"] == 0.01
    assert crypto_trader.orders[0]["spend_percentage"] is None

  async def test_percentage_mode(self, client_factory, webhook_url, crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(
        order_size=25, order_size_type="percentage", dry_run=False))
    assert response.status_code == 200
    assert crypto_trader.orders[0]["spend_percentage"] == 0.25

  async def test_a_wrong_ticker_is_still_rejected(self, client_factory, webhook_url,
                                                  crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(ticker="ETH/USDT"))
    assert response.status_code == 400
    assert crypto_trader.orders == []

  @pytest.mark.parametrize("payload,status", [
    ({"order_size_type": "nonsense"}, 400),
    ({"order_size": "not-a-number"}, 400),
    ({"order_size": 250}, 400),          # over 100%
    ({"order_size": -5}, 400),
    ({"action": "sideways"}, 400),
  ])
  async def test_invalid_orders_are_refused(self, client_factory, webhook_url,
                                            crypto_trader, payload, status):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(**payload))
    assert response.status_code == status
    assert crypto_trader.orders == []

  async def test_the_response_reports_what_happened(self, client_factory, webhook_url,
                                                    crypto_trader):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(dry_run=False))
    body = response.json()
    assert body.get("status") in {"success", "warning"}
    assert "processed_at" in body
    assert body["processed_at"].endswith("UTC")


class TestConvertEndpoint:
  async def test_a_conversion_runs(self, client_factory, signed_in, crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.post("/convert/fakebot", headers=DASHBOARD_HEADERS)
    assert response.status_code == 200
    assert crypto_trader.converted

  async def test_unknown_bot_is_404(self, client_factory, signed_in, crypto_trader):
    async with client_factory(peer=TRUSTED) as client:
      response = await client.post("/convert/nope", headers=DASHBOARD_HEADERS)
    assert response.status_code == 404


class TestAssetsSurviveAnHTTPSProxy:
  """
  Tradleware runs on plain HTTP behind a TLS-terminating proxy, and uvicorn is started
  with --no-proxy-headers so it cannot rewrite the client address. That also leaves
  scope["scheme"] as "http", so any absolute URL the app generates comes out as http://
  on a page the browser loaded over https:// — which the browser blocks as mixed
  content, and the dashboard renders with no CSS, no logo and no JavaScript.

  Static references must therefore be root-relative, never absolute.
  """

  @staticmethod
  def _assets(html):
    import re
    return [a for a in re.findall(r'(?:href|src)="([^"]+)"', html) if "/static/" in a]

  async def test_dashboard_assets_are_scheme_agnostic(self, client_factory, signed_in,
                                                      rich_crypto_trader):
    async with client_factory(peer=TRUSTED, scheme="http") as client:
      response = await client.get("/", headers={"X-Forwarded-Proto": "https"})
    assets = self._assets(response.text)
    assert assets, "no static assets found — the check would be vacuous"
    assert not [a for a in assets if a.startswith("http")], \
      f"absolute asset URLs get blocked as mixed content: {assets}"

  async def test_login_assets_are_scheme_agnostic(self, client_factory, app):
    app.TRUSTED_IPS = []
    async with client_factory(peer="203.0.113.9", scheme="http") as client:
      response = await client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assets = self._assets(response.text)
    assert assets
    assert not [a for a in assets if a.startswith("http")]

  async def test_templates_do_not_reintroduce_url_for(self):
    """url_for() is absolute by design, so it reintroduces the bug wherever it is used."""
    from pathlib import Path
    for template in Path("src/ui/templates").glob("*.html"):
      assert "url_for(" not in template.read_text(), \
        f"{template.name} uses url_for, which bakes the scheme into the URL"
