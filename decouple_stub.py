# Simple stub for python-decouple for testing purposes
import os

def config(name, default=None, cast=None):
    """Simple config function that reads from environment or returns default"""
    value = os.environ.get(name, default)
    if cast and value is not None:
        if cast == bool:
            if isinstance(value, bool):
                return value
            return str(value).lower() in ('true', '1', 'yes', 'on')
        elif cast == list or hasattr(cast, '__call__'):
            # Handle lambda functions like the one in settings
            if hasattr(cast, '__call__') and not cast == list:
                return cast(value)
            return [item.strip() for item in str(value).split(',')]
        else:
            return cast(value)
    return value