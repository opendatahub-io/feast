"""Authorization helpers for the Feast MCP server.

The MCP server does not enforce its own RBAC — it forwards the caller's
bearer token to the upstream Feast feature server, which validates the
token (OIDC / Kubernetes SA token) and applies its own permission model.

Two modes are supported:

1. **No auth** (default, or ``--auth-mode passthrough``):
   No authentication required — connections are accepted without tokens.
   Useful for development or when the client already holds a valid token.

2. **OIDC login** (``--auth-mode oidc``):
   An OIDCProxy discovers endpoints from the OIDC discovery URL
   (the same ``auth_discovery_url`` used in Feast's feature_store.yaml)
   and presents a browser login flow so that IDE clients (Cursor, VS Code,
   etc.) can sign the user in.  After login the upstream access token is
   stored server-side and forwarded to Feast on every tool call.

   Programmatic clients (MCP SDK, demo scripts) can also send OIDC
   provider tokens directly as Bearer tokens — the server validates them
   against the provider's JWKS as a fallback.
"""

from typing import Optional

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import (
    get_access_token,
    get_http_request,
)
from key_value.aio.protocols import AsyncKeyValue

from feast.mcp.observability import get_logger

logger = get_logger(__name__)


class FeastOIDCProxy(OIDCProxy):
    """OIDCProxy that also accepts direct OIDC provider tokens.

    IDE clients go through the full OAuth flow and get MCP-issued JWTs.
    Programmatic clients (MCP SDK, scripts) can send OIDC provider tokens
    directly — they are validated against the provider's JWKS as a fallback.
    """

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Step 1: Try MCP-issued JWT validation (IDE OAuth flow tokens)
        result = await super().load_access_token(token)
        if result is not None:
            return result  # type: ignore[return-value]

        # Step 2: Try direct OIDC provider token validation (SDK clients)
        # _token_validator is a JWTVerifier configured with the OIDC
        # provider's JWKS URI (e.g. http://keycloak:8081/jwks)
        try:
            logger.debug("MCP JWT validation failed, trying direct OIDC provider token")
            validated = await self._token_validator.verify_token(token)
            if validated is not None:
                logger.debug(
                    "Direct OIDC provider token accepted for sub=%s",
                    validated.claims.get("sub"),
                )
            return validated
        except Exception as e:
            logger.debug("Direct OIDC provider token validation also failed: %s", e)
            return None


def create_oidc_auth(
    *,
    discovery_url: str,
    client_id: str,
    client_secret: Optional[str] = None,
    base_url: str,
    audience: Optional[str] = None,
    client_storage: Optional[AsyncKeyValue] = None,
) -> FeastOIDCProxy:
    """Build the OIDC proxy.

    Args:
        client_storage: Shared ``AsyncKeyValue`` backend for OAuth state
            (client registrations, transactions, codes, token mappings).
            Pass a distributed store (Redis, etc.) to keep the OAuth flow
            working across replicas behind a load balancer. When ``None``,
            FastMCP falls back to its default on-disk, per-node store.
    """
    return FeastOIDCProxy(
        config_url=discovery_url,
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        audience=audience,
        client_storage=client_storage,
    )


def _request_context() -> tuple[Optional[str], Optional[str]]:
    """Best-effort ``(client_ip, "METHOD /path")`` of the current request.

    The IP honors ``X-Forwarded-For`` / ``X-Real-IP`` first (the caller is
    usually behind a load balancer or reverse proxy), then the direct socket
    peer. Both are ``None`` outside of an HTTP request (e.g. stdio transport).
    """
    try:
        request = get_http_request()
    except Exception:
        return None, None
    if request is None:
        return None, None

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First hop is the original client.
        ip: Optional[str] = forwarded.split(",")[0].strip()
    elif request.headers.get("x-real-ip"):
        ip = request.headers["x-real-ip"].strip()
    else:
        client = getattr(request, "client", None)
        ip = getattr(client, "host", None) if client else None

    method = getattr(request, "method", None)
    path = getattr(getattr(request, "url", None), "path", None)
    where = f"{method} {path}" if method and path else None
    return ip, where


def _describe_user(access_token: AccessToken) -> str:
    """Human-readable identity of the authenticated caller for logs."""
    claims = getattr(access_token, "claims", None) or {}
    user = (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("sub")
        or getattr(access_token, "subject", None)
        or "unknown"
    )
    client_id = getattr(access_token, "client_id", None)
    return f"{user} (client_id={client_id})" if client_id else str(user)


def _raw_bearer_token() -> Optional[str]:
    """Bearer token read straight off the request, bypassing FastMCP's auth.

    In ``passthrough`` mode no auth provider is configured, so FastMCP never
    parses ``Authorization`` and :func:`get_access_token` stays ``None`` --
    even when the caller is holding exactly the token Feast needs. Reading
    the header directly is what makes "the client already holds a valid
    token" actually work.
    """
    try:
        request = get_http_request()
    except Exception:
        return None
    if request is None:
        return None

    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def get_auth_token() -> Optional[str]:
    """Return the caller's bearer token, logging who is calling from where.

    Called on every tool invocation, so this is the natural choke point to
    record per-request auth context: the authenticated user, their source
    IP, and which request (method + path) they made.
    """
    access_token: Optional[AccessToken] = get_access_token()
    ip, where = _request_context()

    if access_token is None:
        # Either no auth provider is configured (passthrough) or the provider
        # permits anonymous access. When a provider IS set, FastMCP rejects
        # missing/invalid tokens before the tool body runs, so this fallback
        # cannot launder an unvalidated token past validation -- and Feast
        # validates whatever is forwarded either way.
        raw_token = _raw_bearer_token()
        logger.info(
            "Unauthenticated request: ip=%s request=%s forwarded_caller_token=%s",
            ip or "unknown",
            where or "n/a",
            "yes" if raw_token else "no",
        )
        return raw_token

    logger.info(
        "Authenticated request: user=%s ip=%s request=%s",
        _describe_user(access_token),
        ip or "unknown",
        where or "n/a",
    )
    return access_token.token
