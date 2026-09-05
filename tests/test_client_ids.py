"""
IB client-id assignment.

IB refuses a second connection using a client id already in use, so a duplicate leaves
one bot silently never trading. The previous scheme was `hash(bot_id) % 1000`, which is
worse than a plain collision: Python salts string hashing per process, so every restart
redrew the ids. A collision therefore appeared at random — the bot ran for weeks, failed
to start once, and "recovered" on the next restart. Observed across three runs of the
same bot id: 254, 928, 733.

Ids are now pinned in config or assigned from the lowest free number, so collisions are
impossible by construction rather than improbable.
"""

import tempfile
from pathlib import Path

import pytest

import src.misc.config_loader as cl

GATEWAY = 'gateway: {host: "ib_gateway", port: 8888}\nbots:\n'


def _bot(name, client_id=None, extra=""):
  block = (f'  - id: {name}\n'
           f'    account_id: "U1234567"\n'
           f'    symbol: "SYM"\n'
           f'    tradleware_api_key: "tw_live_x"\n')
  if client_id is not None:
    block += f'    client_id: {client_id}\n'
  return block + extra


def load(*bots):
  """Run the real loader and assignment over a temporary config."""
  with tempfile.TemporaryDirectory() as d:
    path = Path(d) / 'ibkr.yaml'
    path.write_text(GATEWAY + "".join(bots), encoding='utf-8')
    configs = cl._load_stock_bots('ibkr', path)
  cl._assign_client_ids(configs)
  return configs, cl.client_id_findings()


def ids(configs):
  return {c['id']: c['client_id'] for c in configs}


class TestUniqueness:
  """The property that matters: no two bots ever share an id."""

  @pytest.mark.parametrize("count", [1, 2, 5, 20])
  def test_auto_assigned_ids_are_unique(self, count):
    configs, _ = load(*(_bot(f"bot{i}") for i in range(count)))
    assigned = [c['client_id'] for c in configs]
    assert len(set(assigned)) == count, assigned

  def test_auto_assignment_fills_from_the_lowest_free_number(self):
    configs, _ = load(_bot("a"), _bot("b"), _bot("c"))
    assert ids(configs) == {"a": 1, "b": 2, "c": 3}

  def test_auto_assignment_skips_pinned_ids(self):
    """An auto-assigned bot must never land on a number someone pinned."""
    configs, _ = load(_bot("pinned_low", 1), _bot("auto"), _bot("pinned_two", 2))
    assert ids(configs) == {"pinned_low": 1, "auto": 3, "pinned_two": 2}

  def test_pinned_ids_are_honoured_verbatim(self):
    configs, _ = load(_bot("a", 42), _bot("b", 7))
    assert ids(configs) == {"a": 42, "b": 7}

  def test_a_high_pin_does_not_push_auto_ids_up(self):
    configs, _ = load(_bot("high", 900), _bot("auto"))
    assert ids(configs)["auto"] == 1


class TestDuplicatePins:
  """
  Reassign rather than let a bot fail. A bot whose id is not the number you typed is
  less bad than a bot that never connects — but it is reported at error level either way.
  """

  def test_the_second_bot_gets_a_free_id_instead_of_the_duplicate(self):
    configs, _ = load(_bot("first", 42), _bot("second", 42))
    assert ids(configs)["first"] == 42
    assert ids(configs)["second"] != 42

  def test_the_duplicate_is_reported_as_critical(self):
    _, findings = load(_bot("first", 42), _bot("second", 42))
    critical = [f for f in findings if f['level'] == 'critical']
    assert len(critical) == 1
    assert critical[0]['bot_id'] == "second"
    assert "42" in critical[0]['reason']

  def test_a_duplicate_is_not_also_reported_as_unpinned(self):
    """It did set one — saying otherwise sends the operator to the wrong line of YAML."""
    _, findings = load(_bot("first", 42), _bot("second", 42))
    info = [f for f in findings if f['level'] == 'info']
    assert info == [], info

  def test_three_way_duplicates_all_end_up_distinct(self):
    configs, _ = load(_bot("a", 5), _bot("b", 5), _bot("c", 5))
    assert len(set(ids(configs).values())) == 3


class TestUnpinnedReporting:
  def test_unpinned_bots_are_reported_with_what_they_were_given(self):
    _, findings = load(_bot("a"), _bot("b"))
    info = [f for f in findings if f['level'] == 'info']
    assert len(info) == 1
    assert "a=1" in info[0]['reason'] and "b=2" in info[0]['reason']

  def test_nothing_is_reported_when_every_bot_is_pinned(self):
    _, findings = load(_bot("a", 1), _bot("b", 2))
    assert findings == []

  def test_a_non_numeric_pin_is_reported_and_replaced(self):
    _, findings = load(_bot("a", '"abc"'))
    warnings = [f for f in findings if f['level'] == 'warning']
    assert len(warnings) == 1 and warnings[0]['bot_id'] == "a"

  def test_a_non_numeric_pin_still_yields_a_usable_id(self):
    configs, _ = load(_bot("a", '"abc"'))
    assert isinstance(configs[0]['client_id'], int)


class TestNoHashing:
  def test_the_trader_no_longer_derives_its_id_from_hash(self):
    """
    hash() on a str is salted per process, so this produced a different id every restart.
    """
    source = Path(cl.__file__).parents[1].joinpath('traders/stock/ibkr_trader.py')
    text = source.read_text(encoding='utf-8')
    assert "hash(self.account_identifier)" not in text
    assert "clientId=self.client_id" in text

  def test_ids_are_stable_across_repeated_loads(self):
    """The whole point: same config in, same ids out, every time."""
    first, _ = load(_bot("a"), _bot("b", 9), _bot("c"))
    second, _ = load(_bot("a"), _bot("b", 9), _bot("c"))
    assert ids(first) == ids(second)


class TestCryptoIsUntouched:
  def test_crypto_bots_get_no_client_id(self):
    """Only IB uses client ids; a crypto config has no business carrying one."""
    body = ('bots:\n  - id: c\n    api_key: "k"\n    secret_key: "s"\n'
            '    stablecoin_fiat_pair: "USDT/SGD"\n'
            '    crypto_stablecoin_pair: "BTC/USDT"\n    tradleware_api_key: "x"\n')
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / 'okx.yaml'
      path.write_text(body, encoding='utf-8')
      configs = cl._load_crypto_bots('okx', path)
    cl._assign_client_ids(configs)
    assert 'client_id' not in configs[0]
    assert cl.client_id_findings() == []
