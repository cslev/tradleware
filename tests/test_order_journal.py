"""
Order journal: a durable JSONL record of every order attempt, written from the
three places the webhook handler calls create_order (crypto buy, crypto sell,
stock). Before this, an order existed only as lines in tradleware.log, which
rotates away and leaves nothing once gone.

The crypto/stock price handling is deliberately asymmetric — ccxt's 'price' is
the requested price (often 0/None for a market order, and never what was
actually paid), while IBKR's 'price' is already the resolved average fill
price. Getting this wrong silently means every crypto row records the ask, not
the fill.
"""
import json
import re
from pathlib import Path

import pytest

from conftest import signal_payload

from src.misc import order_journal

# Under src/logs/ (already gitignored wholesale) rather than pytest's tmp_path, so a
# run's output survives a reboot and can be opened directly instead of hunted down
# under /tmp/pytest-of-<user>/.
_TEST_JOURNALS_DIR = Path(__file__).resolve().parent.parent / "src" / "logs" / "tests" / "order_journal"


@pytest.fixture
def journal_path(request, monkeypatch):
  """Point the journal at a scratch file so tests never touch the real one."""
  _TEST_JOURNALS_DIR.mkdir(parents=True, exist_ok=True)
  path = _TEST_JOURNALS_DIR / f"{re.sub(r'[^\w.-]', '_', request.node.name)}.jsonl"
  path.unlink(missing_ok=True)  # drop the previous run's rows, not this run's
  monkeypatch.setattr(order_journal, "_JOURNAL_PATH", path)
  return path


def _read_rows(path):
  if not path.exists():
    return []
  return [json.loads(line) for line in path.read_text().splitlines() if line]


async def _rejecting(**_kwargs):
  return None


async def _raising(**_kwargs):
  raise RuntimeError("exchange exploded")


class TestRecordOrder:
  """Unit-level: record_order itself, independent of the webhook handler."""

  def test_it_appends_one_json_line_per_call(self, journal_path):
    order_journal.record_order(n=1)
    order_journal.record_order(n=2)
    rows = _read_rows(journal_path)
    assert [row["n"] for row in rows] == [1, 2]

  def test_it_creates_the_parent_directory(self, tmp_path, monkeypatch):
    nested = tmp_path / "nested" / "orders.jsonl"
    monkeypatch.setattr(order_journal, "_JOURNAL_PATH", nested)
    order_journal.record_order(n=1)
    assert nested.exists()

  def test_it_never_rotates_or_caps_the_file(self, journal_path):
    """Unlike the log handlers, nothing here may ever truncate or gzip a row."""
    for n in range(50):
      order_journal.record_order(n=n)
    rows = _read_rows(journal_path)
    assert len(rows) == 50

  def test_a_value_that_cannot_serialise_is_stringified_not_raised(self, journal_path):
    class Unserialisable:  # pylint: disable=too-few-public-methods
      def __str__(self):
        return "unserialisable-value"

    order_journal.record_order(thing=Unserialisable())
    rows = _read_rows(journal_path)
    assert rows[0]["thing"] == "unserialisable-value"


class TestWebhookWritesToTheJournal:
  """Every create_order call site, all three outcomes each."""

  async def test_a_filled_crypto_buy_reads_average_and_cost_not_the_requested_price(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    async with client_factory() as client:
      response = await client.post(webhook_url, json=signal_payload(dry_run=False))
    assert response.status_code == 200

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "filled"
    assert row["bot_type"] == "crypto"
    assert row["exchange_or_broker"] == "okx"
    assert row["action"] == "buy"
    assert row["order_id"] == "order-1"
    # FakeCryptoTrader's create_order returns price=1 and no 'average' — the
    # fallback (average or price) must still land on the fill price, not 0/None.
    assert row["average_fill_price"] == 1
    assert "cost" in row
    assert "price" not in row

  async def test_a_filled_crypto_sell_is_recorded(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(action="sell", dry_run=False))

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "filled"
    assert rows[0]["action"] == "sell"

  async def test_a_rejected_crypto_buy_is_recorded_with_no_price_fields(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    crypto_trader.create_order = _rejecting
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(dry_run=False))

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "rejected"
    assert "average_fill_price" not in rows[0]

  async def test_an_erroring_crypto_buy_is_recorded_with_the_error_text(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    crypto_trader.create_order = _raising
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(dry_run=False))

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert "exchange exploded" in rows[0]["error"]

  async def test_a_rejected_crypto_sell_is_recorded(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    crypto_trader.create_order = _rejecting
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(action="sell", dry_run=False))

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "rejected"

  async def test_an_erroring_crypto_sell_is_recorded(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    crypto_trader.create_order = _raising
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(action="sell", dry_run=False))

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"

  async def test_a_filled_stock_order_reads_price_directly(
      self, client_factory, webhook_url, stock_trader, journal_path):
    payload = signal_payload(api_key="tw_live_stock_key", trader_id="fakestock",
                             ticker="AAPL")
    async with client_factory() as client:
      response = await client.post(webhook_url, json=payload)
    assert response.status_code == 200

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "filled"
    assert row["bot_type"] == "stock"
    assert row["exchange_or_broker"] == "ibkr"
    assert row["order_id"] == "stock-1"
    # IBKR's 'price' is already the resolved avgFillPrice — read directly, no
    # average/cost fallback (those are ccxt-only fields stock orders never have).
    assert row["average_fill_price"] == 100.0
    assert "cost" not in row

  async def test_a_rejected_stock_order_is_recorded(
      self, client_factory, webhook_url, stock_trader, journal_path):
    async def rejecting(**_kwargs):
      return None
    stock_trader.create_order = rejecting
    payload = signal_payload(api_key="tw_live_stock_key", trader_id="fakestock",
                             ticker="AAPL")
    async with client_factory() as client:
      await client.post(webhook_url, json=payload)

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "rejected"

  async def test_an_erroring_stock_order_is_recorded(
      self, client_factory, webhook_url, stock_trader, journal_path):
    stock_trader.create_order = _raising
    payload = signal_payload(api_key="tw_live_stock_key", trader_id="fakestock",
                             ticker="AAPL")
    async with client_factory() as client:
      await client.post(webhook_url, json=payload)

    rows = _read_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"

  async def test_a_dry_run_is_flagged_unmistakably(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    """The journal cannot be trusted as a tax/reconciliation record otherwise."""
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(dry_run=True))

    rows = _read_rows(journal_path)
    assert rows[0]["dry_run"] is True

  async def test_fields_shared_across_outcomes_are_present(
      self, client_factory, webhook_url, crypto_trader, journal_path):
    async with client_factory() as client:
      await client.post(webhook_url, json=signal_payload(dry_run=False,
                                                          alert_name="my-alert"))

    row = _read_rows(journal_path)[0]
    for field in ("timestamp", "trader_id", "bot_type", "exchange_or_broker",
                  "ticker", "action", "order_size", "order_size_type",
                  "dry_run", "alert_name"):
      assert field in row
    assert row["trader_id"] == "fakebot"
    assert row["alert_name"] == "my-alert"
