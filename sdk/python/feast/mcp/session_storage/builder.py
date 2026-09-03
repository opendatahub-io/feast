"""Turn a :class:`SessionStorageConfig` into a live ``AsyncKeyValue`` store.

The factory produces *config*; this builder is the one place that imports the
backend's ``key_value.aio`` store (paying for its optional dependency) and
instantiates it with the config's required kwargs.
"""

from __future__ import annotations

import importlib

from key_value.aio.protocols import AsyncKeyValue

from feast.mcp.session_storage.base import SessionStorageConfig


def build_store(config: SessionStorageConfig) -> AsyncKeyValue:
    """Instantiate the ``key_value.aio`` store described by ``config``.

    Raises:
        ImportError: The backend's optional dependency is not installed.
    """
    try:
        module = importlib.import_module(config.store_module)
    except ImportError as exc:
        extra = config.requires_extra
        hint = (
            f" Install the optional 'py-key-value-aio[{extra}]' extra." if extra else ""
        )
        raise ImportError(
            f"Session storage backend {config.backend!r} requires an optional "
            f"dependency that is not installed.{hint}"
        ) from exc

    store_cls = getattr(module, config.store_class)
    return store_cls(**config.to_store_kwargs())
