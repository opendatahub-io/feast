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

"""Move a catalog asset between collections.

Tables and volumes share one SavedDataset primary key, so one route moves
either kind. Iceberg ``POST /v1/{project}/tables/rename`` stays unimplemented.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from feast.api.data_catalog.catalog_assets import catalog_asset_response
from feast.api.data_catalog.catalog_utils import (
    _http_collection,
    _http_namespace,
    _http_part,
    _registry,
    move_catalog_asset,
)
from feast.api.data_catalog.models import AssetResponse, MoveAssetRequest


def get_asset_router() -> APIRouter:
    router = APIRouter(tags=["assets"])

    @router.post(
        "/v1/{project}/namespaces/{collection}/assets/{asset}/move",
        response_model=AssetResponse,
        response_model_exclude_none=True,
    )
    def move_asset(
        project: str,
        collection: str,
        asset: str,
        body: MoveAssetRequest,
        request: Request,
    ) -> AssetResponse:
        rhai_ns = _http_namespace(project)
        source = _http_collection(collection)
        display_name = _http_part("name", asset)
        destination = _http_collection(body.destination_namespace)
        dataset = move_catalog_asset(
            _registry(request),
            rhai_ns,
            source,
            display_name,
            destination,
        )
        return catalog_asset_response(dataset, destination)

    return router
