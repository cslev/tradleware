"""
Environment variable utility functions.

This module provides helper functions for reading environment variables
with support for inline comments and other common .env file patterns.
"""

import os


def get_env(key: str, default: str = None) -> str:
    """
    Get environment variable and strip any inline comments and quotes.
    
    Inline comments in .env files should have a space before the # character.
    This function also strips surrounding quotes (both single and double) and
    preserves # characters that are part of the actual value.
    
    Examples:
        MYVAR="value" # This is a comment  -> returns "value"
        MYVAR='value'                      -> returns "value"
        MYVAR=value                        -> returns "value"
        PASSWORD="p@ss#word" # comment     -> returns "p@ss#word"
    
    Args:
        key (str): The environment variable name to retrieve
        default (str, optional): Default value if the variable is not set. Defaults to None.
    
    Returns:
        str: The environment variable value with inline comments and quotes stripped, or default if not set
    """
    value = os.getenv(key, default)
    if value is None:
        return None
    
    # Split on ' #' to handle inline comments (with space before #)
    # This preserves # characters that are part of the actual value
    if ' #' in value:
        value = value.split(' #')[0]
    
    # Strip whitespace
    value = value.strip()
    
    # Strip surrounding quotes (both single and double)
    # Only strip if the value starts and ends with the same quote character
    if len(value) >= 2:
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
    
    return value
