# Copyright 2026 The Feast Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Iceberg REST config endpoints (RHAI-387).

``GET /v1/config`` is the engine bootstrap call. ``warehouse`` (or the
prefixed path) becomes ``overrides.prefix`` so later calls use
``/v1/{prefix}/...``. ``endpoints`` lists catalog operations that are
implemented as **read**. Table create/update/drop/rename and LoadTable
exist as routes but return 501 and are not advertised: no credential
vending (DCH); engines talk to object storage themselves. Namespace CRUD
is advertised. Mounting on RestRegistryServer is RHAI-390.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from feast.api.catalog.catalog_utils import _require_namespace
from feast.api.catalog.errors import BadRequestException
from feast.api.catalog.models import DataRegistryConfig

# Iceberg clients match ``{prefix}`` in these strings, not OpenAPI ``{project}``.
CATALOG_CONFIG_ENDPOINTS = [
    "GET /v1/config",
    "GET /v1/{prefix}/config",
    "GET /v1/{prefix}/namespaces",
    "POST /v1/{prefix}/namespaces",
    "GET /v1/{prefix}/namespaces/{namespace}",
    "HEAD /v1/{prefix}/namespaces/{namespace}",
    "DELETE /v1/{prefix}/namespaces/{namespace}",
    "POST /v1/{prefix}/namespaces/{namespace}/properties",
    "GET /v1/{prefix}/namespaces/{namespace}/tables",
    "HEAD /v1/{prefix}/namespaces/{namespace}/tables/{table}",
]


def _as_bad_request(exc: ValueError) -> BadRequestException:
    return BadRequestException(str(exc))


def _config_prefix(raw: str | None) -> str | None:
    """Validate Iceberg ``{prefix}`` (K8s / RHOAI namespace). Empty stays unset."""
    if not raw:
        return None
    try:
        return _require_namespace(raw)
    except ValueError as exc:
        raise _as_bad_request(exc) from exc


def catalog_config(*, prefix: str | None = None) -> DataRegistryConfig:
    overrides: dict[str, str] = {}
    if prefix:
        overrides["prefix"] = prefix
    return DataRegistryConfig(
        defaults={},
        overrides=overrides,
        endpoints=list(CATALOG_CONFIG_ENDPOINTS),
    )


def get_config_router() -> APIRouter:
    router = APIRouter(tags=["discovery"])

    @router.get("/v1/config", response_model=DataRegistryConfig)
    def get_bootstrap_config(
        warehouse: str | None = Query(default=None),
    ) -> DataRegistryConfig:
        return catalog_config(prefix=_config_prefix(warehouse))

    @router.get("/v1/{project}/config", response_model=DataRegistryConfig)
    def get_project_config(project: str) -> DataRegistryConfig:
        return catalog_config(prefix=_config_prefix(project))

    return router
