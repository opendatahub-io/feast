# Copyright 2026 The Feast Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Iceberg REST table routes (RHAI-389).

List and HEAD are catalog **read** over Feast SavedDataset rows.
Create, load, update, drop, and rename are 501 stubs: no Iceberg
warehouse, no credential vending.

Do not mount this router on RestRegistryServer here — RHAI-390 does that.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from feast.api.catalog.catalog_utils import (
    CATALOG_MANAGED_TAG,
    CATALOG_MANAGED_VALUE,
    CATALOG_PROJECT,
    _require_namespace,
    _require_part,
    resolve_namespace,
    scoped_name,
    unscoped_name,
    validate_namespace_exists,
)
from feast.api.catalog.errors import (
    BadRequestException,
    NoSuchNamespaceException,
    NoSuchTableException,
    NotImplementedException,
    ServiceFailureException,
)
from feast.api.catalog.models import ListTablesResponse, TableIdentifier
from feast.errors import SavedDatasetNotFound
from feast.infra.registry.base_registry import BaseRegistry
from feast.saved_dataset import SavedDataset

_TABLE_UNIMPLEMENTED = (
    "This Iceberg table operation is not implemented. "
    "This catalog supports list and exists only; engines must use "
    "their own object-store credentials"
)


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


def _display_name(table: str) -> str:
    try:
        return _require_part("name", table)
    except ValueError as exc:
        raise _as_bad_request(exc) from exc


def _require_collection(
    registry: BaseRegistry, rhai_ns: str, collection: str
) -> None:
    if not validate_namespace_exists(registry, rhai_ns, collection):
        raise NoSuchNamespaceException(f"Namespace does not exist: {collection}")


def _is_iceberg_table(dataset: SavedDataset) -> bool:
    tags = dataset.tags or {}
    return (
        tags.get(CATALOG_MANAGED_TAG) == CATALOG_MANAGED_VALUE
        and tags.get("format") == "iceberg"
        and tags.get("asset_type") == "table"
    )


def _get_iceberg_table(
    registry: BaseRegistry, rhai_ns: str, collection: str, table: str
) -> SavedDataset:
    display = _display_name(table)
    name = scoped_name(rhai_ns, collection, display)
    try:
        dataset = registry.get_saved_dataset(
            name, CATALOG_PROJECT, allow_cache=False
        )
    except SavedDatasetNotFound as exc:
        raise NoSuchTableException(
            f"Table does not exist: {collection}.{display}"
        ) from exc
    if dataset.namespace != rhai_ns or dataset.collection != collection:
        raise NoSuchTableException(f"Table does not exist: {collection}.{display}")
    if not _is_iceberg_table(dataset):
        raise NoSuchTableException(f"Table does not exist: {collection}.{display}")
    return dataset


def get_table_router() -> APIRouter:
    router = APIRouter(tags=["tables"])

    @router.get(
        "/v1/{project}/namespaces/{collection}/tables",
        response_model=ListTablesResponse,
        response_model_by_alias=True,
    )
    def list_tables(
        project: str, collection: str, request: Request
    ) -> ListTablesResponse:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        identifiers: list[TableIdentifier] = []
        for dataset in registry.list_saved_datasets(
            CATALOG_PROJECT, namespace=rhai_ns, collection=col
        ):
            if not _is_iceberg_table(dataset):
                continue
            try:
                display = unscoped_name(dataset.name)
            except ValueError:
                continue
            identifiers.append(TableIdentifier(namespace=[col], name=display))
        return ListTablesResponse(identifiers=identifiers)

    @router.post("/v1/{project}/namespaces/{collection}/tables")
    def create_table(project: str, collection: str) -> Response:
        _project_and_collection(project, collection)
        raise NotImplementedException(_TABLE_UNIMPLEMENTED)

    @router.get("/v1/{project}/namespaces/{collection}/tables/{table}")
    def load_table(project: str, collection: str, table: str) -> Response:
        _project_and_collection(project, collection)
        _display_name(table)
        raise NotImplementedException(_TABLE_UNIMPLEMENTED)

    @router.head(
        "/v1/{project}/namespaces/{collection}/tables/{table}",
        status_code=204,
    )
    def table_exists(
        project: str, collection: str, table: str, request: Request
    ) -> Response:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        _get_iceberg_table(registry, rhai_ns, col, table)
        return Response(status_code=204)

    @router.post("/v1/{project}/namespaces/{collection}/tables/{table}")
    def update_table(project: str, collection: str, table: str) -> Response:
        _project_and_collection(project, collection)
        _display_name(table)
        raise NotImplementedException(_TABLE_UNIMPLEMENTED)

    @router.delete("/v1/{project}/namespaces/{collection}/tables/{table}")
    def drop_table(project: str, collection: str, table: str) -> Response:
        _project_and_collection(project, collection)
        _display_name(table)
        raise NotImplementedException(_TABLE_UNIMPLEMENTED)

    @router.post("/v1/{project}/tables/rename")
    def rename_table(project: str) -> Response:
        _rhai_ns(project)
        raise NotImplementedException(_TABLE_UNIMPLEMENTED)

    return router
