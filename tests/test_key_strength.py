"""
Webhook API key strength assessment.

The key is the only thing protecting an internet-reachable trading endpoint that does
not throttle guesses, and it is free-form text in a YAML file. Nothing here refuses to
load a bot — the findings are advisory — so the tests check the judgement and that it
reaches the operator, not that anything is blocked.
"""

import pytest

from src.misc.key_strength import (MIN_ACCEPTABLE_BITS, assess_key, estimate_bits,
                                   find_shared_keys)

STRONG_KEY = "9f2b7c4e1a6d8035bc47ef29a1d5730e8b46cf19d27a53e0bc814f6a920d3e57"  # hex 32
PLACEHOLDER = "your_tradleware_api_key_here"


class TestEntropyEstimate:
  @pytest.mark.parametrize("key,at_least", [
    (STRONG_KEY, 250),                     # openssl rand -hex 32
    ("Xk7pQ2mR9vT4wY", 80),                # 14 mixed-case + digits
    ("abcdefghijklmnopqrstuvwx", 100),     # long, single class
  ])
  def test_large_keys_score_high(self, key, at_least):
    assert estimate_bits(key) >= at_least

  @pytest.mark.parametrize("key", ["short", "abc123", "12345678", ""])
  def test_small_keys_score_low(self, key):
    assert estimate_bits(key) < MIN_ACCEPTABLE_BITS

  def test_character_classes_widen_the_alphabet(self):
    assert estimate_bits("aaaaaaaaaaaa") < estimate_bits("aA1!aA1!aA1!")

  def test_empty_key_is_zero(self):
    assert estimate_bits("") == 0


class TestAssessment:
  def test_a_properly_generated_key_passes(self):
    assessment = assess_key(STRONG_KEY)
    assert assessment.level == "ok"

  @pytest.mark.parametrize("key", [None, "", "   "])
  def test_a_missing_key_is_critical(self, key):
    assessment = assess_key(key)
    assert assessment.level == "critical"
    assert "cannot accept webhooks" in assessment.reason

  @pytest.mark.parametrize("key", [
    "your_tradleware_api_key_here",
    "generate_with_openssl_rand_hex_32",
    "another_tradleware_api_key_here",
    "your_webhook_auth_key",
    "CHANGEME",
    "replace_me_please",
    "some-example-key",
  ])
  def test_published_placeholders_are_critical(self, key):
    """These values are in the public repository, so they are not secret at all."""
    assessment = assess_key(key)
    assert assessment.level == "critical"
    assert "placeholder" in assessment.reason.lower()

  def test_placeholder_detection_is_case_insensitive(self):
    assert assess_key("Your_Tradleware_API_Key_Here").level == "critical"

  @pytest.mark.parametrize("key", ["hunter2", "mybot2024", "abc123def", "trading1"])
  def test_short_keys_are_weak(self, key):
    assessment = assess_key(key)
    assert assessment.level == "weak"
    assert "guessed" in assessment.reason

  @pytest.mark.parametrize("key", ["a" * 64, "abab" * 20, "1111111111111111"])
  def test_repetitive_keys_are_weak_however_long(self, key):
    """Length alone can clear the bit estimate; distinct characters cannot."""
    assessment = assess_key(key)
    assert assessment.level == "weak"
    assert "distinct characters" in assessment.reason

  def test_a_reasonable_random_key_is_not_flagged(self):
    """A 14-character mixed key is ~83 bits — must not produce a false positive."""
    assert assess_key("Xk7pQ2mR9vT4wY").level == "ok"

  def test_non_string_keys_do_not_crash(self):
    for key in (12345, ["a"], {"k": "v"}, True):
      assert assess_key(key).level in {"ok", "weak", "critical"}

  def test_the_reason_never_contains_the_key(self):
    """The reason is rendered on the dashboard and written to the log."""
    for key in ("hunter2", PLACEHOLDER, "a" * 40, STRONG_KEY):
      assert key not in assess_key(key).reason


class TestSharedKeys:
  def test_unique_keys_report_nothing(self):
    assert find_shared_keys({"a": "key-one", "b": "key-two"}) == {}

  def test_a_reused_key_names_the_other_bots(self):
    shared = find_shared_keys({"a": "same", "b": "same", "c": "different"})
    assert shared == {"a": ["b"], "b": ["a"]}

  def test_three_way_reuse(self):
    shared = find_shared_keys({"a": "same", "b": "same", "c": "same"})
    assert sorted(shared["a"]) == ["b", "c"]

  def test_missing_keys_are_not_counted_as_shared(self):
    assert find_shared_keys({"a": None, "b": "", "c": "   "}) == {}


class TestStartupReporting:
  def test_findings_are_collected_per_bot(self, app, crypto_trader, stock_trader):
    crypto_trader.tradleware_api_key = "weak"
    stock_trader.tradleware_api_key = STRONG_KEY
    findings = app.api_key_findings()
    assert "fakebot" in findings
    assert "fakestock" not in findings, "a strong key must not be reported"

  def test_reuse_is_reported_even_when_both_keys_are_strong(self, app, crypto_trader,
                                                            stock_trader):
    crypto_trader.tradleware_api_key = STRONG_KEY
    stock_trader.tradleware_api_key = STRONG_KEY
    findings = app.api_key_findings()
    assert set(findings) == {"fakebot", "fakestock"}
    assert "Shared with" in findings["fakebot"].reason

  def test_a_placeholder_stays_critical_when_also_shared(self, app, crypto_trader,
                                                         stock_trader):
    crypto_trader.tradleware_api_key = PLACEHOLDER
    stock_trader.tradleware_api_key = PLACEHOLDER
    assert app.api_key_findings()["fakebot"].level == "critical"

  def test_startup_logs_the_findings_without_refusing_to_start(self, app,
                                                               crypto_trader, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    crypto_trader.tradleware_api_key = PLACEHOLDER
    app._report_weak_api_keys()          # must not raise
    assert "fakebot" in caplog.text
    assert "openssl rand -hex 32" in caplog.text
    assert PLACEHOLDER not in caplog.text, "the weak key itself must not be logged"


class TestDashboardSurfacing:
  @pytest.fixture
  def dashboard(self, app, crypto_trader):
    app.TRUSTED_IPS = ["192.168.1.50"]
    return app

  async def test_a_weak_key_raises_a_banner(self, client_factory, dashboard,
                                            crypto_trader):
    crypto_trader.tradleware_api_key = "weak"
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "webhook API key that needs attention" in response.text
    assert "openssl rand -hex 32" in response.text

  async def test_a_placeholder_is_called_out_as_published(self, client_factory,
                                                          dashboard, crypto_trader):
    crypto_trader.tradleware_api_key = PLACEHOLDER
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "published in the Tradleware repository" in response.text

  async def test_a_strong_key_shows_nothing(self, client_factory, dashboard,
                                            crypto_trader):
    crypto_trader.tradleware_api_key = STRONG_KEY
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "webhook API key that needs attention" not in response.text

  async def test_the_key_is_never_rendered_even_when_flagged(self, client_factory,
                                                             dashboard, crypto_trader):
    crypto_trader.tradleware_api_key = "weakbutsecret"
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "weakbutsecret" not in response.text

  async def test_the_dashboard_still_renders_with_findings(self, client_factory,
                                                           dashboard, crypto_trader):
    crypto_trader.tradleware_api_key = "weak"
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert response.status_code == 200

  async def test_the_flagged_bot_card_carries_a_marker(self, client_factory, dashboard,
                                                       crypto_trader):
    """The banner names the bot, but the card itself has to point at the offending row."""
    crypto_trader.tradleware_api_key = "weak"
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "&#9888;" in response.text, "no warning marker beside the API key row"
    assert "openssl rand -hex 32" in response.text

  async def test_the_marker_carries_the_fix_in_a_tooltip(self, client_factory,
                                                         dashboard, crypto_trader):
    crypto_trader.tradleware_api_key = "weak"
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "cursor-help" in response.text
    assert "tradleware_api_key in this bot" in response.text

  async def test_a_sound_key_gets_no_marker(self, client_factory, dashboard,
                                            crypto_trader):
    crypto_trader.tradleware_api_key = STRONG_KEY
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert "&#9888;" not in response.text

  async def test_only_the_offending_bot_is_marked(self, client_factory, dashboard,
                                                  crypto_trader, stock_trader):
    crypto_trader.tradleware_api_key = "weak"
    stock_trader.tradleware_api_key = STRONG_KEY
    async with client_factory(peer="192.168.1.50") as client:
      response = await client.get("/")
    assert response.text.count("&#9888;") == 1
