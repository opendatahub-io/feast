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

"""RHOAI generic-table catalog routes (RHAI-376).

Register tables of any format (including iceberg) as SavedDataset rows.
Iceberg REST POST /tables stays 501 (warehouse create). This route is
catalog metadata only.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request, Response

from feast.api.catalog.catalog_assets import (
    connection_ref_from_tags,
    connection_ref_to_tag,
    delete_catalog_dataset,
    get_catalog_dataset,
    insert_catalog_dataset,
    isoformat_ts,
    labels_from_tags,
    labels_to_tag,
    list_catalog_datasets,
    merge_labels,
    merge_public_properties,
    notes_from_properties,
    public_properties,
    replace_catalog_dataset,
    schema_fields_to_columns,
    storage_uri,
)
from feast.api.catalog.catalog_utils import (
    _require_namespace,
    _require_part,
    resolve_namespace,
    unscoped_name,
    validate_namespace_exists,
)
from feast.api.catalog.errors import (
    BadRequestException,
    NoSuchNamespaceException,
    NoSuchTableException,
    ServiceFailureException,
)
from feast.api.catalog.models import (
    AssetListResponse,
    AssetResponse,
    CreateGenericTableRequest,
    SchemaField,
    UpdateGenericTableRequest,
)
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.base_registry import BaseRegistry
from feast.saved_dataset import SavedDataset

_DEFAULT_TABLE_FORMAT = "iceberg"


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


def _table_format(fmt: str | None) -> str:
    stripped = (fmt or "").strip()
    return stripped or _DEFAULT_TABLE_FORMAT


def _asset_response(dataset: SavedDataset, collection: str) -> AssetResponse:
    tags = dataset.tags or {}
    columns = [
        SchemaField(
            name=column.name,
            type=column.type,
            description=column.description or "",
            nullable=column.nullable,
        )
        for column in (dataset.columns or [])
    ]
    return AssetResponse(
        name=unscoped_name(dataset.name),
        asset_type=tags.get("asset_type") or "table",
        uuid=tags.get("uuid"),
        format=tags.get("format"),
        location=storage_uri(dataset) or None,
        columns=columns or None,
        collection=collection,
        connection_ref=connection_ref_from_tags(tags),
        owner=tags.get("owner") or None,
        description=dataset.description or None,
        labels=labels_from_tags(tags),
        properties=public_properties(tags) or None,
        registered_by=tags.get("registered_by") or None,
        updated_by=tags.get("updated_by") or None,
        created_at=isoformat_ts(dataset.created_timestamp),
        updated_at=isoformat_ts(dataset.last_updated_timestamp),
    )


def _get_table(
    registry: BaseRegistry, rhai_ns: str, collection: str, table: str
) -> SavedDataset:
    dataset = get_catalog_dataset(registry, rhai_ns, collection, table)
    if dataset is None or (dataset.tags or {}).get("asset_type") != "table":
        raise NoSuchTableException(f"Table does not exist: {collection}.{table}")
    return dataset


def get_generic_table_router() -> APIRouter:
    router = APIRouter(tags=["generic-tables"])

    @router.get(
        "/v1/{project}/namespaces/{collection}/generic-tables",
        response_model=AssetListResponse,
    )
    def list_generic_tables(
        project: str,
        collection: str,
        request: Request,
        label: str | None = Query(default=None),
    ) -> AssetListResponse:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        assets = [
            _asset_response(dataset, col)
            for dataset in list_catalog_datasets(
                registry, rhai_ns, col, asset_type="table"
            )
        ]
        if label:
            assets = [
                asset for asset in assets if asset.labels and label in asset.labels
            ]
        return AssetListResponse(assets=assets)

    @router.post(
        "/v1/{project}/namespaces/{collection}/generic-tables",
        response_model=AssetResponse,
        status_code=201,
    )
    def create_generic_table(
        project: str,
        collection: str,
        body: CreateGenericTableRequest,
        request: Request,
        x_user: str | None = Header(default=None, alias="X-User"),
        kubeflow_userid: str | None = Header(default=None, alias="kubeflow-userid"),
    ) -> AssetResponse:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        display = _display_name(body.name)
        try:
            label_tags = labels_to_tag(body.labels)
            ref_tags = connection_ref_to_tag(body.connection_ref)
            columns = schema_fields_to_columns(body.schema_fields)
        except ValueError as exc:
            raise _as_bad_request(exc) from exc
        tags = {
            **notes_from_properties(body.properties),
            **label_tags,
            **ref_tags,
            "asset_type": "table",
            "format": _table_format(body.format),
        }
        for key in ("purpose", "license", "maturity", "domain", "pii", "owner"):
            value = getattr(body, key)
            if value:
                tags[key] = value
        registered_by = x_user or kubeflow_userid
        if registered_by:
            tags["registered_by"] = registered_by
        dataset = insert_catalog_dataset(
            registry,
            rhai_ns=rhai_ns,
            collection=col,
            display_name=display,
            location=(body.location or "").strip(),
            tags=tags,
            description=body.description or "",
            columns=columns,
        )
        return _asset_response(dataset, col)

    @router.get(
        "/v1/{project}/namespaces/{collection}/generic-tables/{table}",
        response_model=AssetResponse,
    )
    def get_generic_table(
        project: str, collection: str, table: str, request: Request
    ) -> AssetResponse:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_table(registry, rhai_ns, col, _display_name(table))
        return _asset_response(dataset, col)

    @router.patch(
        "/v1/{project}/namespaces/{collection}/generic-tables/{table}",
        response_model=AssetResponse,
    )
    def update_generic_table(
        project: str,
        collection: str,
        table: str,
        body: UpdateGenericTableRequest,
        request: Request,
        x_user: str | None = Header(default=None, alias="X-User"),
        kubeflow_userid: str | None = Header(default=None, alias="kubeflow-userid"),
    ) -> AssetResponse:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_table(registry, rhai_ns, col, _display_name(table))
        tags = dict(dataset.tags or {})
        if body.format is not None:
            tags["format"] = _table_format(body.format)
        if body.owner is not None:
            tags["owner"] = body.owner
        if "connection_ref" in body.model_fields_set:
            tags.pop("_connection_ref", None)
            try:
                tags.update(connection_ref_to_tag(body.connection_ref))
            except ValueError as exc:
                raise _as_bad_request(exc) from exc
        if body.add_labels or body.remove_labels:
            try:
                tags = merge_labels(
                    tags, add=body.add_labels, remove=body.remove_labels
                )
            except ValueError as exc:
                raise _as_bad_request(exc) from exc
        if body.properties is not None:
            tags = merge_public_properties(tags, body.properties)
            tags["asset_type"] = "table"
        for key in ("purpose", "license", "maturity", "domain", "pii"):
            value = getattr(body, key)
            if value is not None:
                tags[key] = value
        updated_by = x_user or kubeflow_userid
        if updated_by:
            tags["updated_by"] = updated_by
        if body.description is not None:
            dataset.description = body.description
        if body.location is not None:
            dataset.storage = SavedDatasetFileStorage(path=body.location)
        if body.schema_fields is not None:
            try:
                dataset.columns = schema_fields_to_columns(body.schema_fields)
            except ValueError as exc:
                raise _as_bad_request(exc) from exc
        dataset.tags = tags
        updated = replace_catalog_dataset(registry, dataset)
        return _asset_response(updated, col)

    @router.delete(
        "/v1/{project}/namespaces/{collection}/generic-tables/{table}",
        status_code=204,
    )
    def delete_generic_table(
        project: str, collection: str, table: str, request: Request
    ) -> Response:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        display = _display_name(table)
        _get_table(registry, rhai_ns, col, display)
        delete_catalog_dataset(registry, rhai_ns, col, display)
        return Response(status_code=204)

    return router
