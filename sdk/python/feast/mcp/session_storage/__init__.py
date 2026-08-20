"""Session-storage configuration for the Feast MCP server.

FastMCP keeps session state (and SSE resumability events) in an
``AsyncKeyValue`` store that defaults to in-memory — which pins every
session to one process and breaks a standard load balancer. This package
provides a factory that turns a chosen backend + options into a validated
config in the exact format the matching ``key_value.aio`` store requires.

    from feast.mcp.session_storage import SessionStorageConfigFactory

    cfg = SessionStorageConfigFactory.create("redis", {"url": "redis://cache:6379"})
    cfg.to_store_kwargs()   # -> {"url": "redis://cache:6379"}
"""

from feast.mcp.session_storage.backends import (
    BACKENDS,
    DiskSessionStorageConfig,
    MemorySessionStorageConfig,
    MongoDBSessionStorageConfig,
    PostgreSQLSessionStorageConfig,
    RedisSessionStorageConfig,
    ValkeySessionStorageConfig,
)
from feast.mcp.session_storage.base import SessionStorageConfig
from feast.mcp.session_storage.builder import build_store
from feast.mcp.session_storage.factory import SessionStorageConfigFactory

__all__ = [
    "SessionStorageConfig",
    "SessionStorageConfigFactory",
    "build_store",
    "BACKENDS",
    "MemorySessionStorageConfig",
    "RedisSessionStorageConfig",
    "ValkeySessionStorageConfig",
    "DiskSessionStorageConfig",
    "MongoDBSessionStorageConfig",
    "PostgreSQLSessionStorageConfig",
]
