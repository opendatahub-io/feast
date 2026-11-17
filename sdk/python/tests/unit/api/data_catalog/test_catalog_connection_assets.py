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

from __future__ import annotations

import tempfile
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from feast.api.data_catalog.config import get_config_router
from feast.api.data_catalog.connection_assets import get_connection_assets_router
from feast.api.data_catalog.errors import register_error_handlers
from feast.api.data_catalog.generic_tables import get_generic_table_router
from feast.api.data_catalog.namespaces import get_namespace_router
from feast.api.data_catalog.volumes import get_volume_router
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig

NS = "demo-user-1"
COL = "underwriting"
AUTH = {"X-User": "test-user"}


@pytest.fixture
def sqlite_registry() -> Iterator[SqlRegistry]:
    _fd, registry_path = tempfile.mkstemp()
    registry = SqlRegistry(
        SqlRegistryConfig(
            registry_type="sql",
            path=f"sqlite:///{registry_path}",
            purge_feast_metadata=False,
        ),
        "scratch",
        None,
    )
    yield registry
    registry.teardown()


def _client(registry: SqlRegistry) -> TestClient:
    app = FastAPI()
    app.state.registry = registry
    register_error_handlers(app)
    app.include_router(get_config_router())
    app.include_router(get_connection_assets_router())
    app.include_router(get_namespace_router())
    app.include_router(get_volume_router())
    app.include_router(get_generic_table_router())
    return TestClient(app, raise_server_exceptions=False)


def _ensure_collection(
    client: TestClient, project: str = NS, collection: str = COL
) -> None:
    client.post(
        f"/v1/{project}/namespaces",
        json={"namespace": [collection], "properties": {}},
    )


def test_connection_asset_lookup_spans_asset_types_and_collections(
    sqlite_registry: SqlRegistry,
) -> None:
    client = _client(sqlite_registry)
    connection_id = "550e8400-e29b-41d4-a716-446655440000"
    _ensure_collection(client)
    _ensure_collection(client, collection="archive")
    _ensure_collection(client, project="neighbor-user", collection="archive")

    table = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": "events",
            "format": "parquet",
            "connection_ref": {"type": "dch", "id": connection_id},
        },
        headers=AUTH,
    )
    volume = client.post(
        f"/v1/{NS}/namespaces/archive/volumes",
        json={
            "name": "documents",
            "format": "documents",
            "connection_ref": {"type": "dch", "id": connection_id},
        },
        headers=AUTH,
    )
    different_connection = client.post(
        f"/v1/{NS}/namespaces/archive/generic-tables",
        json={
            "name": "other-events",
            "format": "parquet",
            "connection_ref": {
                "type": "dch",
                "id": "550e8400-e29b-41d4-a716-446655440001",
            },
        },
        headers=AUTH,
    )
    neighbor_table = client.post(
        "/v1/neighbor-user/namespaces/archive/generic-tables",
        json={
            "name": "neighbor-events",
            "format": "parquet",
            "connection_ref": {"type": "dch", "id": connection_id},
        },
        headers=AUTH,
    )
    assert table.status_code == 201, table.text
    assert volume.status_code == 200, volume.text
    assert different_connection.status_code == 201, different_connection.text
    assert neighbor_table.status_code == 201, neighbor_table.text

    response = client.get(f"/v1/{NS}/connections/dch/{connection_id}/assets")
    assert response.status_code == 200, response.text
    assets = response.json()["assets"]
    assert {
        (asset["asset_type"], asset["name"], asset["collection"]) for asset in assets
    } == {
        ("table", "events", COL),
        ("volume", "documents", "archive"),
    }
    assert {asset["uuid"] for asset in assets} == {
        table.json()["uuid"],
        volume.json()["uuid"],
    }
    assert all(
        asset["connection_ref"] == {"type": "dch", "id": connection_id}
        for asset in assets
    )


def test_connection_asset_lookup_empty_and_invalid_filters(
    sqlite_registry: SqlRegistry,
) -> None:
    client = _client(sqlite_registry)
    _ensure_collection(client)

    empty = client.get(
        f"/v1/{NS}/connections/dch/550e8400-e29b-41d4-a716-446655440000/assets"
    )
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"assets": []}

    invalid_id = client.get(f"/v1/{NS}/connections/dch/not-a-uuid/assets")
    assert invalid_id.status_code == 400, invalid_id.text

    unsupported_source = client.get(
        f"/v1/{NS}/connections/secret/550e8400-e29b-41d4-a716-446655440000/assets"
    )
    assert unsupported_source.status_code == 400, unsupported_source.text
