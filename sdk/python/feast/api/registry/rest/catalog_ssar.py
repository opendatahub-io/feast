"""
Server-side SubjectAccessReview (SSAR) filtering for data-registry catalog endpoints.

When the data registry is enabled (DATACATALOG_ENABLED=true), endpoints like
/v1/projects and /v1/search bypass kube-rbac-proxy auth (--ignore-paths) and
perform their own per-namespace authorization. This module provides the SSAR
check utilities consumed by those endpoints.

Environment variables (set by the Feast operator):
    CATALOG_SSAR_API_GROUP: API group for SAR checks (e.g. "dataregistry.opendatahub.io")
    CATALOG_SSAR_RESOURCES: Comma-separated resources (e.g. "namespaces,tables,volumes")
"""

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from fastapi import Request

logger = logging.getLogger(__name__)

_SSAR_API_GROUP = os.getenv("CATALOG_SSAR_API_GROUP", "")
_SSAR_RESOURCES = os.getenv("CATALOG_SSAR_RESOURCES", "namespaces").split(",")
_SSAR_CACHE_TTL = int(os.getenv("CATALOG_SSAR_CACHE_TTL_SECONDS", "30"))

_access_cache: Dict[Tuple[str, str], Tuple[bool, float]] = {}


def is_catalog_ssar_enabled() -> bool:
    return bool(_SSAR_API_GROUP)


def extract_bearer_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:]
    return None


def filter_projects_by_ssar(
    projects: List[Dict],
    bearer_token: str,
    resource: str = "namespaces",
    verb: str = "list",
) -> List[Dict]:
    """
    Filter a list of projects (namespaces) by performing SubjectAccessReview
    for each one. Returns only projects where the user is authorized.
    """
    if not is_catalog_ssar_enabled():
        return projects

    try:
        from kubernetes import client, config

        config.load_incluster_config()
        authz_api = client.AuthorizationV1Api()
    except Exception as e:
        logger.error(f"Failed to initialize K8s client for SSAR: {e}")
        return []

    permitted = []
    for project in projects:
        project_name = project.get("spec", {}).get("name", "")
        if not project_name:
            continue

        if _check_access_cached(authz_api, bearer_token, project_name, resource, verb):
            permitted.append(project)

    logger.debug(f"SSAR filter: {len(permitted)}/{len(projects)} projects permitted")
    return permitted


def _check_access_cached(
    authz_api,
    bearer_token: str,
    namespace: str,
    resource: str,
    verb: str,
) -> bool:
    cache_key = (_token_fingerprint(bearer_token), f"{namespace}/{resource}/{verb}")
    now = time.time()

    cached = _access_cache.get(cache_key)
    if cached and (now - cached[1]) < _SSAR_CACHE_TTL:
        return cached[0]

    result = _do_ssar_check(authz_api, bearer_token, namespace, resource, verb)
    _access_cache[cache_key] = (result, now)

    if len(_access_cache) > 10000:
        _evict_cache(now)

    return result


def _do_ssar_check(
    authz_api,
    bearer_token: str,
    namespace: str,
    resource: str,
    verb: str,
) -> bool:
    """Perform a SubjectAccessReview impersonating the caller via their token."""
    from kubernetes import client

    try:
        token_review = client.V1TokenReview(
            spec=client.V1TokenReviewSpec(token=bearer_token)
        )
        auth_api = client.AuthenticationV1Api()
        tr_response = auth_api.create_token_review(token_review)

        if not tr_response.status.authenticated:
            return False

        username = tr_response.status.user.username
        groups = tr_response.status.user.groups or []

        sar = client.V1SubjectAccessReview(
            spec=client.V1SubjectAccessReviewSpec(
                user=username,
                groups=groups,
                resource_attributes=client.V1ResourceAttributes(
                    namespace=namespace,
                    verb=verb,
                    group=_SSAR_API_GROUP,
                    resource=resource,
                ),
            )
        )

        response = authz_api.create_subject_access_review(sar)
        return response.status.allowed

    except Exception as e:
        logger.error(f"SSAR check failed for {namespace}/{resource}/{verb}: {e}")
        return False


def _token_fingerprint(token: str) -> str:
    """Short hash of token for cache keying (avoids storing full tokens in memory)."""
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()[:16]


_CACHE_MAX = 10000
_CACHE_TARGET = 7500  # trim to 75 % capacity after eviction


def _evict_cache(now: float) -> None:
    """Remove expired entries first; if the cache is still above _CACHE_TARGET,
    evict the oldest entries by insertion/refresh timestamp to enforce the cap."""
    expired = [
        k for k, (_, ts) in _access_cache.items() if (now - ts) >= _SSAR_CACHE_TTL
    ]
    for k in expired:
        del _access_cache[k]

    if len(_access_cache) > _CACHE_TARGET:
        overflow = len(_access_cache) - _CACHE_TARGET
        oldest = sorted(_access_cache, key=lambda k: _access_cache[k][1])[:overflow]
        for k in oldest:
            del _access_cache[k]
