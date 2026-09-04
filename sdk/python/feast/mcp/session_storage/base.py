"""Product interface for the session-storage config factory.

Each concrete :class:`SessionStorageConfig` describes one ``key_value.aio``
backend and knows how to render itself into the **exact keyword arguments**
that backend's store constructor requires — the "required format" that the
rest of the server (and, later, the store builder) can consume without
knowing anything backend-specific.

The classes here hold configuration only. They do *not* import the
underlying ``key_value.aio`` store or open any connection — that keeps the
factory dependency-free and lets a caller decide when (and whether) to pay
for a backend's optional dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class SessionStorageConfig(ABC):
    """Normalized, validated config for a single session-storage backend.

    Subclasses are frozen dataclasses. Class-level attributes describe the
    backend itself; :meth:`to_store_kwargs` renders instance fields into the
    format the matching ``key_value.aio`` store constructor expects.
    """

    #: Short backend identifier used in config/CLI, e.g. ``"redis"``.
    backend: ClassVar[str]

    #: Import path of the backing ``key_value.aio`` store module.
    store_module: ClassVar[str]

    #: Name of the store class inside :attr:`store_module`.
    store_class: ClassVar[str]

    #: Optional pip extra needed to import the store (``None`` if always
    #: available in the base install, as with the in-memory backend).
    requires_extra: ClassVar[str | None] = None

    #: Whether state is shared across processes. ``False`` backends (memory,
    #: node-local disk) are *not* safe behind a load balancer with more than
    #: one replica — the whole reason this factory exists.
    shared: ClassVar[bool]

    @abstractmethod
    def to_store_kwargs(self) -> dict[str, Any]:
        """Return kwargs in the format the store constructor requires.

        The result is passed verbatim as ``StoreClass(**kwargs)``.
        """

    def describe(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of the chosen configuration.

        Secrets are intentionally omitted; use :meth:`to_store_kwargs` when
        the actual connection parameters are needed.
        """
        return {
            "backend": self.backend,
            "store": f"{self.store_module}:{self.store_class}",
            "requires_extra": self.requires_extra,
            "shared": self.shared,
        }
