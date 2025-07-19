import logging
import sys
import os
from pathlib import Path
import colorama
from colorama import Fore, Style

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
  def __init__(self, name, logfile_name:str="bolehtrade.log"):
    self.logger = logging.getLogger(name)
    self.logger.setLevel(logging.DEBUG)

    # Create console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    # Create a formatter with the current log level colors
    formatter = ColoredFormatter('%(asctime)s - [%(name)s] - %(levelname)s - %(message)s', 
                                 datefmt='%Y-%m-%d %H:%M:%S')
    ch.setFormatter(formatter)


    # File handler
    # --- Determine logs directory one level above this file ---
    base_dir = Path(__file__).resolve().parent.parent
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / logfile_name
    fh = logging.FileHandler(logfile, mode='a') #rewrite the logfile always
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s - [%(name)s] - %(levelname)s - %(message)s', 
                                      datefmt='%Y-%m-%d %H:%M:%S'))

    # Add the handler to the logger
    self.logger.addHandler(ch)
    self.logger.addHandler(fh)

  def debug(self, message, exc_info=False): 
    self.logger.debug(message, exc_info=exc_info) 

  def info(self, message, exc_info=False): 
    self.logger.info(message, exc_info=exc_info) 

  def warning(self, message, exc_info=False): 
    self.logger.warning(message, exc_info=exc_info)

  def error(self, message, exc_info=False): 
    self.logger.error(message, exc_info=exc_info) 

  def critical(self, message, exc_info=False): 
    self.logger.critical(message, exc_info=exc_info)

  def success(self, message, exc_info=False):
    """
    Logs a message with the custom SUCCESS level.
    """
    self.logger.log(SUCCESS_LEVEL, message, exc_info=exc_info)


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