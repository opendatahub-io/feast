"""Observability for the Feast MCP server: logging fanned out to stdout and OTEL.

from feast.mcp.observability import configure_logging, load_logging_config, get_logger

configure_logging(load_logging_config())
log = get_logger(__name__)
log.info("hello")   # -> stdout and, if configured, the OTLP endpoint
"""

from feast.mcp.observability.config import LoggingConfig, load_logging_config
from feast.mcp.observability.logger import (
    ROOT_LOGGER_NAME,
    JsonFormatter,
    configure_logging,
    get_logger,
    request_id_var,
    shutdown_logging,
)
from feast.mcp.observability.tracing import (
    RequestTracingMiddleware,
    configure_tracing,
    shutdown_tracing,
)

__all__ = [
    "LoggingConfig",
    "load_logging_config",
    "configure_logging",
    "get_logger",
    "shutdown_logging",
    "JsonFormatter",
    "ROOT_LOGGER_NAME",
    "request_id_var",
    "configure_tracing",
    "shutdown_tracing",
    "RequestTracingMiddleware",
]
