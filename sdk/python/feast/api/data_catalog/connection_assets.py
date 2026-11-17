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

"""Look up catalog assets associated with a Data Connect Hub connection."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request

from feast.api.data_catalog.catalog_assets import (
    catalog_asset_response,
    connection_ref_from_tags,
)
from feast.api.data_catalog.catalog_utils import (
    DEFAULT_COLLECTION,
    _catalog_saved_datasets,
    _http_namespace,
    _registry,
)
from feast.api.data_catalog.models import (
    AssetListResponse,
    AssetResponse,
    DchConnectionRef,
)


def get_connection_assets_router() -> APIRouter:
    router = APIRouter(tags=["connections"])

    @router.get(
        "/v1/{project}/connections/{source}/{connection_id}/assets",
        response_model=AssetListResponse,
        response_model_exclude_unset=True,
        operation_id="listConnectionAssets",
    )
    def list_connection_assets(
        project: str,
        source: Literal["dch"],
        connection_id: UUID,
        request: Request,
    ) -> AssetListResponse:
        namespace = _http_namespace(project)
        registry = _registry(request)
        matching_assets: list[AssetResponse] = []

        for dataset in _catalog_saved_datasets(registry, namespace):
            connection_ref = connection_ref_from_tags(dataset.tags or {})
            if (
                isinstance(connection_ref, DchConnectionRef)
                and source == "dch"
                and connection_ref.id == connection_id
            ):
                collection = dataset.collection or DEFAULT_COLLECTION
                matching_assets.append(catalog_asset_response(dataset, collection))

        matching_assets.sort(
            key=lambda asset: (asset.collection, asset.name.casefold())
        )
        return AssetListResponse(assets=matching_assets)

    return router
