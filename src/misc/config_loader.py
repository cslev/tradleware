"""
config_loader.py — Tradleware Bot Configuration Loader

Scans the bot_configs/ directory tree for YAML bot definition files and returns
a list of validated trader config dictionaries ready for use by app.py.

Directory layout expected:
  bot_configs/
    crypto/
      okx.yaml          → exchange = 'okx'
      cryptocom.yaml    → exchange = 'cryptocom'
      ir.yaml           → exchange = 'ir'
    stock/
      ibkr.yaml         → broker = 'ibkr'

Each crypto YAML must have a top-level `bots:` list.
Each stock YAML must have a top-level `bots:` list and, for IBKR, a `gateway:` section.

Returned dict shape — crypto bot:
  {
    'bot_type':              'crypto',
    'exchange':              'okx',          # from filename
    'id':                    'mybtcbot',
    'api_key':               '...',
    'secret_key':            '...',
    'passphrase':            '...',          # optional
    'subaccount_name':       '...',          # optional
    'hostname':              '...',
    'stablecoin_fiat_pair':  'USDT/SGD',
    'crypto_stablecoin_pair':'BTC/USDT',
    'tradleware_api_key':    '...',
  }

Returned dict shape — IBKR stock bot:
  {
    'bot_type':          'stock',
    'broker':            'ibkr',             # from filename
    'id':                'myapplebot',
    'account_id':        'U1234567',
    'symbol':            'AAPL',
    'extended_hours':    False,
    'fractional_shares': False,
    'tradleware_api_key': '...',
    'gateway': {
      'host':         '127.0.0.1',
      'port':         8888,
      'username':     '...',
      'password':     '...',
      'trading_mode': 'paper',
      'vnc_password': '...',
      'read_only':    False,
    },
  }
"""

from pathlib import Path
from typing import Any

import yaml

from src.misc.logger import CustomLogger

# Logger used only during config loading (before traders are initialised)
_loader_logger = CustomLogger(name='ConfigLoader')

# Project root is three levels up from this file (src/misc/config_loader.py)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_BOT_CONFIGS_DIR = _PROJECT_ROOT / 'bot_configs'

# Required fields per bot type (id and tradleware_api_key are always required)
# hostname is intentionally excluded — it is optional for all exchanges (each falls back to its default)
_CRYPTO_REQUIRED = {'id', 'api_key', 'secret_key',
                    'stablecoin_fiat_pair', 'crypto_stablecoin_pair', 'tradleware_api_key'}
_STOCK_REQUIRED = {'id', 'account_id', 'symbol', 'tradleware_api_key'}


def _load_yaml_file(path: Path) -> dict:
  """Load a YAML file and return the parsed dict, or None on failure."""
  try:
    with open(path, encoding='utf-8') as fh:
      return yaml.safe_load(fh)
  except FileNotFoundError:
    _loader_logger.warning(f"Config file not found: {path}")
  except yaml.YAMLError as exc:
    _loader_logger.error(f"YAML parse error in {path}: {exc}")
  return None


def _validate_bot(bot: dict, required: set, source: str) -> bool:
  """Return True if the bot dict contains all required fields with non-empty values."""
  missing = {f for f in required if not bot.get(f)}
  if missing:
    _loader_logger.warning(
      f"Skipping bot '{bot.get('id', '<no id>')}' in {source}: "
      f"missing or empty required fields: {', '.join(sorted(missing))}"
    )
    return False
  return True


def _normalise_id(bot_id: Any) -> str:
  """Normalise bot id to lowercase string."""
  return str(bot_id).strip().lower()


def _load_crypto_bots(exchange: str, path: Path) -> list:
  """Parse a crypto YAML file and return a list of validated bot config dicts."""
  data = _load_yaml_file(path)
  if not data:
    return []

  raw_bots = data.get('bots')
  if not isinstance(raw_bots, list) or not raw_bots:
    _loader_logger.warning(f"No bots defined or invalid format in {path}")
    return []

  configs = []
  for bot in raw_bots:
    if not isinstance(bot, dict):
      continue
    if not _validate_bot(bot, _CRYPTO_REQUIRED, str(path)):
      continue

    configs.append({
      'bot_type':               'crypto',
      'exchange':               exchange,
      'id':                     _normalise_id(bot['id']),
      'api_key':                str(bot['api_key']),
      'secret_key':             str(bot['secret_key']),
      'passphrase':             str(bot.get('passphrase', '')),
      'subaccount_name':        str(bot.get('subaccount_name', '')),
      'hostname':               str(bot['hostname']),
      'stablecoin_fiat_pair':   str(bot['stablecoin_fiat_pair']),
      'crypto_stablecoin_pair': str(bot['crypto_stablecoin_pair']),
      'tradleware_api_key':     str(bot['tradleware_api_key']),
    })
    _loader_logger.info(
      f"Loaded crypto bot '{configs[-1]['id']}' ({exchange.upper()})"
    )

  return configs


def _load_stock_bots(broker: str, path: Path) -> list:
  """Parse a stock YAML file and return a list of validated bot config dicts."""
  data = _load_yaml_file(path)
  if not data:
    return []

  raw_bots = data.get('bots')
  if not isinstance(raw_bots, list) or not raw_bots:
    _loader_logger.warning(f"No bots defined or invalid format in {path}")
    return []

  # Extract gateway section (broker-level, shared across all bots in this file)
  gateway = {}
  if broker == 'ibkr':
    raw_gw = data.get('gateway', {})
    gateway = {
      'host':         str(raw_gw.get('host', '127.0.0.1')),
      'port':         int(raw_gw.get('port', 8888)),
      'username':     str(raw_gw.get('username', '')),
      'password':     str(raw_gw.get('password', '')),
      'trading_mode': str(raw_gw.get('trading_mode', 'paper')),
      'vnc_password': str(raw_gw.get('vnc_password', '')),
      'read_only':    bool(raw_gw.get('read_only', False)),
    }

  configs = []
  for bot in raw_bots:
    if not isinstance(bot, dict):
      continue
    if not _validate_bot(bot, _STOCK_REQUIRED, str(path)):
      continue

    configs.append({
      'bot_type':           'stock',
      'broker':             broker,
      'id':                 _normalise_id(bot['id']),
      'account_id':         str(bot['account_id']),
      'symbol':             str(bot['symbol']).upper(),
      'extended_hours':     bool(bot.get('extended_hours', False)),
      'fractional_shares':  bool(bot.get('fractional_shares', False)),
      'tradleware_api_key': str(bot['tradleware_api_key']),
      'gateway':            gateway,
    })
    _loader_logger.info(
      f"Loaded stock bot '{configs[-1]['id']}' ({broker.upper()} / {configs[-1]['symbol']})"
    )

  return configs


def get_bot_configs() -> list:
  """
  Scan bot_configs/crypto/ and bot_configs/stock/ for YAML files, parse and
  validate each, and return a flat list of ready-to-use trader config dicts.

  Files that do not exist or cannot be parsed are skipped with a warning.
  Bots missing required fields are skipped with a warning.
  Returns an empty list if the bot_configs/ directory does not exist.
  """
  if not _BOT_CONFIGS_DIR.exists():
    _loader_logger.warning(
      f"bot_configs/ directory not found at {_BOT_CONFIGS_DIR}. "
      "No bots will be loaded."
    )
    return []

  all_configs = []

  # --- Crypto ---
  crypto_dir = _BOT_CONFIGS_DIR / 'crypto'
  if crypto_dir.exists():
    for yaml_file in sorted(crypto_dir.glob('*.yaml')):
      exchange = yaml_file.stem.lower()
      all_configs.extend(_load_crypto_bots(exchange, yaml_file))
  else:
    _loader_logger.warning("bot_configs/crypto/ not found, skipping crypto bots")

  # --- Stock ---
  stock_dir = _BOT_CONFIGS_DIR / 'stock'
  if stock_dir.exists():
    for yaml_file in sorted(stock_dir.glob('*.yaml')):
      broker = yaml_file.stem.lower()
      all_configs.extend(_load_stock_bots(broker, yaml_file))
  else:
    _loader_logger.warning("bot_configs/stock/ not found, skipping stock bots")

  _loader_logger.info(f"Config loader finished: {len(all_configs)} bot(s) found")
  return all_configs


if __name__ == '__main__':
  import json

  print("\n=== Tradleware Config Loader — Test Run ===\n")
  bot_configs = get_bot_configs()

  if not bot_configs:
    print("No bots found. Check bot_configs/ directory and YAML files.")
  else:
    print(f"Loaded {len(bot_configs)} bot(s):\n")
    for cfg in bot_configs:
      if cfg['bot_type'] == 'crypto':
        print(f"  [{cfg['bot_type'].upper()}] id={cfg['id']}  exchange={cfg['exchange'].upper()}")
        print(f"    pair:              {cfg['crypto_stablecoin_pair']}")
        print(f"    stablecoin/fiat:   {cfg['stablecoin_fiat_pair']}")
        print(f"    hostname:          {cfg['hostname']}")
        print(f"    subaccount:        {cfg['subaccount_name'] or '(none)'}")
        print(f"    api_key:           {cfg['api_key'][:6]}{'*' * 10}  (truncated)")
        print(f"    tradleware_key:    {cfg['tradleware_api_key'][:6]}{'*' * 10}  (truncated)")
      else:
        gw = cfg['gateway']
        print(f"  [{cfg['bot_type'].upper()}]  id={cfg['id']}  broker={cfg['broker'].upper()}")
        print(f"    symbol:            {cfg['symbol']}")
        print(f"    account_id:        {cfg['account_id']}")
        print(f"    extended_hours:    {cfg['extended_hours']}")
        print(f"    fractional_shares: {cfg['fractional_shares']}")
        print(f"    tradleware_key:    {cfg['tradleware_api_key'][:6]}{'*' * 10}  (truncated)")
        print(f"    gateway host:      {gw['host']}:{gw['port']}  mode={gw['trading_mode']}")
      print()

  print("Full config dump (secrets truncated):")
  safe = json.loads(json.dumps(bot_configs))
  for cfg in safe:
    for secret_field in ('api_key', 'secret_key', 'passphrase', 'tradleware_api_key'):
      if secret_field in cfg and cfg[secret_field]:
        cfg[secret_field] = cfg[secret_field][:6] + '...'
    if 'gateway' in cfg:
      for gf in ('password', 'vnc_password'):
        if cfg['gateway'].get(gf):
          cfg['gateway'][gf] = cfg['gateway'][gf][:3] + '...'
  print(json.dumps(safe, indent=2))
