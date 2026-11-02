"""Import every provider package so each one registers its protocol."""

import importlib
import pkgutil

_LOADED = False


def load_providers() -> None:
    """Import provider subpackages once. Importing a package runs its ``register`` call."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for module in pkgutil.iter_modules(__path__):
        if module.name == "base" or module.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{module.name}")


load_providers()
