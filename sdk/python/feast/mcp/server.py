"""Feast MCP server built on FastMCP.

Composes two sub-servers behind a single MCP endpoint:

- **features** — proxies to the Feast feature server (online features,
  vector search, push, materialization).  Mounted when ``--feast-url``
  or ``features.url`` in config is provided.
- **registry** — proxies to the Feast REST registry server (browse
  feature views, entities, data sources, lineage).  Mounted when
  ``--registry-url`` or ``registry.url`` in config is provided.

Configuration is loaded from (highest priority first):
  1. CLI arguments
  2. Environment variables
  3. ``feast_mcp.yaml`` config file (or ``--config path``)
"""

import logging
from typing import Optional

import click
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from feast.mcp.auth import create_oidc_auth
from feast.mcp.client import FeastClient
from feast.mcp.config import Config, load_config
from feast.mcp.observability import (
    RequestTracingMiddleware,
    configure_logging,
    configure_tracing,
    load_logging_config,
    shutdown_logging,
    shutdown_tracing,
)

logger = logging.getLogger(__name__)

#: Tracer used to open a span per HTTP request (None when OTEL is off/absent).
_tracer = None

mcp = FastMCP(
    "feast",
    instructions=(
        "Feast Feature Store — retrieve online features, search documents, "
        "manage materialization, and browse the feature registry."
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "mcp-server"})


def _mount_servers(cfg: Config) -> None:
    if cfg.features.url:
        from feast.mcp.features import create_features_mcp

        client = FeastClient(cfg.features.url, timeout=cfg.timeout)
        mcp.mount(create_features_mcp(client), namespace="features")
        logger.info("Feature tools mounted from %s", cfg.features.url)

    if cfg.registry.url:
        from feast.mcp.registry import create_registry_mcp

        client = FeastClient(cfg.registry.url, timeout=cfg.timeout)
        mcp.mount(create_registry_mcp(client), namespace="registry")
        logger.info("Registry tools mounted from %s", cfg.registry.url)


def _build_session_store(cfg: Config):
    """Build the shared OAuth-state store, or None to use FastMCP's default."""
    if not cfg.session_storage.backend:
        return None

    from feast.mcp.session_storage import (
        SessionStorageConfigFactory,
        build_store,
    )

    storage_cfg = SessionStorageConfigFactory.create(
        cfg.session_storage.backend, cfg.session_storage.options
    )
    if not storage_cfg.shared:
        logger.warning(
            "Session storage backend %r is not shared across processes; "
            "the OAuth flow may break behind a load balancer with >1 replica.",
            storage_cfg.backend,
        )
    logger.info("OAuth client_storage backend: %s", storage_cfg.backend)
    return build_store(storage_cfg)


def _configure_auth(cfg: Config) -> None:
    if cfg.auth.mode != "oidc":
        return

    base_url = cfg.auth.base_url or f"http://localhost:{cfg.server.port}"
    mcp.auth = create_oidc_auth(
        discovery_url=cfg.auth.discovery_url,  # type: ignore[arg-type]
        client_id=cfg.auth.client_id,  # type: ignore[arg-type]
        client_secret=cfg.auth.client_secret,
        base_url=base_url,
        audience=cfg.auth.audience,
        client_storage=_build_session_store(cfg),
    )


def _build_http_app(cfg: Config):
    if cfg.server.transport == "sse":
        kwargs = {"path": "/sse", "transport": "sse"}
    else:
        kwargs = {"path": "/mcp", "transport": cfg.server.transport}
    app = mcp.http_app(**kwargs)  # type: ignore[arg-type]
    # Outermost layer so the request span is active for the whole request and
    # every downstream log line is correlated to one trace id.
    return RequestTracingMiddleware(app, tracer=_tracer)


def _run_uvicorn(cfg: Config) -> None:
    import uvicorn

    app = _build_http_app(cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)


def _run_gunicorn(cfg: Config) -> None:
    from gunicorn.app.base import BaseApplication

    asgi_app = _build_http_app(cfg)

    class FeastMCPApplication(BaseApplication):
        def load_config(self) -> None:
            self.cfg.set("bind", f"{cfg.server.host}:{cfg.server.port}")
            self.cfg.set("workers", cfg.server.workers)
            self.cfg.set("worker_class", "uvicorn.workers.UvicornWorker")
            self.cfg.set("accesslog", "-")

        def load(self):
            return asgi_app

    FeastMCPApplication().run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_server(
    config_path: Optional[str] = None,
    cli_args: Optional[dict] = None,
) -> None:
    """Configure and run the MCP server.

    Deliberately free of any CLI framework so the same body backs both the
    standalone ``feast-mcp`` script and Feast's ``feast mcp`` subcommand.

    ``cli_args`` holds only the options that were *explicitly supplied*, keyed
    by click destination name. Options the user did not pass must be absent
    rather than ``None``: :func:`load_config` treats any present key as an
    override, so a stray default here would silently outrank ``feast_mcp.yaml``.
    """
    cli_args = cli_args or {}

    # Configure logging first so all subsequent setup is visible.
    log_cfg = load_logging_config(config_path=config_path, cli_args=cli_args)
    configure_logging(log_cfg)
    # Set up tracing so each request gets a span (and trace-correlated logs).
    global _tracer
    _tracer = configure_tracing(log_cfg)

    cfg = load_config(config_path=config_path, cli_args=cli_args)

    # --- Validate ---
    if not cfg.features.url and not cfg.registry.url:
        raise click.UsageError(
            "At least one of --feast-url or --registry-url must be provided "
            "(via CLI, env var, or feast_mcp.yaml)"
        )

    if cfg.auth.mode == "oidc" and not all(
        [cfg.auth.discovery_url, cfg.auth.client_id]
    ):
        raise click.UsageError(
            "--oidc-discovery-url and --oidc-client-id are required with --auth-mode oidc"
        )

    if cfg.server.transport == "sse" and cfg.server.workers and cfg.server.workers > 1:
        raise click.UsageError(
            "SSE transport does not support multiple workers. "
            "Use --transport http for multi-worker scaling, "
            "or remove --workers for single-process SSE"
        )

    # --- Setup ---
    _mount_servers(cfg)
    _configure_auth(cfg)

    # --- Run ---
    try:
        if cfg.server.transport == "stdio":
            mcp.run(transport="stdio")
        elif cfg.server.workers:
            _run_gunicorn(cfg)
        else:
            _run_uvicorn(cfg)
    finally:
        shutdown_tracing()
        shutdown_logging()


# Every option below defaults to ``None`` on purpose. Real defaults live in
# ``feast.mcp.config`` / ``feast.mcp.observability.config``; declaring them
# here would put the value into ``cli_args`` unconditionally and stop
# ``feast_mcp.yaml`` and the environment from ever taking effect. Defaults are
# therefore documented in the help text instead of via ``show_default``.
@click.command("mcp")
@click.option(
    "--config",
    default=None,
    help="Path to feast_mcp.yaml config file.",
)
@click.option(
    "--feast-url",
    default=None,
    help="URL of the Feast feature server to proxy (mounts the feature tools).",
)
@click.option(
    "--registry-url",
    default=None,
    help="URL of the Feast REST registry server to proxy (mounts the registry tools).",
)
@click.option(
    "--timeout",
    type=float,
    default=None,
    help="HTTP timeout in seconds for upstream calls.  [default: 30]",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http", "streamable-http", "sse"]),
    default=None,
    help="MCP transport to serve.  [default: stdio]",
)
@click.option(
    "--host",
    default=None,
    help="Bind address for HTTP transports.  [default: 0.0.0.0]",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Bind port for HTTP transports.  [default: 8000]",
)
@click.option(
    "--workers",
    type=int,
    default=None,
    help="Run under gunicorn with this many workers (not supported by sse).",
)
# --- authentication ---
@click.option(
    "--auth-mode",
    type=click.Choice(["passthrough", "oidc"]),
    default=None,
    help="Auth mode.  [default: passthrough]",
)
@click.option("--oidc-discovery-url", default=None, help="OIDC discovery document URL.")
@click.option("--oidc-client-id", default=None, help="OIDC client id.")
@click.option("--oidc-client-secret", default=None, help="OIDC client secret.")
@click.option("--oidc-audience", default=None, help="Expected OIDC token audience.")
@click.option(
    "--base-url",
    default=None,
    help="Public base URL of this server, used to build OAuth redirect URIs.",
)
@click.option(
    "--session-storage-backend",
    default=None,
    help=(
        "Shared backend for OAuth state (redis, valkey, postgresql, mongodb, "
        "disk, memory). Backend options come from feast_mcp.yaml. Required for "
        "OIDC auth behind a load balancer with >1 replica."
    ),
)
# --- observability ---
@click.option("--log-level", default=None, help="Log level.  [default: INFO]")
@click.option(
    "--log-format",
    type=click.Choice(["text", "json"]),
    default=None,
    help="Console log format.  [default: text]",
)
@click.option(
    "--otel-endpoint",
    default=None,
    help=(
        "OTLP endpoint for log and span export (enables OTEL when set), "
        "e.g. http://localhost:4317"
    ),
)
@click.option(
    "--otel-protocol",
    type=click.Choice(["grpc", "http"]),
    default=None,
    help="OTLP protocol.  [default: grpc]",
)
@click.option(
    "--otel-service-name",
    default=None,
    help="service.name reported to OTEL.  [default: feast-mcp]",
)
def mcp_cli(config: Optional[str], **options: object) -> None:
    """Run the Feast MCP server.

    Serves the Model Context Protocol in its own process, proxying to a
    running Feast feature server and/or REST registry server. At least one of
    --feast-url or --registry-url is required.

    This is separate from `feast serve` with `mcp_enabled: true`, which mounts
    an OpenAPI-derived MCP endpoint inside the feature server itself.

    Settings resolve in priority order: CLI options, environment variables,
    feast_mcp.yaml, defaults.
    """
    load_dotenv()
    run_server(
        config_path=config,
        cli_args={key: value for key, value in options.items() if value is not None},
    )


def main() -> None:
    """Console-script entry point for ``feast-mcp``."""
    mcp_cli()


if __name__ == "__main__":
    main()
