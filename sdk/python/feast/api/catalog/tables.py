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

"""Iceberg REST table lifecycle (RHAI-389). OpenAPI v0.7.5.

Do not mount this router on RestRegistryServer here — RHAI-390 does that.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from feast.api.catalog.catalog_utils import (
    CATALOG_PROJECT,
    _require_namespace,
    _require_part,
    create_catalog_table,
    ensure_catalog_project,
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
from feast.api.catalog.mapping import (
    PROTECTED_TAGS,
    catalog_table_tags,
    default_location,
    iceberg_schema_to_columns,
    is_iceberg_table,
    saved_dataset_to_load_table,
)
from feast.api.catalog.models import (
    CreateTableRequest,
    ListTablesResponse,
    LoadTableResponse,
    RenameTableRequest,
    TableIdentifier,
    UpdateTableRequest,
)
from feast.errors import SavedDatasetNotFound
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.base_registry import BaseRegistry
from feast.saved_dataset import SavedDataset

_PLACEHOLDER_FEATURES = ["fv:feature"]
_PLACEHOLDER_JOIN_KEYS = ["entity_id"]


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
    if not is_iceberg_table(dataset):
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
            if not is_iceberg_table(dataset):
                continue
            identifiers.append(
                TableIdentifier(namespace=[col], name=unscoped_name(dataset.name))
            )
        return ListTablesResponse(identifiers=identifiers)

    @router.post(
        "/v1/{project}/namespaces/{collection}/tables",
        response_model=LoadTableResponse,
        response_model_by_alias=True,
    )
    def create_table(
        project: str,
        collection: str,
        body: CreateTableRequest,
        request: Request,
    ) -> LoadTableResponse:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        display = _display_name(body.name)
        name = scoped_name(rhai_ns, col, display)
        location = default_location(rhai_ns, col, display, body.location)
        dataset = SavedDataset(
            name=name,
            features=_PLACEHOLDER_FEATURES,
            join_keys=_PLACEHOLDER_JOIN_KEYS,
            storage=SavedDatasetFileStorage(path=location),
            namespace=rhai_ns,
            collection=col,
            description=body.properties.get("description", ""),
            columns=iceberg_schema_to_columns(body.schema_),
            tags=catalog_table_tags(body.properties),
        )
        create_catalog_table(registry, rhai_ns, col, dataset)
        stored = registry.get_saved_dataset(
            name, CATALOG_PROJECT, allow_cache=False
        )
        return saved_dataset_to_load_table(stored, rhai_ns, col)

    @router.get(
        "/v1/{project}/namespaces/{collection}/tables/{table}",
        response_model=LoadTableResponse,
        response_model_by_alias=True,
    )
    def load_table(
        project: str, collection: str, table: str, request: Request
    ) -> LoadTableResponse:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_iceberg_table(registry, rhai_ns, col, table)
        return saved_dataset_to_load_table(dataset, rhai_ns, col)

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

    @router.post(
        "/v1/{project}/namespaces/{collection}/tables/{table}",
        response_model=LoadTableResponse,
        response_model_by_alias=True,
    )
    def update_table(
        project: str,
        collection: str,
        table: str,
        body: UpdateTableRequest,
        request: Request,
    ) -> LoadTableResponse:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_iceberg_table(registry, rhai_ns, col, table)
        tags = dict(dataset.tags or {})
        for update in body.updates:
            if update.action == "set-properties":
                tags.update(update.updates)
                tags["format"] = "iceberg"
                tags["asset_type"] = "table"
                tags["_catalog_managed"] = "true"
            elif update.action == "remove-properties":
                for key in update.removals:
                    if key in PROTECTED_TAGS:
                        continue
                    tags.pop(key, None)
            else:
                raise NotImplementedException(
                    f"Unsupported update action: {update.action}"
                )
        dataset.tags = tags
        ensure_catalog_project(registry)
        registry.apply_saved_dataset(dataset, CATALOG_PROJECT)
        stored = registry.get_saved_dataset(
            dataset.name, CATALOG_PROJECT, allow_cache=False
        )
        return saved_dataset_to_load_table(stored, rhai_ns, col)

    @router.delete(
        "/v1/{project}/namespaces/{collection}/tables/{table}",
        status_code=204,
    )
    def drop_table(
        project: str, collection: str, table: str, request: Request
    ) -> Response:
        rhai_ns, col = _project_and_collection(project, collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_iceberg_table(registry, rhai_ns, col, table)
        registry.delete_saved_dataset(dataset.name, CATALOG_PROJECT)
        return Response(status_code=204)

    @router.post("/v1/{project}/tables/rename", status_code=204)
    def rename_table(
        project: str, body: RenameTableRequest, request: Request
    ) -> Response:
        rhai_ns = _rhai_ns(project)
        try:
            src_col = resolve_namespace(body.source.namespace)
            dst_col = resolve_namespace(body.destination.namespace)
            src_name = _require_part("name", body.source.name)
            dst_name = _require_part("name", body.destination.name)
        except ValueError as exc:
            raise _as_bad_request(exc) from exc
        registry = _registry(request)
        _require_collection(registry, rhai_ns, src_col)
        _require_collection(registry, rhai_ns, dst_col)
        if src_col == dst_col and src_name == dst_name:
            _get_iceberg_table(registry, rhai_ns, src_col, src_name)
            return Response(status_code=204)
        dataset = _get_iceberg_table(registry, rhai_ns, src_col, src_name)
        dest_scoped = scoped_name(rhai_ns, dst_col, dst_name)
        moved = SavedDataset(
            name=dest_scoped,
            features=list(dataset.features),
            join_keys=list(dataset.join_keys),
            storage=dataset.storage,
            namespace=rhai_ns,
            collection=dst_col,
            description=dataset.description,
            columns=list(dataset.columns),
            tags=catalog_table_tags(
                {
                    key: value
                    for key, value in (dataset.tags or {}).items()
                    if key not in PROTECTED_TAGS
                }
            ),
        )
        create_catalog_table(registry, rhai_ns, dst_col, moved)
        registry.delete_saved_dataset(dataset.name, CATALOG_PROJECT)
        return Response(status_code=204)

    return router
