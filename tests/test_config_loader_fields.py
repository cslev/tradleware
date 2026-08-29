"""
Config loader field coverage.

`_load_stock_bots` does not pass the YAML through — it builds an explicit dict. Anything
absent from that dict never reaches the trader, and because every trader reads its
settings with `config.get(name, default)`, a dropped field is indistinguishable from one
the user never set. It fails silently, with the default quietly winning.

That had already happened to the five market-hours settings: documented in
IBKR_SETUP.md, read by BaseStockTrader, emitted by nobody. A bot on a European venue
silently got NYSE hours — refusing to trade during its own session and trading outside
it.

The coverage test below is the real guard; the rest pin specific defaults.
"""

import re
from pathlib import Path

import pytest

from src.misc.config_loader import _load_stock_bots, _load_crypto_bots

SRC = Path(__file__).resolve().parents[1] / "src"


def _fields_read_by(*paths):
  """Every config key a module reads, via config.get('x') or config['x']."""
  pattern = re.compile(r"config(?:\.get\(|\[)['\"]([a-z_]+)['\"]")
  found = set()
  for path in paths:
    found |= set(pattern.findall((SRC / path).read_text(encoding="utf-8")))
  return found


def _write_yaml(tmp_path, body):
  path = tmp_path / "bots.yaml"
  path.write_text(body, encoding="utf-8")
  return path


STOCK_MINIMAL = """
gateway:
  host: "ib_gateway"
  port: 8888
bots:
  - id: myapplebot
    account_id: "U1234567"
    symbol: "AAPL"
    tradleware_api_key: "tw_live_x"
"""


class TestFieldCoverage:
  """The guard: anything a trader reads, the loader must emit."""

  def test_the_stock_loader_emits_every_field_its_traders_read(self, tmp_path):
    read = _fields_read_by("traders/stock/base_stock_trader.py",
                           "traders/stock/ibkr_trader.py")
    emitted = set(_load_stock_bots("ibkr", _write_yaml(tmp_path, STOCK_MINIMAL))[0])
    missing = read - emitted
    assert missing == set(), (
      f"traders read {sorted(missing)} but the loader never emits them — "
      f"those settings are silently ignored in YAML"
    )

  def test_the_crypto_loader_emits_every_field_its_traders_read(self, tmp_path):
    body = """
bots:
  - id: mybtcbot
    api_key: "k"
    secret_key: "s"
    stablecoin_fiat_pair: "USDT/SGD"
    crypto_stablecoin_pair: "BTC/USDT"
    tradleware_api_key: "tw_live_x"
"""
    read = _fields_read_by("traders/crypto/base_crypto_trader.py")
    emitted = set(_load_crypto_bots("okx", _write_yaml(tmp_path, body))[0])
    assert read - emitted == set()


class TestStockDefaults:
  """A pre-change config must behave exactly as it did before."""

  @pytest.fixture
  def loaded(self, tmp_path):
    bots = _load_stock_bots("ibkr", _write_yaml(tmp_path, STOCK_MINIMAL))
    assert len(bots) == 1
    return bots[0]

  @pytest.mark.parametrize("field,expected", [
    ("account_currency", "USD"),
    ("trading_currency", "USD"),
    ("exchange", "SMART"),
    ("primary_exchange", ""),
    ("market_timezone", "America/New_York"),
    ("market_open", "09:30"),
    ("market_close", "16:00"),
    ("pre_market_open", "04:00"),
    ("after_hours_close", "20:00"),
  ])
  def test_omitted_settings_keep_their_previous_behaviour(self, loaded, field, expected):
    assert loaded[field] == expected


class TestStockOverrides:
  def test_configured_values_reach_the_trader(self, tmp_path):
    body = """
gateway:
  host: "ib_gateway"
  port: 8888
bots:
  - id: myetfbot
    account_id: "U1234567"
    symbol: "VWCE"
    account_currency: "EUR"
    primary_exchange: "AEB"
    market_timezone: "Europe/Amsterdam"
    market_open: "09:00"
    market_close: "17:30"
    tradleware_api_key: "tw_live_x"
"""
    bot = _load_stock_bots("ibkr", _write_yaml(tmp_path, body))[0]
    assert bot["account_currency"] == "EUR"
    assert bot["primary_exchange"] == "AEB"
    assert bot["market_timezone"] == "Europe/Amsterdam"
    assert (bot["market_open"], bot["market_close"]) == ("09:00", "17:30")

  def test_trading_currency_falls_back_to_the_account_currency(self, tmp_path):
    """Set one currency and both follow — they match in the common case."""
    body = STOCK_MINIMAL.replace('    symbol: "AAPL"',
                                 '    symbol: "VWCE"\n    account_currency: "EUR"')
    bot = _load_stock_bots("ibkr", _write_yaml(tmp_path, body))[0]
    assert bot["trading_currency"] == "EUR"

  def test_trading_currency_can_be_set_apart(self, tmp_path):
    """A USD account buying a EUR instrument — IB converts or lends."""
    body = STOCK_MINIMAL.replace(
      '    symbol: "AAPL"',
      '    symbol: "VWCE"\n    account_currency: "USD"\n    trading_currency: "EUR"')
    bot = _load_stock_bots("ibkr", _write_yaml(tmp_path, body))[0]
    assert (bot["account_currency"], bot["trading_currency"]) == ("USD", "EUR")


class TestOptionalHostname:
  def test_a_crypto_bot_without_hostname_loads(self, tmp_path):
    """
    hostname is deliberately absent from _CRYPTO_REQUIRED — each trader falls back to
    its exchange default. The loader nonetheless read bot['hostname'], so a config the
    validator had just accepted raised KeyError and took the whole file with it.
    """
    body = """
bots:
  - id: mybtcbot
    api_key: "k"
    secret_key: "s"
    stablecoin_fiat_pair: "USDT/SGD"
    crypto_stablecoin_pair: "BTC/USDT"
    tradleware_api_key: "tw_live_x"
"""
    bots = _load_crypto_bots("okx", _write_yaml(tmp_path, body))
    assert len(bots) == 1
    assert bots[0]["hostname"] == ""

  def test_a_supplied_hostname_is_still_used(self, tmp_path):
    body = """
bots:
  - id: mybtcbot
    api_key: "k"
    secret_key: "s"
    hostname: "okx.com"
    stablecoin_fiat_pair: "USDT/SGD"
    crypto_stablecoin_pair: "BTC/USDT"
    tradleware_api_key: "tw_live_x"
"""
    assert _load_crypto_bots("okx", _write_yaml(tmp_path, body))[0]["hostname"] == "okx.com"


class TestShippedExample:
  """
  The example file is the first thing a user copies, so it has to load.

  It already documented the market-hours settings while the loader dropped them — an
  example that parses is not the same as an example that works.
  """

  EXAMPLE = SRC.parent / "bot_configs/stock/ibkr.yaml.example"

  def _load(self, tmp_path, text):
    path = tmp_path / "ibkr.yaml"
    path.write_text(text, encoding="utf-8")
    return _load_stock_bots("ibkr", path)

  def test_the_example_loads_as_shipped(self, tmp_path):
    bots = self._load(tmp_path, self.EXAMPLE.read_text(encoding="utf-8"))
    assert [b["id"] for b in bots] == ["myapplebot"]

  def test_the_shipped_bot_keeps_the_us_defaults(self, tmp_path):
    bot = self._load(tmp_path, self.EXAMPLE.read_text(encoding="utf-8"))[0]
    assert bot["account_currency"] == "USD"
    assert bot["exchange"] == "SMART"
    assert bot["primary_exchange"] == ""
    assert bot["market_timezone"] == "America/New_York"

  def test_the_commented_eur_bot_works_when_uncommented(self, tmp_path):
    """
    A user enables it by removing the leading '#'. If the block were mis-indented or
    referenced a field the loader drops, they would find out at runtime.
    """
    lines = self.EXAMPLE.read_text(encoding="utf-8").splitlines()
    # From the bot's own '- id:' line onward — the prose above it is explanation, and
    # uncommenting that would produce invalid YAML rather than a second bot.
    start = next(i for i, line in enumerate(lines) if "id: myetfbot" in line)
    uncommented = [
      re.sub(r"^(\s*)#\s?", r"\1", line, count=1) if i >= start else line
      for i, line in enumerate(lines)
    ]
    bots = self._load(tmp_path, "\n".join(uncommented))

    assert [b["id"] for b in bots] == ["myapplebot", "myetfbot"]
    etf = bots[1]
    assert etf["account_currency"] == "EUR"
    assert etf["trading_currency"] == "EUR", "must follow account_currency when unset"
    assert etf["primary_exchange"] == "AEB"
    assert etf["market_timezone"] == "Europe/Amsterdam"
    assert etf["market_open"] == "09:00"
