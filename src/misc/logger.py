import logging
import sys
import json
from pathlib import Path
import colorama
from colorama import Fore, Style
import requests
from .get_env import get_env  # Import centralized get_env helper

# Initialize colorama
colorama.init(autoreset=True)


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
    print(f"Logger initialized by {name} - gotify_url: {self.gotify_url}, gotify_token: {self.gotify_token}, gotify_log_level: {self.gotify_log_level}, general_log_level: {self.general_log_level}")

    # Only add handlers if they don't already exist (avoid duplicates)
    if not self.logger.handlers:
      # Create console handler
      ch = logging.StreamHandler(sys.stdout)
      ch.setLevel(self.general_log_level)
      # Create a formatter with the current log level colors
      formatter = ColoredFormatter('%(asctime)s - [%(name)s] - %(funcName)s-(line %(lineno)d) - %(levelname)s - %(message)s',
                                   datefmt='%Y-%m-%d %H:%M:%S')
      ch.setFormatter(formatter)

      # File handler
      # --- Determine logs directory one level above this file ---
      base_dir = Path(__file__).resolve().parent.parent
      logs_dir = base_dir / "logs"
      logs_dir.mkdir(parents=True, exist_ok=True)
      logfile = logs_dir / logfile_name
      fh = logging.FileHandler(logfile, mode='a') #rewrite the logfile always
      fh.setLevel(self.general_log_level)
      fh.setFormatter(logging.Formatter('%(asctime)s - [%(name)s] - %(funcName)s-(line %(lineno)d) - %(levelname)s - %(message)s',
                                        datefmt='%Y-%m-%d %H:%M:%S'))

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
    Returns:
      bool: True if the notification was sent successfully, False otherwise.
    """
    headers = {
      "Content-Type": "application/json",
      "X-Gotify-Key": self.gotify_token
    }

    payload = {
      "title": title,
      "message": message,
      "priority": priority
    }

    if extras:
      payload["extras"] = extras

    try:
      response = requests.post(
        f"{self.gotify_url}/message",
        headers=headers,
        data=json.dumps(payload),
        timeout=10
      )
      response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
      self.logger.info(f"✅ Gotify notification sent successfully: '{title}'")
      return True
    except requests.exceptions.HTTPError as http_err:
      self.logger.error(f"❌ Gotify HTTP error occurred: {http_err} - {response.text}")
    except requests.exceptions.ConnectionError as conn_err:
      self.logger.error(f"❌ Gotify connection error occurred: {conn_err}. Is Gotify server running at {self.gotify_url}?")
    except requests.exceptions.Timeout as timeout_err:
      self.logger.error(f"❌ Gotify request timed out: {timeout_err}")
    except requests.exceptions.RequestException as req_err:
      self.logger.error(f"❌ An unexpected Gotify request error occurred: {req_err}")
    except Exception as e:
      self.logger.error(f"❌ An unexpected error occurred: {e}")
    return False
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
