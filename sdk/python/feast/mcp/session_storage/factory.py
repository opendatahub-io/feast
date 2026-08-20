"""The session-storage config factory.

Single responsibility: given a chosen backend name and a bag of raw options
(from YAML / env / CLI, so values may be strings), return a validated
:class:`SessionStorageConfig` whose ``to_store_kwargs()`` is in the exact
format the backend's ``key_value.aio`` store constructor requires.

    >>> from feast.mcp.session_storage import SessionStorageConfigFactory
    >>> cfg = SessionStorageConfigFactory.create("redis", {"url": "redis://cache:6379"})
    >>> cfg.to_store_kwargs()
    {'url': 'redis://cache:6379'}
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, Mapping, Optional

from feast.mcp.session_storage.backends import BACKENDS
from feast.mcp.session_storage.base import SessionStorageConfig


def _unwrap_optional(tp: Any) -> Any:
    """Return ``T`` for ``Optional[T]`` / ``T | None``; otherwise ``tp``."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, getattr(types, "UnionType", ())):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _coerce(value: Any, tp: Any) -> Any:
    """Coerce a raw option value to the field's declared scalar type.

    Options often arrive as strings (env vars, YAML). Only bool/int/float
    are coerced; everything else is passed through untouched.
    """
    if value is None:
        return None
    tp = _unwrap_optional(tp)
    if tp is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if tp is int and not isinstance(value, bool):
        return int(value)
    if tp is float:
        return float(value)
    return value


class SessionStorageConfigFactory:
    """Builds :class:`SessionStorageConfig` products for a chosen backend."""

    _registry: dict[str, type[SessionStorageConfig]] = dict(BACKENDS)

    @classmethod
    def supported(cls) -> list[str]:
        """Return the sorted list of registered backend identifiers."""
        return sorted(cls._registry)

    @classmethod
    def register(cls, config_cls: type[SessionStorageConfig]) -> None:
        """Register a custom backend config class (keyed by its ``backend``)."""
        cls._registry[config_cls.backend] = config_cls

    @classmethod
    def create(
        cls,
        backend: str,
        options: Optional[Mapping[str, Any]] = None,
    ) -> SessionStorageConfig:
        """Create a validated config for ``backend`` from raw ``options``.

        Args:
            backend: Backend identifier (see :meth:`supported`).
            options: Raw backend options; unknown keys raise ``ValueError``.

        Raises:
            ValueError: Unknown backend, or an option not valid for it.
        """
        try:
            config_cls = cls._registry[backend]
        except KeyError:
            raise ValueError(
                f"Unknown session storage backend {backend!r}. "
                f"Supported: {cls.supported()}"
            ) from None

        provided = dict(options or {})
        fields = {f.name: f for f in dataclasses.fields(config_cls)}  # type: ignore[arg-type]
        unknown = set(provided) - set(fields)
        if unknown:
            raise ValueError(
                f"Unknown option(s) for backend {backend!r}: {sorted(unknown)}. "
                f"Valid options: {sorted(fields)}"
            )

        hints = typing.get_type_hints(config_cls)
        kwargs = {
            name: _coerce(value, hints.get(name)) for name, value in provided.items()
        }
        return config_cls(**kwargs)
