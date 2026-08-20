"""Concrete session-storage backend configs.

One frozen dataclass per supported ``key_value.aio`` store. Each renders its
fields into that store constructor's required kwargs via
:meth:`~feast.mcp.session_storage.base.SessionStorageConfig.to_store_kwargs`.

Field names and defaults mirror the store constructors in
``key_value.aio.stores.*`` (py-key-value-aio 0.4.x) so the rendered kwargs
map straight onto them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from feast.mcp.session_storage.base import SessionStorageConfig


def _prune(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is ``None`` so store defaults apply."""
    return {k: v for k, v in kwargs.items() if v is not None}


# ---------------------------------------------------------------------------
# In-memory (default) — NOT load-balancer safe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemorySessionStorageConfig(SessionStorageConfig):
    """In-process store. The default, and the reason sessions don't survive
    behind a load balancer with more than one replica."""

    backend: ClassVar[str] = "memory"
    store_module: ClassVar[str] = "key_value.aio.stores.memory"
    store_class: ClassVar[str] = "MemoryStore"
    requires_extra: ClassVar[Optional[str]] = None
    shared: ClassVar[bool] = False

    max_entries_per_collection: Optional[int] = None
    default_collection: Optional[str] = None

    def to_store_kwargs(self) -> dict[str, Any]:
        return _prune(
            {
                "max_entries_per_collection": self.max_entries_per_collection,
                "default_collection": self.default_collection,
            }
        )


# ---------------------------------------------------------------------------
# Redis — shared, load-balancer safe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedisSessionStorageConfig(SessionStorageConfig):
    """Redis-backed store. Provide either ``url`` or ``host``/``port``/``db``."""

    backend: ClassVar[str] = "redis"
    store_module: ClassVar[str] = "key_value.aio.stores.redis"
    store_class: ClassVar[str] = "RedisStore"
    requires_extra: ClassVar[Optional[str]] = "redis"
    shared: ClassVar[bool] = True

    url: Optional[str] = None
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    ssl: bool = False
    default_collection: Optional[str] = None

    def to_store_kwargs(self) -> dict[str, Any]:
        base: dict[str, Any]
        if self.url:
            base = {"url": self.url}
        else:
            base = {
                "host": self.host,
                "port": self.port,
                "db": self.db,
                "password": self.password,
                "ssl": self.ssl,
            }
        base["default_collection"] = self.default_collection
        return _prune(base)


# ---------------------------------------------------------------------------
# Valkey — shared, load-balancer safe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValkeySessionStorageConfig(SessionStorageConfig):
    """Valkey-backed store (Redis-compatible, via the glide client)."""

    backend: ClassVar[str] = "valkey"
    store_module: ClassVar[str] = "key_value.aio.stores.valkey"
    store_class: ClassVar[str] = "ValkeyStore"
    requires_extra: ClassVar[Optional[str]] = "valkey"
    shared: ClassVar[bool] = True

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    username: Optional[str] = None
    password: Optional[str] = None
    default_collection: Optional[str] = None

    def to_store_kwargs(self) -> dict[str, Any]:
        return _prune(
            {
                "host": self.host,
                "port": self.port,
                "db": self.db,
                "username": self.username,
                "password": self.password,
                "default_collection": self.default_collection,
            }
        )


# ---------------------------------------------------------------------------
# Disk — node-local persistence, NOT load-balancer safe across nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiskSessionStorageConfig(SessionStorageConfig):
    """On-disk store (diskcache). Persists across restarts on a single node,
    but is not shared across replicas."""

    backend: ClassVar[str] = "disk"
    store_module: ClassVar[str] = "key_value.aio.stores.disk"
    store_class: ClassVar[str] = "DiskStore"
    requires_extra: ClassVar[Optional[str]] = "disk"
    shared: ClassVar[bool] = False

    directory: Optional[str] = None
    max_size: Optional[int] = None
    default_collection: Optional[str] = None
    auto_create: bool = True

    def to_store_kwargs(self) -> dict[str, Any]:
        return _prune(
            {
                "directory": self.directory,
                "max_size": self.max_size,
                "default_collection": self.default_collection,
                "auto_create": self.auto_create,
            }
        )


# ---------------------------------------------------------------------------
# MongoDB — shared, load-balancer safe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MongoDBSessionStorageConfig(SessionStorageConfig):
    """MongoDB-backed store. Provide a connection ``url``."""

    backend: ClassVar[str] = "mongodb"
    store_module: ClassVar[str] = "key_value.aio.stores.mongodb"
    store_class: ClassVar[str] = "MongoDBStore"
    requires_extra: ClassVar[Optional[str]] = "mongodb"
    shared: ClassVar[bool] = True

    url: Optional[str] = None
    db_name: Optional[str] = None
    coll_name: Optional[str] = None
    default_collection: Optional[str] = None
    auto_create: bool = True

    def to_store_kwargs(self) -> dict[str, Any]:
        return _prune(
            {
                "url": self.url,
                "db_name": self.db_name,
                "coll_name": self.coll_name,
                "default_collection": self.default_collection,
                "auto_create": self.auto_create,
            }
        )


# ---------------------------------------------------------------------------
# PostgreSQL — shared, load-balancer safe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostgreSQLSessionStorageConfig(SessionStorageConfig):
    """PostgreSQL-backed store. Provide either ``url`` or the discrete
    ``host``/``database``/``user`` fields."""

    backend: ClassVar[str] = "postgresql"
    store_module: ClassVar[str] = "key_value.aio.stores.postgresql"
    store_class: ClassVar[str] = "PostgreSQLStore"
    requires_extra: ClassVar[Optional[str]] = "postgresql"
    shared: ClassVar[bool] = True

    url: Optional[str] = None
    host: str = "localhost"
    port: int = 5432
    database: str = "postgres"
    user: Optional[str] = None
    password: Optional[str] = None
    table_name: Optional[str] = None
    default_collection: Optional[str] = None
    auto_create: bool = True

    def to_store_kwargs(self) -> dict[str, Any]:
        if self.url:
            base: dict[str, Any] = {"url": self.url}
        else:
            base = {
                "host": self.host,
                "port": self.port,
                "database": self.database,
                "user": self.user,
                "password": self.password,
            }
        base["table_name"] = self.table_name
        base["default_collection"] = self.default_collection
        base["auto_create"] = self.auto_create
        return _prune(base)


#: All backend config classes, keyed by their ``backend`` identifier.
BACKENDS: dict[str, type[SessionStorageConfig]] = {
    cfg.backend: cfg
    for cfg in (
        MemorySessionStorageConfig,
        RedisSessionStorageConfig,
        ValkeySessionStorageConfig,
        DiskSessionStorageConfig,
        MongoDBSessionStorageConfig,
        PostgreSQLSessionStorageConfig,
    )
}
