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

import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from feast.api.catalog.catalog_utils import (
    CATALOG_PROJECT,
    DEFAULT_COLLECTION,
    ns_meta_key,
    scoped_name,
)
from feast.api.catalog.config import CATALOG_CONFIG_ENDPOINTS, get_config_router
from feast.api.catalog.errors import register_error_handlers
from feast.api.catalog.namespaces import get_namespace_router
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig
from feast.saved_dataset import SavedDataset

NS = "demo-user-1"
NS2 = "demo-user-2"
COL = "underwriting"


@pytest.fixture
def sqlite_registry():
    fd, registry_path = tempfile.mkstemp()
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


def _client(registry) -> TestClient:
    app = FastAPI()
    app.state.registry = registry
    register_error_handlers(app)
    app.include_router(get_config_router())
    app.include_router(get_namespace_router())
    return TestClient(app, raise_server_exceptions=False)


def _make_saved_dataset(name: str, namespace: str, collection: str) -> SavedDataset:
    return SavedDataset(
        name=name,
        features=["fv:feature"],
        join_keys=["entity_id"],
        storage=SavedDatasetFileStorage(path="file:///tmp/dataset.parquet"),
        namespace=namespace,
        collection=collection,
        tags={"_catalog_managed": "true", "asset_type": "table"},
    )


def test_n1_list_empty_registry_has_default(sqlite_registry):
    response = _client(sqlite_registry).get(f"/v1/{NS}/namespaces")
    assert response.status_code == 200
    assert response.json() == {"namespaces": [[DEFAULT_COLLECTION]]}


def test_n2_create_round_trip_and_scoped_tag(sqlite_registry):
    client = _client(sqlite_registry)
    response = client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": [COL], "properties": {"owner": "uw"}},
    )
    assert response.status_code == 200
    assert response.json() == {
        "namespace": [COL],
        "properties": {"owner": "uw"},
    }

    listed = client.get(f"/v1/{NS}/namespaces")
    assert listed.status_code == 200
    assert listed.json()["namespaces"] == [[DEFAULT_COLLECTION], [COL]]

    got = client.get(f"/v1/{NS}/namespaces/{COL}")
    assert got.status_code == 200
    assert got.json()["properties"] == {"owner": "uw"}

    head = client.head(f"/v1/{NS}/namespaces/{COL}")
    assert head.status_code == 204

    project = sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False)
    key = ns_meta_key(NS, COL)
    assert key in project.tags
    assert NS in key
    assert f"_ns_meta_{COL}" not in project.tags


def test_n3_duplicate_create_is_409_already_exists(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    response = client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["type"] == "AlreadyExistsException"
    assert error["code"] == 409
    assert "detail" not in response.json()


def test_n4_same_collection_name_isolated_across_tenants(sqlite_registry):
    client = _client(sqlite_registry)
    first = client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": [COL], "properties": {"owner": "uw-1"}},
    )
    assert first.status_code == 200
    second = client.post(
        f"/v1/{NS2}/namespaces",
        json={"namespace": [COL], "properties": {"owner": "uw-2"}},
    )
    assert second.status_code == 200
    assert client.get(f"/v1/{NS}/namespaces/{COL}").json()["properties"] == {
        "owner": "uw-1"
    }
    assert client.get(f"/v1/{NS2}/namespaces/{COL}").json()["properties"] == {
        "owner": "uw-2"
    }
    project = sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False)
    assert ns_meta_key(NS, COL) in project.tags
    assert ns_meta_key(NS2, COL) in project.tags
    assert f"_ns_meta_{COL}" not in project.tags


def test_n5_head_unknown_is_404(sqlite_registry):
    response = _client(sqlite_registry).head(f"/v1/{NS}/namespaces/missing")
    assert response.status_code == 404


def test_n6_get_and_head_default_on_empty_registry(sqlite_registry):
    client = _client(sqlite_registry)
    got = client.get(f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}")
    assert got.status_code == 200
    assert got.json() == {"namespace": [DEFAULT_COLLECTION], "properties": {}}
    head = client.head(f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}")
    assert head.status_code == 204


def test_n7_delete_empty_non_default(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    response = client.delete(f"/v1/{NS}/namespaces/{COL}")
    assert response.status_code == 204
    listed = client.get(f"/v1/{NS}/namespaces")
    assert listed.json()["namespaces"] == [[DEFAULT_COLLECTION]]
    project = sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False)
    assert ns_meta_key(NS, COL) not in project.tags
    assert client.get(f"/v1/{NS}/namespaces/{COL}").status_code == 404


def test_n8_delete_with_asset_is_409_not_empty(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    sqlite_registry.apply_saved_dataset(
        _make_saved_dataset(
            scoped_name(NS, COL, "risk_scores"),
            NS,
            COL,
        ),
        CATALOG_PROJECT,
    )
    response = client.delete(f"/v1/{NS}/namespaces/{COL}")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["type"] == "NamespaceNotEmptyException"
    assert error["code"] == 409
    assert client.get(f"/v1/{NS}/namespaces/{COL}").status_code == 200


def test_n9_delete_default_is_400(sqlite_registry):
    response = _client(sqlite_registry).delete(
        f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}"
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "BadRequestException"
    assert "default" in error["message"].lower()


def test_n10_update_properties(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(
        f"/v1/{NS}/namespaces",
        json={
            "namespace": [COL],
            "properties": {"owner": "uw", "deprecated-key": "x"},
        },
    )
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/properties",
        json={
            "updates": {"owner": "data-team", "status": "active"},
            "removals": ["deprecated-key", "absent"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"updated", "removed", "missing"}
    assert sorted(body["updated"]) == ["owner", "status"]
    assert body["removed"] == ["deprecated-key"]
    assert body["missing"] == ["absent"]
    got = client.get(f"/v1/{NS}/namespaces/{COL}")
    assert got.json()["properties"] == {
        "owner": "data-team",
        "status": "active",
    }


def test_n11_multipart_namespace_is_iceberg_400(sqlite_registry):
    client = _client(sqlite_registry)
    response = client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": ["a", "b"]},
    )
    assert response.status_code == 400
    body = response.json()
    assert "detail" not in body
    assert body["error"]["type"] == "BadRequestException"

    nested = client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": [f"a{chr(0x1F)}b"]},
    )
    assert nested.status_code == 400
    assert "detail" not in nested.json()
    assert nested.json()["error"]["type"] == "BadRequestException"


def test_n12_config_advertises_namespace_not_tables(sqlite_registry):
    response = _client(sqlite_registry).get("/v1/config")
    assert response.status_code == 200
    endpoints = response.json()["endpoints"]
    assert endpoints == CATALOG_CONFIG_ENDPOINTS
    for sig in (
        "GET /v1/{prefix}/namespaces",
        "POST /v1/{prefix}/namespaces",
        "GET /v1/{prefix}/namespaces/{namespace}",
        "HEAD /v1/{prefix}/namespaces/{namespace}",
        "DELETE /v1/{prefix}/namespaces/{namespace}",
        "POST /v1/{prefix}/namespaces/{namespace}/properties",
    ):
        assert sig in endpoints
    assert all("/tables" not in sig for sig in endpoints)
    assert all("/volumes" not in sig for sig in endpoints)


def test_post_default_is_409(sqlite_registry):
    response = _client(sqlite_registry).post(
        f"/v1/{NS}/namespaces",
        json={"namespace": [DEFAULT_COLLECTION]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "AlreadyExistsException"


def test_missing_create_body_is_iceberg_400(sqlite_registry):
    response = _client(sqlite_registry).post(f"/v1/{NS}/namespaces", json={})
    assert response.status_code == 400
    body = response.json()
    assert "detail" not in body
    assert body["error"]["type"] == "BadRequestException"
    assert "namespace" in body["error"]["message"]
