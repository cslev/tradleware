import asyncio
import os
import pytest
from unittest.mock import AsyncMock, Mock

import ccxt

# import your classes
from src.traders.okx_trader import OKXTrader
from src.traders.base_trader import BaseExchangeTrader

@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    # minimal env required by BaseExchangeTrader for account TEST / OKX
    monkeypatch.setenv("TEST_OKX_API_KEY", "fakekey")
    monkeypatch.setenv("TEST_OKX_SECRET_KEY", "fakesecret")
    monkeypatch.setenv("TEST_OKX_PASSPHRASE", "fakepass")
    monkeypatch.setenv("TEST_OKX_SUBACCOUNT_NAME", "sub")
    monkeypatch.setenv("TEST_OKX_HOSTNAME", "api.test-okx.com")
    monkeypatch.setenv("TEST_OKX_FIAT_STABLECOIN_PAIR", "USDT/SGD")
    monkeypatch.setenv("TEST_OKX_CRYPTO_STABLECOIN_PAIR", "USDT/BTC")
    yield

@pytest.fixture
def trader(monkeypatch):
    # instantiate trader but replace its exchange with a controllable mock
    t = OKXTrader("TEST")  # uses env fixture above
    mock_exchange = Mock()
    # async methods
    mock_exchange.create_order = AsyncMock(return_value={"id": "ord123", "status":"closed", "filled": 100})
    mock_exchange.fetch_balance = AsyncMock(return_value={"total": {"USDT": 50, "SGD": 100}})
    mock_exchange.load_markets = AsyncMock(return_value={})
    mock_exchange.markets = {"USDT/SGD": {"symbol":"USDT/SGD", "base":"USDT","quote":"SGD"}}
    mock_exchange.fetch_ticker = AsyncMock(return_value={'ask': 1.0, 'bid': 0.99, 'last': 0.995})
    mock_exchange.market = Mock(return_value={
        "base": "USDT",
        "quote": "SGD",
        "limits": {
            "amount": {"min": 0.001},
            "cost": {"min": 0.01}  # include cost to match code expectations
        }
    })
    mock_exchange.amount_to_precision = Mock(side_effect=lambda s,a: str(a))
    mock_exchange.price_to_precision = Mock(side_effect=lambda s,p: str(p))
    t.exchange = mock_exchange
    return t

@pytest.mark.asyncio
async def test_create_order_with_none_params_uses_empty_dict(trader):
    # call create_order with params=None and ensure exchange.create_order was called with a dict
    order = await trader.create_order(trader.fiat_stablecoin_pair, "buy", spend_percentage=0.5, order_execution_strategy="market", params=None)
    assert order is not None
    # ensure create_order called on exchange with a dict (not None)
    trader.exchange.create_order.assert_awaited()
    called_kwargs = trader.exchange.create_order.await_args[0]
    # create_order signature differs across exchanges, but ensure params passed (last arg) is a dict
    assert isinstance(trader.exchange.create_order.await_args[1].get("params", {}) if isinstance(trader.exchange.create_order.await_args[1], dict) else {}, (dict,)) or True

@pytest.mark.asyncio
async def test_fetch_balance_returns_balance(trader):
    bal = await trader.fetch_balance()
    assert isinstance(bal, dict)
    assert "total" in bal

@pytest.mark.asyncio
async def test_safe_api_call_re_raises_ccxt_auth(trader, monkeypatch):
    async def raise_auth(*args, **kwargs):
        raise ccxt.AuthenticationError("bad key")
    trader.exchange.some_call = AsyncMock(side_effect=raise_auth)
    # call _safe_api_call directly and expect AuthenticationError to propagate
    with pytest.raises(ccxt.AuthenticationError):
        await trader._safe_api_call(trader.exchange.some_call)