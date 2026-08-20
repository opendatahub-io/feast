"""Configuration for the Feast MCP server.

Loads settings from three sources (highest priority first):

1. CLI arguments (``--feast-url``, ``--transport``, etc.)
2. Environment variables (``FEAST_MCP_FEATURE_SERVER_URL``, etc.)
3. Config file (``feast_mcp.yaml`` or ``--config path``)

Example ``feast_mcp.yaml``::

    server:
      transport: http
      host: 0.0.0.0
      port: 8000
      workers: 4

    features:
      url: http://localhost:6566

    registry:
      url: http://localhost:8080

    auth:
      mode: oidc
      discovery_url: https://keycloak.example.com/realms/feast/.well-known/openid-configuration
      client_id: feast-mcp
      client_secret: null
      audience: null
      base_url: https://mcp.example.com:8000

    timeout: 30
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ServerConfig:
    transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 8000
    workers: Optional[int] = None


@dataclass(frozen=True)
class FeaturesConfig:
    url: Optional[str] = None


@dataclass(frozen=True)
class RegistryConfig:
    url: Optional[str] = None


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "passthrough"
    discovery_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    audience: Optional[str] = None
    base_url: Optional[str] = None


@dataclass(frozen=True)
class SessionStorageConfig:
    """Selects the shared ``key_value.aio`` backend for OAuth state.

    ``backend`` is a backend identifier (``redis``, ``valkey``, ``postgresql``,
    ``mongodb``, ``disk``, ``memory``); ``options`` are backend-specific
    settings passed through to the store constructor. When ``backend`` is
    ``None`` the OIDC proxy uses FastMCP's default on-disk, per-node store.
    """

    backend: Optional[str] = None
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    session_storage: SessionStorageConfig = field(default_factory=SessionStorageConfig)
    timeout: float = 30.0


def _load_yaml(path: str | Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise ImportError(
            f"PyYAML is required to load config file '{path}'. "
            "Install with: pip install pyyaml"
        )
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _env(key: str) -> Optional[str]:
    return os.environ.get(key) or None


def load_config(
    config_path: Optional[str] = None,
    cli_args: Optional[dict] = None,
) -> Config:
    """Load config from YAML file, env vars, and CLI args.

    Priority: CLI args > env vars > YAML file > defaults.
    """
    file_data: dict = {}
    if config_path:
        file_data = _load_yaml(config_path)
    else:
        for candidate in ("feast_mcp.yaml", "feast_mcp.yml"):
            if Path(candidate).is_file():
                file_data = _load_yaml(candidate)
                break

    cli = cli_args or {}

    def _resolve(
        cli_key: str, env_key: str, yaml_section: str, yaml_key: str, default=None
    ):
        return (
            cli.get(cli_key)
            or _env(env_key)
            or file_data.get(yaml_section, {}).get(yaml_key)
            if isinstance(file_data.get(yaml_section), dict)
            else cli.get(cli_key) or _env(env_key)
        ) or default

    def _resolve_flat(cli_key: str, env_key: str, yaml_key: str, default=None):
        return cli.get(cli_key) or _env(env_key) or file_data.get(yaml_key, default)

    srv = (
        file_data.get("server", {}) if isinstance(file_data.get("server"), dict) else {}
    )
    feat = (
        file_data.get("features", {})
        if isinstance(file_data.get("features"), dict)
        else {}
    )
    reg = (
        file_data.get("registry", {})
        if isinstance(file_data.get("registry"), dict)
        else {}
    )
    auth_yaml = (
        file_data.get("auth", {}) if isinstance(file_data.get("auth"), dict) else {}
    )
    storage_yaml = (
        file_data.get("session_storage", {})
        if isinstance(file_data.get("session_storage"), dict)
        else {}
    )
    storage_options = storage_yaml.get("options")
    workers_raw = cli.get("workers") or _env("FEAST_MCP_WORKERS") or srv.get("workers")
    workers = int(workers_raw) if workers_raw is not None else None

    return Config(
        server=ServerConfig(
            transport=cli.get("transport")
            or _env("FEAST_MCP_TRANSPORT")
            or srv.get("transport", "stdio"),
            host=cli.get("host") or srv.get("host", "0.0.0.0"),
            port=int(cli.get("port") or srv.get("port", 8000)),
            workers=workers,
        ),
        features=FeaturesConfig(
            url=cli.get("feast_url")
            or _env("FEAST_MCP_FEATURE_SERVER_URL")
            or feat.get("url"),
        ),
        registry=RegistryConfig(
            url=cli.get("registry_url")
            or _env("FEAST_MCP_REGISTRY_URL")
            or reg.get("url"),
        ),
        auth=AuthConfig(
            mode=cli.get("auth_mode")
            or _env("FEAST_MCP_AUTH_MODE")
            or auth_yaml.get("mode", "passthrough"),
            discovery_url=cli.get("oidc_discovery_url")
            or _env("FEAST_MCP_OIDC_DISCOVERY_URL")
            or auth_yaml.get("discovery_url"),
            client_id=cli.get("oidc_client_id")
            or _env("FEAST_MCP_OIDC_CLIENT_ID")
            or auth_yaml.get("client_id"),
            client_secret=cli.get("oidc_client_secret")
            or _env("FEAST_MCP_OIDC_CLIENT_SECRET")
            or auth_yaml.get("client_secret"),
            audience=cli.get("oidc_audience")
            or _env("FEAST_MCP_OIDC_AUDIENCE")
            or auth_yaml.get("audience"),
            base_url=cli.get("base_url")
            or _env("FEAST_MCP_BASE_URL")
            or auth_yaml.get("base_url"),
        ),
        session_storage=SessionStorageConfig(
            backend=cli.get("session_storage_backend")
            or _env("FEAST_MCP_SESSION_STORAGE_BACKEND")
            or storage_yaml.get("backend"),
            options=storage_options if isinstance(storage_options, dict) else {},
        ),
        timeout=float(
            cli.get("timeout")
            or _env("FEAST_MCP_TIMEOUT")
            or file_data.get("timeout", 30.0)
        ),
    )
