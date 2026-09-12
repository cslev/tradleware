import csv
import gzip
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import shutil
import sys
import json
import threading
from pathlib import Path
import colorama
from colorama import Fore, Style
import requests
from .get_env import get_env  # Import centralized get_env helper

# Initialize colorama
colorama.init(autoreset=True)


#################### GOTIFY DELIVERY ####################
# Sending a Gotify notification is an ordinary blocking HTTP request, and loggers are
# called from inside async request handlers. Posting inline would stall the event loop
# for the whole round trip, so one slow or unreachable notification server would freeze
# trading for every bot — and any unauthenticated request that produces an ERROR could
# trigger that stall on demand. Notifications are therefore handed to a single
# background thread, shared by every CustomLogger in the process, and the caller
# returns immediately.

_GOTIFY_QUEUE = queue.Queue(maxsize=100)
_gotify_worker = None
_gotify_worker_lock = threading.Lock()
_gotify_dropped = 0


def _gotify_worker_loop():
  """Deliver queued notifications one at a time, forever. Never dies on an error."""
  while True:
    job = _GOTIFY_QUEUE.get()
    try:
      job()
    except Exception:  # pylint: disable=broad-exception-caught
      pass  # a failed notification must never take the worker down with it
    finally:
      _GOTIFY_QUEUE.task_done()


def _ensure_gotify_worker() -> None:
  """Start the delivery thread on first use. Daemon, so it never blocks process exit."""
  global _gotify_worker  # pylint: disable=global-statement
  if _gotify_worker is not None and _gotify_worker.is_alive():
    return
  with _gotify_worker_lock:
    if _gotify_worker is None or not _gotify_worker.is_alive():
      _gotify_worker = threading.Thread(
        target=_gotify_worker_loop, name='gotify-delivery', daemon=True
      )
      _gotify_worker.start()


def _enqueue_gotify(job, std_logger) -> bool:
  """
  Hand a delivery off to the worker, dropping it if the backlog is already full.

  Dropping is deliberate: blocking here would reintroduce exactly the stall this
  queue exists to prevent. Drops are reported through the standard logger — never
  through CustomLogger, which would enqueue another notification.
  """
  global _gotify_dropped  # pylint: disable=global-statement
  _ensure_gotify_worker()
  try:
    _GOTIFY_QUEUE.put_nowait(job)
    return True
  except queue.Full:
    _gotify_dropped += 1
    if _gotify_dropped == 1 or _gotify_dropped % 100 == 0:
      std_logger.warning(
        f"Gotify backlog is full — dropped {_gotify_dropped} notification(s) so far. "
        "Events are being produced faster than the notification server accepts them."
      )
    return False


def flush_gotify_queue(timeout: float = 5.0) -> bool:
  """
  Wait for queued notifications to drain, for use during shutdown.

  The worker is a daemon thread, so without this the last few notifications would be
  discarded when the process exits. Returns False if the backlog outlived the timeout.
  """
  # Queue.join() cannot take a timeout, so wait on it from a thread we can abandon
  waiter = threading.Thread(target=_GOTIFY_QUEUE.join, daemon=True)
  waiter.start()
  waiter.join(timeout)
  return not waiter.is_alive()


def _deliver_gotify(url: str, token: str, payload: dict, std_logger) -> None:
  """Perform the actual HTTP POST. Runs on the worker thread, never on the event loop."""
  title = payload.get("title", "")
  response = None
  try:
    response = requests.post(
      f"{url}/message",
      headers={"Content-Type": "application/json", "X-Gotify-Key": token},
      data=json.dumps(payload),
      timeout=10
    )
    response.raise_for_status()
    std_logger.debug(f"✅ Gotify notification sent successfully: '{title}'")
  except requests.exceptions.HTTPError as http_err:
    body = response.text if response is not None else ''
    std_logger.error(f"❌ Gotify HTTP error occurred: {http_err} - {body}")
  except requests.exceptions.ConnectionError as conn_err:
    std_logger.error(
      f"❌ Gotify connection error occurred: {conn_err}. Is Gotify server running at {url}?"
    )
  except requests.exceptions.Timeout as timeout_err:
    std_logger.error(f"❌ Gotify request timed out: {timeout_err}")
  except requests.exceptions.RequestException as req_err:
    std_logger.error(f"❌ An unexpected Gotify request error occurred: {req_err}")
  except Exception as exc:  # pylint: disable=broad-exception-caught
    std_logger.error(f"❌ An unexpected error occurred: {exc}")


# Define a custom SUCCESS log level
SUCCESS_LEVEL = 25 # Between INFO (20) and WARNING (30)
logging.addLevelName(SUCCESS_LEVEL, 'SUCCESS')

# Define colors for different log levels
log_level_colors = {
  logging.DEBUG: Fore.BLUE,
  logging.INFO: Fore.WHITE,
  logging.WARNING: Fore.YELLOW,
  logging.ERROR: Fore.RED,
  SUCCESS_LEVEL: Fore.GREEN,
  logging.CRITICAL: Fore.MAGENTA

}

#################### ROTATING LOG FILE ####################
# The log file has no external rotation in the Docker image, so it is capped here.
# Without a cap it grows without bound — a webhook that gets probed writes a couple of
# hundred bytes per rejected request, which fills a Raspberry Pi SD card in time.

_LOG_MAX_BYTES_DEFAULT = 10 * 1024 * 1024   # 10 MB per file
_LOG_BACKUP_COUNT_DEFAULT = 5               # plus 5 older files: ~60 MB ceiling

_file_handlers = {}
_file_handler_lock = threading.Lock()


def _int_env(key: str, default: int) -> int:
  """Read an integer environment variable, falling back on anything unparseable."""
  try:
    return int(str(get_env(key, str(default))).strip())
  except (TypeError, ValueError):
    return default


def _bool_env(key: str, default: bool) -> bool:
  """Read a boolean environment variable, accepting true/false, 1/0, yes/no, on/off."""
  raw = get_env(key, 'true' if default else 'false')
  return str(raw).strip().lower() in ('true', '1', 'yes', 'on')


def _gzip_namer(name: str) -> str:
  """Give rotated files a .gz suffix, so tradleware.log.1 becomes tradleware.log.1.gz."""
  return name + '.gz'


def _gzip_rotator(source: str, dest: str) -> None:
  """
  Compress a rolled-over log file in place of a plain rename.

  Log text compresses about 8x, turning the default ~60 MB ceiling into ~16 MB, which
  matters on an SD card. Streamed rather than read whole so memory use stays flat on a
  Raspberry Pi. Level 6 is the sweet spot: level 9 costs three times the CPU for a few
  percent, and xz costs twenty-five times for a third less.
  """
  with open(source, 'rb') as raw, gzip.open(dest, 'wb', compresslevel=6) as compressed:
    shutil.copyfileobj(raw, compressed)
  os.remove(source)


def _get_rotating_file_handler(logfile_name: str) -> RotatingFileHandler:
  """
  Return the one rotating handler for this log file, shared across every logger.

  Sharing is required, not just tidy: every CustomLogger writes to the same file, and
  independent RotatingFileHandlers would each track the size on their own and roll
  over whenever they individually hit the limit — renaming the file out from under the
  others, which carry on writing to the orphaned inode. A single handler serialises
  both writes and rollover through its own lock.
  """
  handler = _file_handlers.get(logfile_name)
  if handler is not None:
    return handler
  with _file_handler_lock:
    if logfile_name not in _file_handlers:
      logs_dir = Path(__file__).resolve().parent.parent / "logs"
      logs_dir.mkdir(parents=True, exist_ok=True)
      handler = RotatingFileHandler(
        logs_dir / logfile_name,
        maxBytes=_int_env('LOG_MAX_BYTES', _LOG_MAX_BYTES_DEFAULT),
        backupCount=_int_env('LOG_BACKUP_COUNT', _LOG_BACKUP_COUNT_DEFAULT),
        encoding='utf-8'  # log messages carry emoji; never depend on the host locale
      )
      handler.setFormatter(logging.Formatter(
        '%(asctime)s - [%(name)s] - %(funcName)s-(line %(lineno)d) - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
      ))
      if _bool_env('LOG_COMPRESS_ROTATED', True):
        # Compression runs during rollover, on whichever thread emits the record that
        # trips the size limit. That is roughly 0.1s per 10 MB here and under a second
        # on a Pi, once per full log file, so it is left inline rather than deferred.
        handler.namer = _gzip_namer
        handler.rotator = _gzip_rotator
      # Left at NOTSET on purpose: each logger already filters by its own level, so the
      # shared handler must not impose whichever level happened to be set up first.
      _file_handlers[logfile_name] = handler
  return _file_handlers[logfile_name]


class _CSVFormatter(logging.Formatter):
  """
  Renders each record as one CSV row: timestamp, level, `fields` in call order, message.

  Built with the csv module rather than "','.join(...)", because several of the
  candidate columns — a request path, a User-Agent, the message itself — are supplied
  by whoever is making the request. Any of them can legitimately contain a comma, a
  quote, or a newline; hand-joining would let one such value silently corrupt that row
  and shift every column after it, which defeats the entire point of a parseable log.
  csv.writer quotes a field only when it needs to, so an ordinary row stays plain text.

  A field a given call site's `extra=` did not supply renders as an empty column
  rather than raising — logging a scanner hit must never itself throw.
  """

  def __init__(self, fields, datefmt='%Y-%m-%d %H:%M:%S'):
    super().__init__(datefmt=datefmt)
    self.fields = tuple(fields)

  def format(self, record):
    row = (
      [self.formatTime(record, self.datefmt), record.levelname]
      + [str(getattr(record, f, '')) for f in self.fields]
      + [record.getMessage()]
    )
    buf = io.StringIO()
    csv.writer(buf, lineterminator='').writerow(row)
    return buf.getvalue()


def _get_csv_rotating_handler(logfile_name: str, fields) -> RotatingFileHandler:
  """
  Return the one CSV-formatting rotating handler for this file, shared like
  `_get_rotating_file_handler` and for the same reason — two independent handlers on
  one file corrupt each other's rollover.

  Writes a header row the first time this file is created, never again: an existing
  file being reopened in append mode (a restart) must not gain a second header
  part-way through. Kept in the same registry as the plain-text handlers so a filename
  collision returns the existing handler rather than opening the file twice with
  conflicting formatters.
  """
  handler = _file_handlers.get(logfile_name)
  if handler is not None:
    return handler
  with _file_handler_lock:
    if logfile_name not in _file_handlers:
      logs_dir = Path(__file__).resolve().parent.parent / "logs"
      logs_dir.mkdir(parents=True, exist_ok=True)
      path = logs_dir / logfile_name
      needs_header = not path.exists() or path.stat().st_size == 0
      handler = RotatingFileHandler(
        path,
        maxBytes=_int_env('LOG_MAX_BYTES', _LOG_MAX_BYTES_DEFAULT),
        backupCount=_int_env('LOG_BACKUP_COUNT', _LOG_BACKUP_COUNT_DEFAULT),
        encoding='utf-8'
      )
      handler.setFormatter(_CSVFormatter(fields))
      if _bool_env('LOG_COMPRESS_ROTATED', True):
        handler.namer = _gzip_namer
        handler.rotator = _gzip_rotator
      if needs_header:
        header = ','.join(('timestamp', 'level') + tuple(fields) + ('message',))
        handler.stream.write(header + '\n')
        handler.stream.flush()
      _file_handlers[logfile_name] = handler
  return _file_handlers[logfile_name]


def get_csv_file_logger(name: str, logfile_name: str, fields,
                        level: int = logging.INFO) -> logging.Logger:
  """
  A logger, like get_plain_file_logger, with no Gotify path and its own file — but
  writing one CSV row per record instead of a free-text line, for events meant to be
  grepped, awked or loaded with pandas.read_csv rather than read by eye.

  `fields` is fixed once, at construction, not chosen per call: every row in the file
  then has the same shape, which is the property that actually makes a log parseable.
  A schema a caller could silently redefine call to call would not be. Pass the values
  per call via the standard logging `extra={...}` mapping — see the two access_logger
  call sites in app.py.
  """
  csv_logger = logging.getLogger(name)
  csv_logger.propagate = False
  csv_logger.setLevel(level)
  if not csv_logger.handlers:
    csv_logger.addHandler(_get_csv_rotating_handler(logfile_name, fields))
  return csv_logger


def get_plain_file_logger(name: str, logfile_name: str,
                          level: int = logging.INFO) -> logging.Logger:
  """
  A logger that writes to its own rotating file and can never reach Gotify.

  Built for routine noise that is worth keeping on disk but must never page anyone —
  an unauthenticated GET from a mass scanner, say. CustomLogger is the wrong base for
  that: even with no Gotify URL passed in, its constructor falls back to the
  GOTIFY_SERVER_URL / GOTIFY_APP_TOKEN env vars, and whether a level notifies depends
  on GOTIFY_LOG_LEVEL — a setting an operator may lower for an unrelated reason (to see
  more of their own INFO lines) without meaning to also start paging on scanner traffic.
  A plain logging.Logger has no notification path to disable in the first place, so it
  cannot be re-coupled to Gotify by a later change elsewhere.

  Also keeps this traffic out of tradleware.log, on its own rotation schedule, so a
  busy scanner cannot crowd out real events in the file an operator actually reads.

  propagate is left False so records never reach the root logger — otherwise anything
  attached there (a test's caplog, a future root-level handler) would see this traffic
  too, which is the exact coupling this function exists to avoid.
  """
  plain_logger = logging.getLogger(name)
  plain_logger.propagate = False
  plain_logger.setLevel(level)
  if not plain_logger.handlers:
    plain_logger.addHandler(_get_rotating_file_handler(logfile_name))
  return plain_logger


_excepthook_installed = False

def _install_global_excepthook(logger_instance):
  """Install a sys.excepthook that routes unhandled exceptions through the logging system.

  Only installed once regardless of how many CustomLogger instances are created.
  """
  global _excepthook_installed  # pylint: disable=global-statement
  if _excepthook_installed:
    return
  _excepthook_installed = True

  def _handle_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
      # Let Ctrl+C behave normally
      sys.__excepthook__(exc_type, exc_value, exc_tb)
      return
    logger_instance.logger.critical(
      "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
    )

  sys.excepthook = _handle_exception


def format_log_buffer(buffer) -> list:
  """
  Render a trader's log buffer for the dashboard, dating each day once.

  Entries are `(day, clock, level, message)` tuples. A `── YYYY-MM-DD ──` header is
  emitted before the first entry and again whenever the day rolls over, so a card left
  open across midnight — or showing a bot that has been quiet for days — says which day
  its lines belong to without repeating the date on all fifty rows.

  Plain strings are passed through untouched, so a buffer populated before this format
  existed still renders.
  """
  lines = []
  current_day = None
  for entry in buffer:
    if not isinstance(entry, tuple):
      lines.append(entry)
      continue
    day, clock, level, message = entry
    if day != current_day:
      lines.append(f"── {day} ──")
      current_day = day
    lines.append(f"[{clock}] {level}: {message}")
  return lines


class ColoredFormatter(logging.Formatter):
  def format(self, record):
    # Store original levelname and message to restore after formatting
    original_levelname = record.levelname
    original_msg = record.msg

    # Temporarily set levelname for custom levels if needed for string formatting
    if record.levelno == SUCCESS_LEVEL:
      record.levelname = 'SUCCESS'

    # Determine icon based on level
    icon_str = ""
    if record.levelno == logging.ERROR:
      icon_str = " ❌ " # Red cross mark
    elif record.levelno == SUCCESS_LEVEL:
      icon_str = " ✅ " # Green check mark

    # Prepend the icon to the message. This will be processed by the super().format method.
    record.msg = f"{icon_str}{original_msg}"

    # Get the standard formatted message (e.g., "2023-10-27 10:00:00 - [MyApp] - INFO - ✅ This is a success message")
    formatted_message_parts = super().format(record)

    # Apply color to the entire line
    level_color = log_level_colors.get(record.levelno, Fore.WHITE)
    final_colored_message = f"{level_color}{formatted_message_parts}{Style.RESET_ALL}"

    # Restore original record attributes to avoid side effects for other handlers or formatters
    record.levelname = original_levelname
    record.msg = original_msg

    return final_colored_message

class CustomLogger:
  def __init__(self,
               name,
               logfile_name:str="tradleware.log",
               gotify_url:str=None,
               gotify_token:str=None,
               gotify_log_level:int=logging.WARNING
               ):
    self.logger = logging.getLogger(name)

    # Helper: prefer explicit arg if non-empty, otherwise read env and treat empty string as unset (None)
    def _coerce_str_arg(arg_val, env_key, default=None):
      if arg_val is not None and str(arg_val).strip() != "":
        return str(arg_val).strip()
      env_val = get_env(env_key, default if default is not None else "")
      return env_val.strip() if isinstance(env_val, str) and env_val.strip() != "" else None

    self.gotify_url = _coerce_str_arg(gotify_url, 'GOTIFY_SERVER_URL')
    self.gotify_token = _coerce_str_arg(gotify_token, 'GOTIFY_APP_TOKEN')

    # Parse gotify log level safely (accept int/string), fall back to logging.WARNING on failure
    level_src = None
    if gotify_log_level is not None and str(gotify_log_level).strip() != "":
      level_src = str(gotify_log_level).strip()
    else:
      level_src = get_env('GOTIFY_LOG_LEVEL', str(logging.WARNING))
    try:
      self.gotify_log_level = int(level_src)
    except Exception:
      self.gotify_log_level = logging.WARNING

    # Parse general log level from env (LOG_LEVEL), default to DEBUG if not set
    log_level_src = get_env('LOG_LEVEL', str(logging.DEBUG))
    try:
      self.general_log_level = int(str(log_level_src).strip())
    except Exception:
      self.general_log_level = logging.DEBUG

    self.logger.setLevel(self.general_log_level)
    # The Gotify app token grants push access to the user's server — report only
    # whether it is configured, never the value itself
    gotify_token_state = 'set' if self.gotify_token else 'not set'
    print(f"Logger initialized by {name} - gotify_url: {self.gotify_url}, gotify_token: {gotify_token_state}, gotify_log_level: {self.gotify_log_level}, general_log_level: {self.general_log_level}")

    # Only add handlers if they don't already exist (avoid duplicates)
    if not self.logger.handlers:
      # Create console handler
      ch = logging.StreamHandler(sys.stdout)
      ch.setLevel(self.general_log_level)
      # Create a formatter with the current log level colors
      formatter = ColoredFormatter('%(asctime)s - [%(name)s] - %(funcName)s-(line %(lineno)d) - %(levelname)s - %(message)s',
                                   datefmt='%Y-%m-%d %H:%M:%S')
      ch.setFormatter(formatter)

      # File handler — one rotating handler shared by every logger in the process
      fh = _get_rotating_file_handler(logfile_name)

      # Add the handlers to the logger
      self.logger.addHandler(ch)
      self.logger.addHandler(fh)

    # Route uncaught exceptions to the log file (installed once globally)
    _install_global_excepthook(self)

  def debug(self, message, exc_info=False):
    self.logger.debug(message, exc_info=exc_info)
    if self.gotify_url and self.gotify_token and logging.DEBUG >= self.gotify_log_level:
      self.send_gotify_notification(title="🐛 Debug Notification",
                                  message=message,
                                  priority=1)  # Low priority for debug messages

  def info(self, message, exc_info=False):
    self.logger.info(message, exc_info=exc_info)
    if self.gotify_url and self.gotify_token and logging.INFO >= self.gotify_log_level:
      self.send_gotify_notification(title="ℹ️ Info Notification",
                                  message=message,
                                  priority=5)  # Normal priority for info messages

  def warning(self, message, exc_info=False):
    self.logger.warning(message, exc_info=exc_info)
    if self.gotify_url and self.gotify_token and logging.WARNING >= self.gotify_log_level:
      self.send_gotify_notification(title="⚠️ Warning Notification",
                                  message=message,
                                  priority=7)  # High priority for warning messages

  def error(self, message, exc_info=False):
    self.logger.error(message, exc_info=exc_info)
    if self.gotify_url and self.gotify_token and logging.ERROR >= self.gotify_log_level:
      self.send_gotify_notification(title="❌ Error Notification",
                                  message=message,
                                  priority=9)  # Very high priority for error messages

  def critical(self, message, exc_info=False):
    self.logger.critical(message, exc_info=exc_info)
    if self.gotify_url and self.gotify_token and logging.CRITICAL >= self.gotify_log_level:
      self.send_gotify_notification(title="🚨 Critical Notification",
                                  message=message,
                                  priority=10)  # Maximum priority for critical messages

  def success(self, message, exc_info=False):
    """
    Logs a message with the custom SUCCESS level.
    """
    self.logger.log(SUCCESS_LEVEL, message, exc_info=exc_info)
    if self.gotify_url and self.gotify_token and (SUCCESS_LEVEL >= self.gotify_log_level or self.gotify_log_level <= logging.WARNING):
      self.send_gotify_notification(title="✅ Success Notification",
                                  message=message,
                                  priority=6)  # Medium-high priority for success messages


  def send_gotify_notification(self,
                               title: str,
                               message: str,
                               priority: int = 5,
                               extras: dict = None):
    """
    Sends a notification to a Gotify service.

    Args:
      title (str): The title of the notification.
      message (str): The main message content.
      priority (int): The priority level (1-10, 1=lowest, 5=default, 10=highest).
      extras (dict, optional): A dictionary of extra key-value pairs for advanced Gotify clients.
                                 Defaults to None.
    Returns immediately without waiting for the network: the notification is queued
    for the background delivery thread. Callers include async request handlers, where
    a blocking HTTP call would stall the whole event loop.

    Returns:
      bool: True if the notification was queued, False if the backlog was full and it
            was dropped. Not an indication that delivery succeeded.
    """
    payload = {
      "title": title,
      "message": message,
      "priority": priority
    }

    if extras:
      payload["extras"] = extras

    url, token, std_logger = self.gotify_url, self.gotify_token, self.logger
    return _enqueue_gotify(
      lambda: _deliver_gotify(url, token, payload, std_logger), std_logger
    )
# Example usage
if __name__ == "__main__":
  logger1 = CustomLogger('MyClass')
  logger2 = CustomLogger('AnotherClass')
  logger3 = CustomLogger('ThirdClass')

  logger1.debug("This is a debug message from MyClass.")
  logger1.info("This is an info message from MyClass.")
  logger2.warning("This is a warning message from AnotherClass.")

  # Messages with icons
  logger3.error("This is an error message from ThirdClass - something went wrong!")
  logger1.success("Operation completed successfully!")

  # Adjusted CRITICAL message to reflect its standard severity
  logger2.critical("CRITICAL: System integrity compromised. Immediate attention required.")
  logger2.info("This is an info message from AnotherClass.")
