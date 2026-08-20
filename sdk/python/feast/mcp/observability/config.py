"""Separate configuration for Feast MCP observability (logging + OTEL).

Kept independent of the main server ``Config`` so logging can be set up
*first* — before anything else is wired — and so OTEL settings live in one
place. Resolves from (highest priority first):

  1. CLI arguments
  2. Environment variables (``FEAST_MCP_*``, with standard ``OTEL_*`` as a
     fallback so existing OpenTelemetry tooling keeps working)
  3. ``observability:`` section of ``feast_mcp.yaml``
  4. Defaults

Example ``feast_mcp.yaml``::

    observability:
      level: INFO
      format: json            # text | json
      stdio: true
      otel_enabled: true
      otel_endpoint: http://localhost:4317
      otel_protocol: grpc     # grpc | http
      otel_service_name: feast-mcp
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class LoggingConfig:
    """Resolved logging / OTEL settings."""

    level: str = "INFO"
    format: str = "text"  # text | json
    stdio: bool = True
    otel_enabled: bool = False
    otel_endpoint: Optional[str] = None
    otel_protocol: str = "grpc"  # grpc | http
    otel_service_name: str = "feast-mcp"
    otel_headers: Optional[str] = None
    otel_insecure: bool = True


def _env(*keys: str) -> Optional[str]:
    """First non-empty value among ``keys`` from the environment."""
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


def _as_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml_section(config_path: Optional[str], section: str) -> dict:
    candidates = [config_path] if config_path else ["feast_mcp.yaml", "feast_mcp.yml"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                import yaml
            except ImportError:
                return {}
            with open(candidate) as f:
                data = yaml.safe_load(f) or {}
            sub = data.get(section)
            return sub if isinstance(sub, dict) else {}
    return {}


def load_logging_config(
    config_path: Optional[str] = None,
    cli_args: Optional[dict] = None,
) -> LoggingConfig:
    """Load logging config from CLI args, env vars, and YAML."""
    cli = cli_args or {}
    y = _load_yaml_section(config_path, "observability")

    level = (
        cli.get("log_level") or _env("FEAST_MCP_LOG_LEVEL") or y.get("level") or "INFO"
    )
    fmt = (
        cli.get("log_format")
        or _env("FEAST_MCP_LOG_FORMAT")
        or y.get("format")
        or "text"
    )

    stdio = _as_bool(_env("FEAST_MCP_LOG_STDIO"))
    if stdio is None:
        stdio = _as_bool(y.get("stdio"))
    stdio = True if stdio is None else stdio

    endpoint = (
        cli.get("otel_endpoint")
        or _env("FEAST_MCP_OTEL_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")
        or y.get("otel_endpoint")
    )

    enabled_raw: Any = _env("FEAST_MCP_OTEL_ENABLED")
    if enabled_raw is None and "otel_enabled" in y:
        enabled_raw = y.get("otel_enabled")
    enabled = _as_bool(enabled_raw)
    if enabled is None:
        # Enable automatically once an endpoint is configured.
        enabled = bool(endpoint)

    protocol = (
        cli.get("otel_protocol")
        or _env("FEAST_MCP_OTEL_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL")
        or y.get("otel_protocol")
        or "grpc"
    )
    service = (
        cli.get("otel_service_name")
        or _env("FEAST_MCP_OTEL_SERVICE_NAME", "OTEL_SERVICE_NAME")
        or y.get("otel_service_name")
        or "feast-mcp"
    )
    headers = _env("FEAST_MCP_OTEL_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS") or y.get(
        "otel_headers"
    )

    insecure = _as_bool(_env("FEAST_MCP_OTEL_INSECURE"))
    if insecure is None:
        insecure = _as_bool(y.get("otel_insecure"))
    insecure = True if insecure is None else insecure

    return LoggingConfig(
        level=str(level).upper(),
        format=str(fmt).lower(),
        stdio=stdio,
        otel_enabled=enabled,
        otel_endpoint=endpoint,
        otel_protocol=str(protocol).lower(),
        otel_service_name=service,
        otel_headers=headers,
        otel_insecure=insecure,
    )
