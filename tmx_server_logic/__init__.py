# This file makes Python treat the 'tmx_server_logic' directory as a package.

# You can optionally expose certain classes or functions at the package level
# for easier importing by users of this package.
# For example:
# from .models import TMXHeader, TranslationUnit
# from .tmx_parser import TMXParser
# from .storage import SQLiteStore

# For now, we'll keep it simple and let users import directly from the modules.
# This also helps avoid circular import issues if modules depend on each other
# in complex ways during initialization.

MAJOR_VERSION = 0
MINOR_VERSION = 1
PATCH_VERSION = 0
__version__ = f"{MAJOR_VERSION}.{MINOR_VERSION}.{PATCH_VERSION}"

# It's good practice to define what `from tmx_server_logic import *` does,
# though explicit imports are generally preferred.
# __all__ = ['TMXParser', 'SQLiteStore', 'TMXHeader', 'TranslationUnit'] # Example
# For now, leave __all__ undefined or empty if not exposing anything directly.
__all__ = []
