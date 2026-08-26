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

"""Iceberg REST namespace (collection) lifecycle (RHAI-388).

Empty collections persist as scoped Project tags ``_ns_meta_{ns}/{collection}``
on Feast project ``data-registry``. Do not mount this router on
RestRegistryServer here (RHAI-390).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from feast.api.catalog.catalog_utils import (
    DEFAULT_COLLECTION,
    collection_has_assets,
    delete_namespace_meta,
    ensure_catalog_project,
    get_namespace_properties,
    list_namespaces,
    resolve_namespace,
    set_namespace_properties,
    validate_namespace_exists,
    _require_namespace,
)
from feast.api.catalog.errors import (
    BadRequestException,
    NamespaceAlreadyExistsException,
    NamespaceNotEmptyException,
    NoSuchNamespaceException,
    ServiceFailureException,
)
from feast.api.catalog.models import (
    CreateNamespaceRequest,
    ListNamespacesResponse,
    NamespaceResponse,
    UpdateNamespacePropertiesRequest,
    UpdateNamespacePropertiesResponse,
)
from feast.infra.registry.base_registry import BaseRegistry


def _registry(request: Request) -> BaseRegistry:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise ServiceFailureException("catalog registry is not configured")
    return registry


def _as_bad_request(exc: ValueError) -> BadRequestException:
    return BadRequestException(str(exc))


def _rhai_ns(project: str) -> str:
    try:
        return _require_namespace(project)
    except ValueError as exc:
        raise _as_bad_request(exc) from exc


def _collection_name(collection: str) -> str:
    try:
        return resolve_namespace(collection)
    except ValueError as exc:
        raise _as_bad_request(exc) from exc


def _project_and_collection(project: str, collection: str) -> tuple[str, str]:
    return _rhai_ns(project), _collection_name(collection)


def get_namespace_router() -> APIRouter:
    router = APIRouter(tags=["namespaces"])

    @router.get("/v1/{project}/namespaces", response_model=ListNamespacesResponse)
    def list_namespaces_http(project: str, request: Request) -> ListNamespacesResponse:
        rhai_ns = _rhai_ns(project)
        names = list_namespaces(_registry(request), rhai_ns)
        return ListNamespacesResponse(namespaces=[[name] for name in names])

    @router.post("/v1/{project}/namespaces", response_model=NamespaceResponse)
    def create_namespace(
        project: str, body: CreateNamespaceRequest, request: Request
    ) -> NamespaceResponse:
        rhai_ns = _rhai_ns(project)
        try:
            collection = resolve_namespace(body.namespace)
        except ValueError as exc:
            raise _as_bad_request(exc) from exc
        registry = _registry(request)
        ensure_catalog_project(registry)
        if validate_namespace_exists(registry, rhai_ns, collection):
            raise NamespaceAlreadyExistsException(
                f"Namespace already exists: {collection}"
            )
        properties = dict(body.properties)
        set_namespace_properties(registry, rhai_ns, collection, properties)
        return NamespaceResponse(namespace=[collection], properties=properties)

    @router.get(
        "/v1/{project}/namespaces/{collection}",
        response_model=NamespaceResponse,
    )
    def get_namespace(
        project: str, collection: str, request: Request
    ) -> NamespaceResponse:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        if not validate_namespace_exists(registry, rhai_ns, col):
            raise NoSuchNamespaceException(f"Namespace does not exist: {col}")
        return NamespaceResponse(
            namespace=[col],
            properties=get_namespace_properties(registry, rhai_ns, col),
        )

    @router.head("/v1/{project}/namespaces/{collection}", status_code=204)
    def namespace_exists(project: str, collection: str, request: Request) -> Response:
        rhai_ns, col = _project_and_collection(project, collection)
        if not validate_namespace_exists(_registry(request), rhai_ns, col):
            raise NoSuchNamespaceException(f"Namespace does not exist: {col}")
        return Response(status_code=204)

    @router.delete("/v1/{project}/namespaces/{collection}", status_code=204)
    def drop_namespace(project: str, collection: str, request: Request) -> Response:
        rhai_ns, col = _project_and_collection(project, collection)
        if col == DEFAULT_COLLECTION:
            raise BadRequestException("Cannot drop the default collection")
        registry = _registry(request)
        if not validate_namespace_exists(registry, rhai_ns, col):
            raise NoSuchNamespaceException(f"Namespace does not exist: {col}")
        if collection_has_assets(registry, rhai_ns, col):
            raise NamespaceNotEmptyException(f"Namespace not empty: {col}")
        delete_namespace_meta(registry, rhai_ns, col)
        return Response(status_code=204)

    @router.post(
        "/v1/{project}/namespaces/{collection}/properties",
        response_model=UpdateNamespacePropertiesResponse,
    )
    def update_namespace_properties(
        project: str,
        collection: str,
        body: UpdateNamespacePropertiesRequest,
        request: Request,
    ) -> UpdateNamespacePropertiesResponse:
        rhai_ns, col = _project_and_collection(project, collection)
        overlap = sorted(set(body.updates) & set(body.removals))
        if overlap:
            raise BadRequestException(
                "Cannot update and remove the same property: " + ", ".join(overlap)
            )
        registry = _registry(request)
        if not validate_namespace_exists(registry, rhai_ns, col):
            raise NoSuchNamespaceException(f"Namespace does not exist: {col}")
        current = get_namespace_properties(registry, rhai_ns, col)
        updated = sorted(body.updates)
        removed: list[str] = []
        missing: list[str] = []
        for key, value in body.updates.items():
            current[key] = value
        for key in body.removals:
            if key in current:
                current.pop(key)
                removed.append(key)
            else:
                missing.append(key)
        set_namespace_properties(registry, rhai_ns, col, current)
        return UpdateNamespacePropertiesResponse(
            updated=updated,
            removed=removed,
            missing=missing,
        )

    return router
