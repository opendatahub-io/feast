"""Per-request tracing for the Feast MCP server.

Wraps each incoming HTTP request in an OpenTelemetry span. Two things fall
out of that single span:

1. **Log correlation.** While the span is active, the OTEL logging handler
   automatically stamps ``trace_id`` / ``span_id`` on every exported log
   record, and :class:`TraceContextFilter` mirrors the same ids onto the
   console logs. So *all* log lines produced while handling one request —
   including the per-request auth line in :mod:`feast.mcp.auth` — share one
   id and can be grouped in your backend.
2. **Traces.** If an OTLP endpoint is configured, the spans themselves are
   exported too, so the request shows up as a trace you can click into.

OpenTelemetry is an optional dependency. When the SDK is not installed the
middleware falls back to a generated per-request id (set on
:data:`request_id_var`), so console logs are still correlated — you just
don't get a real trace id or exported spans.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, MutableMapping, Optional

from feast.mcp.observability.config import LoggingConfig
from feast.mcp.observability.logger import (
    ROOT_LOGGER_NAME,
    _parse_headers,
    request_id_var,
)

#: Kept so spans can be flushed/shut down cleanly on exit.
_tracer_provider = None


def _build_span_exporter(config: LoggingConfig, root: logging.Logger):
    """Build the OTLP span exporter, or None if it is unavailable.

    Mirrors the log exporter selection in ``logger._build_otel_handler`` so
    traces and logs travel over the same protocol/endpoint/headers.
    """
    proto = config.otel_protocol
    headers = _parse_headers(config.otel_headers)
    try:
        if proto in ("http", "http/protobuf", "httpprotobuf"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=config.otel_endpoint, headers=headers)

        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[assignment]
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(  # type: ignore[call-arg]
            endpoint=config.otel_endpoint,
            insecure=config.otel_insecure,
            headers=headers,
        )
    except ImportError:
        root.warning(
            "OTEL span exporter for protocol %r is not installed; spans will "
            "not be exported (request logs still get a trace id). Install with: "
            "pip install 'feast-mcp[otel]'",
            proto,
        )
        return None


def configure_tracing(config: LoggingConfig):
    """Set up a real ``TracerProvider`` and return a tracer (or ``None``).

    A real provider is what gives spans *valid* trace/span ids — the global
    default is a no-op tracer whose ids are all zero, which the logging
    handler would ignore. Returns ``None`` when OTEL is disabled or the SDK
    is missing; the request middleware then falls back to a generated id.
    """
    global _tracer_provider
    if not config.otel_enabled:
        return None

    root = logging.getLogger(ROOT_LOGGER_NAME)
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        root.warning(
            "OTEL tracing requested but the OpenTelemetry SDK is not installed; "
            "request logs will use a generated id instead of a real trace id. "
            "Install with: pip install 'feast-mcp[otel]'"
        )
        return None

    resource = Resource.create({"service.name": config.otel_service_name})
    provider = TracerProvider(resource=resource)

    exporter = _build_span_exporter(config, root)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    root.info(
        "OTEL tracing enabled -> %s (%s); request logs are trace-correlated",
        config.otel_endpoint,
        config.otel_protocol,
    )
    return trace.get_tracer("feast.mcp")


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider, if one was created."""
    global _tracer_provider
    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
        finally:
            _tracer_provider = None


class RequestTracingMiddleware:
    """Pure-ASGI middleware: one span (or request id) per HTTP request.

    Starting the span *here* — outside the MCP handler — means it is the
    active span for the entire request, so every log line emitted downstream
    (tool dispatch, ``get_auth_token``, upstream calls) is correlated to it.

    When ``tracer`` is ``None`` (OTEL unavailable) it still sets a generated
    id on :data:`request_id_var` so console logs remain groupable.
    """

    def __init__(self, app: Any, tracer: Optional[Any] = None) -> None:
        self.app = app
        self.tracer = tracer

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        name = f"{method} {path}".strip() or "http.request"

        if self.tracer is not None:
            with self.tracer.start_as_current_span(name) as span:
                ctx = span.get_span_context()
                token = request_id_var.set(format(ctx.trace_id, "032x"))
                try:
                    await self.app(scope, receive, send)
                finally:
                    request_id_var.reset(token)
        else:
            token = request_id_var.set(uuid.uuid4().hex)
            try:
                await self.app(scope, receive, send)
            finally:
                request_id_var.reset(token)
