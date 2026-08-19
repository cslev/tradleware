"""
Webhook API key strength assessment.

Each bot authenticates its webhook with a free-form ``tradleware_api_key`` from its YAML
config, so nothing stops it being ``test123`` or the placeholder copied straight out of
the example file. The endpoint is reachable from the internet and is not rate limited,
so a guessable key is the whole of the protection gone.

Nothing here refuses to load a bot. A weak key still works, and failing at startup over
a configuration opinion would stop a running deployment from trading — a far worse
outcome than the risk being warned about. The findings are surfaced on the dashboard and
in the startup log instead, and it stays the operator's call.
"""

# Standard library imports
from collections import namedtuple
import math

# Assessment outcome. `level` is one of 'ok', 'weak' or 'critical'; `reason` is written
# to be shown to a person, and `bits` is the estimated search space.
KeyAssessment = namedtuple('KeyAssessment', 'level reason bits')

# Below this, a key is small enough to be worth guessing against an endpoint that does
# not throttle failed attempts. A key generated the documented way — openssl rand
# -hex 32 — lands around 330 bits, so the bar is nowhere near it.
MIN_ACCEPTABLE_BITS = 64

# A key drawn from almost no distinct characters ('aaaa...') can clear the bit estimate
# on length alone, so it is rejected separately.
MIN_DISTINCT_CHARACTERS = 5

# Substrings of the placeholders shipped in the .yaml.example files, plus the usual
# stand-ins. These are public in the repository, so a key containing one is not secret
# at all — that is worse than merely short.
_PLACEHOLDER_MARKERS = (
  'your_tradleware_api_key', 'another_tradleware_api_key', 'your_webhook_auth_key',
  'generate_with_openssl', 'your_api_key', 'apikeyhere', 'changeme', 'placeholder',
  'example', 'replace_me', 'todo', 'xxxx', 'test123', 'secret123',
)


def estimate_bits(key: str) -> int:
  """
  Estimate the search space of a key, in bits, from its length and character classes.

  This is the standard length x log2(alphabet) estimate. It measures how *large* the
  key is, not how *unpredictable* — 'Password2026' scores the same as twelve random
  characters — so it catches keys that are too small to bother guessing, and cannot
  catch one that is merely obvious. The placeholder check covers the common case of the
  latter.
  """
  if not key:
    return 0
  alphabet = 0
  if any(character.islower() for character in key):
    alphabet += 26
  if any(character.isupper() for character in key):
    alphabet += 26
  if any(character.isdigit() for character in key):
    alphabet += 10
  if any(not character.isalnum() for character in key):
    alphabet += 32
  if alphabet <= 1:
    return 0
  return int(len(key) * math.log2(alphabet))


def assess_key(key) -> KeyAssessment:
  """
  Judge one bot's webhook key.

  Returns 'critical' when the key is absent or is a published placeholder, 'weak' when
  it is too small or too repetitive to resist guessing, and 'ok' otherwise.
  """
  if key is None or not str(key).strip():
    return KeyAssessment('critical', 'No API key is configured — this bot cannot '
                                     'accept webhooks at all.', 0)

  text = str(key).strip()
  lowered = text.lower()
  bits = estimate_bits(text)

  for marker in _PLACEHOLDER_MARKERS:
    if marker in lowered:
      return KeyAssessment(
        'critical',
        'This is still an example placeholder. The value is published in the '
        'Tradleware repository, so anyone can read it — regenerate the key now.',
        bits)

  if len(set(text)) < MIN_DISTINCT_CHARACTERS:
    return KeyAssessment(
      'weak',
      f'Only {len(set(text))} distinct characters, so the key is trivial to guess '
      'however long it is.', bits)

  if bits < MIN_ACCEPTABLE_BITS:
    return KeyAssessment(
      'weak',
      f'Roughly {bits} bits of search space from {len(text)} characters. The webhook '
      'does not throttle failed attempts, so a key this small can be guessed.', bits)

  return KeyAssessment('ok', f'Roughly {bits} bits of search space.', bits)


def find_shared_keys(keys_by_bot: dict) -> dict:
  """
  Find keys used by more than one bot.

  Per-bot keys are the reason one compromised key does not expose every bot, which the
  README states as a design property. Reusing one quietly removes it. Returns a mapping
  of bot id to the other bots sharing its key.
  """
  owners = {}
  for bot_id, key in keys_by_bot.items():
    if key and str(key).strip():
      owners.setdefault(str(key).strip(), []).append(bot_id)

  shared = {}
  for bot_ids in owners.values():
    if len(bot_ids) > 1:
      for bot_id in bot_ids:
        shared[bot_id] = [other for other in bot_ids if other != bot_id]
  return shared
