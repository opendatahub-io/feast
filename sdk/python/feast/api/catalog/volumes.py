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

"""RHOAI volume catalog routes (RHAI-376).

Register/list/update/delete SavedDataset rows with asset_type=volume.
Does not create or delete object-storage files.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from feast.api.catalog.catalog_assets import (
    delete_catalog_dataset,
    get_catalog_dataset,
    insert_catalog_dataset,
    isoformat_ts,
    labels_from_tags,
    labels_to_tag,
    list_catalog_datasets,
    merge_public_properties,
    notes_from_properties,
    public_properties,
    replace_catalog_dataset,
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
    NoSuchVolumeException,
    ServiceFailureException,
)
from feast.api.catalog.models import (
    CreateVolumeRequest,
    ListVolumesResponse,
    UpdateVolumeRequest,
    VolumeInfo,
)
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.base_registry import BaseRegistry
from feast.saved_dataset import SavedDataset

_DEFAULT_VOLUME_TYPE = "EXTERNAL"


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


def _display_name(volume: str) -> str:
    try:
        return _require_part("name", volume)
    except ValueError as exc:
        raise _as_bad_request(exc) from exc


def _require_collection(
    registry: BaseRegistry, rhai_ns: str, collection: str
) -> None:
    if not validate_namespace_exists(registry, rhai_ns, collection):
        raise NoSuchNamespaceException(f"Namespace does not exist: {collection}")


def _volume_info(dataset: SavedDataset, rhai_ns: str, collection: str) -> VolumeInfo:
    tags = dataset.tags or {}
    return VolumeInfo(
        name=unscoped_name(dataset.name),
        catalog_name=rhai_ns,
        schema_name=collection,
        volume_type=tags.get("volume_type") or _DEFAULT_VOLUME_TYPE,
        storage_location=storage_uri(dataset),
        comment=dataset.description or tags.get("comment") or None,
        owner=tags.get("owner") or None,
        created_at=isoformat_ts(dataset.created_timestamp),
        updated_at=isoformat_ts(dataset.last_updated_timestamp),
        labels=labels_from_tags(tags),
        properties=public_properties(tags),
        config={},
    )


def _get_volume(
    registry: BaseRegistry, rhai_ns: str, collection: str, volume: str
) -> SavedDataset:
    dataset = get_catalog_dataset(registry, rhai_ns, collection, volume)
    if dataset is None or (dataset.tags or {}).get("asset_type") != "volume":
        raise NoSuchVolumeException(f"Volume does not exist: {collection}.{volume}")
    return dataset


def get_volume_router() -> APIRouter:
    router = APIRouter(tags=["volumes"])

    @router.get(
        "/v1/{project}/namespaces/{collection}/volumes",
        response_model=ListVolumesResponse,
        response_model_by_alias=True,
    )
    def list_volumes(
        project: str, collection: str, request: Request
    ) -> ListVolumesResponse:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        volumes = [
            _volume_info(dataset, rhai_ns, col)
            for dataset in list_catalog_datasets(
                registry, rhai_ns, col, asset_type="volume"
            )
        ]
        return ListVolumesResponse(volumes=volumes)

    @router.post(
        "/v1/{project}/namespaces/{collection}/volumes",
        response_model=VolumeInfo,
        response_model_by_alias=True,
    )
    def create_volume(
        project: str, collection: str, body: CreateVolumeRequest, request: Request
    ) -> VolumeInfo:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        display = _display_name(body.name)
        location = (body.storage_location or body.location or "").strip()
        volume_type = (
            body.volume_type or body.content_type or _DEFAULT_VOLUME_TYPE
        ).strip() or _DEFAULT_VOLUME_TYPE
        tags = {
            **notes_from_properties(body.properties),
            **labels_to_tag(body.labels),
            "asset_type": "volume",
            "volume_type": volume_type,
        }
        if body.owner:
            tags["owner"] = body.owner
        comment = body.comment or body.description or ""
        dataset = insert_catalog_dataset(
            registry,
            rhai_ns=rhai_ns,
            collection=col,
            display_name=display,
            location=location,
            tags=tags,
            description=comment,
        )
        return _volume_info(dataset, rhai_ns, col)

    @router.get(
        "/v1/{project}/namespaces/{collection}/volumes/{volume}",
        response_model=VolumeInfo,
        response_model_by_alias=True,
    )
    def get_volume(
        project: str, collection: str, volume: str, request: Request
    ) -> VolumeInfo:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_volume(registry, rhai_ns, col, _display_name(volume))
        return _volume_info(dataset, rhai_ns, col)

    @router.head(
        "/v1/{project}/namespaces/{collection}/volumes/{volume}",
        status_code=204,
    )
    def volume_exists(
        project: str, collection: str, volume: str, request: Request
    ) -> Response:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        _get_volume(registry, rhai_ns, col, _display_name(volume))
        return Response(status_code=204)

    @router.put(
        "/v1/{project}/namespaces/{collection}/volumes/{volume}",
        response_model=VolumeInfo,
        response_model_by_alias=True,
    )
    def update_volume(
        project: str,
        collection: str,
        volume: str,
        body: UpdateVolumeRequest,
        request: Request,
    ) -> VolumeInfo:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        dataset = _get_volume(registry, rhai_ns, col, _display_name(volume))
        tags = dict(dataset.tags or {})
        if body.owner is not None:
            tags["owner"] = body.owner
        if body.properties is not None:
            tags = merge_public_properties(tags, body.properties)
            tags["asset_type"] = "volume"
            tags.setdefault("volume_type", _DEFAULT_VOLUME_TYPE)
        if body.comment is not None:
            dataset.description = body.comment
        if body.storage_location is not None:
            dataset.storage = SavedDatasetFileStorage(path=body.storage_location)
        dataset.tags = tags
        updated = replace_catalog_dataset(registry, dataset)
        return _volume_info(updated, rhai_ns, col)

    @router.delete(
        "/v1/{project}/namespaces/{collection}/volumes/{volume}",
        status_code=204,
    )
    def delete_volume(
        project: str, collection: str, volume: str, request: Request
    ) -> Response:
        rhai_ns = _rhai_ns(project)
        col = _collection_name(collection)
        registry = _registry(request)
        _require_collection(registry, rhai_ns, col)
        display = _display_name(volume)
        _get_volume(registry, rhai_ns, col, display)
        delete_catalog_dataset(registry, rhai_ns, col, display)
        return Response(status_code=204)

    return router
