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
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from feast.api.data_catalog.catalog_utils import (
    CATALOG_PROJECT,
    DEFAULT_COLLECTION,
    scoped_name,
)
from feast.api.data_catalog.config import CATALOG_CONFIG_ENDPOINTS, get_config_router
from feast.api.data_catalog.errors import register_error_handlers
from feast.api.data_catalog.namespaces import get_namespace_router
from feast.api.data_catalog.tables import get_table_router
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig
from feast.saved_dataset import SavedDataset

NS = "demo-user-1"
NS2 = "demo-user-2"
COL = "underwriting"
TABLE = "events"


@pytest.fixture
def sqlite_registry():
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


def _client(registry) -> TestClient:
    app = FastAPI()
    app.state.registry = registry
    register_error_handlers(app)
    app.include_router(get_config_router())
    app.include_router(get_namespace_router())
    app.include_router(get_table_router())
    return TestClient(app, raise_server_exceptions=False)


def _ensure_collection(client: TestClient, project: str = NS, collection: str = COL):
    client.post(
        f"/v1/{project}/namespaces",
        json={"namespace": [collection], "properties": {}},
    )


def _seed_iceberg_table(
    registry,
    *,
    project: str = NS,
    collection: str = COL,
    name: str = TABLE,
    extra_tags: dict | None = None,
) -> SavedDataset:
    tags = {
        "_catalog_managed": "true",
        "asset_type": "table",
        "format": "iceberg",
        "owner": "uw",
    }
    if extra_tags:
        tags.update(extra_tags)
    dataset = SavedDataset(
        name=scoped_name(project, collection, name),
        features=["fv:feature"],
        join_keys=["entity_id"],
        storage=SavedDatasetFileStorage(path="s3://bucket/events/"),
        namespace=project,
        collection=collection,
        tags=tags,
    )
    registry.apply_saved_dataset(dataset, CATALOG_PROJECT)
    return dataset


def _assert_501(response) -> None:
    assert response.status_code == 501, response.text
    assert response.json()["error"]["type"] == "NotImplementedException"
    assert "feast://" not in (response.text or "")
    if response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
        if isinstance(body, dict):
            assert "metadata-location" not in body


def test_t1_list_empty_default(sqlite_registry):
    response = _client(sqlite_registry).get(
        f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}/tables"
    )
    assert response.status_code == 200
    assert response.json() == {"identifiers": []}


def test_t2_list_missing_collection_404(sqlite_registry):
    response = _client(sqlite_registry).get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NoSuchNamespaceException"


def test_t3_list_head_after_seed(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _assert_501(client.post(f"/v1/{NS}/namespaces/{COL}/tables"))
    _seed_iceberg_table(sqlite_registry)
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert listed.status_code == 200
    assert listed.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]
    _assert_501(client.get(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}"))
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 204


def test_t4_http_create_does_not_insert(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _assert_501(client.post(f"/v1/{NS}/namespaces/{COL}/tables"))
    assert client.get(f"/v1/{NS}/namespaces/{COL}/tables").json() == {
        "identifiers": []
    }


def test_t5_seeded_same_name_other_project_ok(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _ensure_collection(client, NS2, COL)
    _seed_iceberg_table(sqlite_registry, project=NS)
    _seed_iceberg_table(sqlite_registry, project=NS2)
    first = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    second = client.get(f"/v1/{NS2}/namespaces/{COL}/tables")
    assert first.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]
    assert second.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]
    first_row = sqlite_registry.get_saved_dataset(
        scoped_name(NS, COL, TABLE), CATALOG_PROJECT, allow_cache=False
    )
    second_row = sqlite_registry.get_saved_dataset(
        scoped_name(NS2, COL, TABLE), CATALOG_PROJECT, allow_cache=False
    )
    assert first_row.name != second_row.name


def test_t6_create_missing_collection_is_501(sqlite_registry):
    _assert_501(
        _client(sqlite_registry).post(f"/v1/{NS}/namespaces/{COL}/tables")
    )


def test_t8_load_is_501(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _assert_501(client.get(f"/v1/{NS}/namespaces/{COL}/tables/missing"))


def test_t9_head_missing_404(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/missing").status_code == 404


def test_t10_delete_is_501_row_remains(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    _assert_501(client.delete(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}"))
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 204
    assert client.get(f"/v1/{NS}/namespaces/{COL}/tables").json()["identifiers"] == [
        {"namespace": [COL], "name": TABLE}
    ]


def test_t11_update_is_501_tags_unchanged(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    _assert_501(
        client.post(
            f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}",
            json={"updates": [{"action": "set-properties"}]},
        )
    )
    stored = sqlite_registry.get_saved_dataset(
        scoped_name(NS, COL, TABLE), CATALOG_PROJECT, allow_cache=False
    )
    assert stored.tags["owner"] == "uw"
    assert "tier" not in (stored.tags or {})


def test_no_load_table_models_or_feast_uri():
    import feast.api.data_catalog.tables as tables
    from feast.api.data_catalog import models

    assert not hasattr(models, "LoadTableResponse")
    assert not hasattr(models, "CreateTableRequest")
    assert not hasattr(models, "UpdateTableRequest")
    assert not hasattr(models, "RenameTableRequest")
    source = Path(tables.__file__).read_text()
    assert "feast://" not in source
    assert "metadata_location" not in source
    assert "saved_dataset_to_load_table" not in source


def test_t13_rename_is_501_source_remains(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    _assert_501(client.post(f"/v1/{NS}/tables/rename"))
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 204


def test_t15_list_skips_volumes(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    sqlite_registry.apply_saved_dataset(
        SavedDataset(
            name=scoped_name(NS, COL, "docs"),
            features=["fv:feature"],
            join_keys=["entity_id"],
            storage=SavedDatasetFileStorage(path="s3://bucket/docs/"),
            namespace=NS,
            collection=COL,
            tags={"_catalog_managed": "true", "asset_type": "volume"},
        ),
        CATALOG_PROJECT,
    )
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert listed.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]


def test_list_skips_iceberg_tags_without_catalog_managed(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    sqlite_registry.apply_saved_dataset(
        SavedDataset(
            name=scoped_name(NS, COL, "unmanaged"),
            features=["fv:feature"],
            join_keys=["entity_id"],
            storage=SavedDatasetFileStorage(path="s3://bucket/unmanaged/"),
            namespace=NS,
            collection=COL,
            tags={"asset_type": "table", "format": "iceberg"},
        ),
        CATALOG_PROJECT,
    )
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert listed.json() == {"identifiers": []}
    assert (
        client.head(f"/v1/{NS}/namespaces/{COL}/tables/unmanaged").status_code
        == 404
    )


def test_list_skips_untagged_saved_dataset(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    sqlite_registry.apply_saved_dataset(
        SavedDataset(
            name=scoped_name(NS, COL, "plain"),
            features=["fv:feature"],
            join_keys=["entity_id"],
            storage=SavedDatasetFileStorage(path="s3://bucket/plain/"),
            namespace=NS,
            collection=COL,
        ),
        CATALOG_PROJECT,
    )
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert listed.json() == {"identifiers": []}
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/plain").status_code == 404


def test_t16_config_advertises_read_not_writes(sqlite_registry):
    endpoints = _client(sqlite_registry).get("/v1/config").json()["endpoints"]
    assert endpoints == CATALOG_CONFIG_ENDPOINTS
    assert "GET /v1/{prefix}/namespaces/{namespace}/tables" in endpoints
    assert "HEAD /v1/{prefix}/namespaces/{namespace}/tables/{table}" in endpoints
    assert "GET /v1/{prefix}/namespaces/{namespace}/tables/{table}" not in endpoints
    assert "POST /v1/{prefix}/namespaces/{namespace}/tables" not in endpoints
    assert "POST /v1/{prefix}/namespaces/{namespace}/tables/{table}" not in endpoints
    assert "DELETE /v1/{prefix}/namespaces/{namespace}/tables/{table}" not in endpoints
    assert "POST /v1/{prefix}/tables/rename" not in endpoints
    assert all("/volumes" not in sig for sig in endpoints)


def test_t17_create_in_default_is_501(sqlite_registry):
    _assert_501(
        _client(sqlite_registry).post(
            f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}/tables"
        )
    )


def test_seeded_table_blocks_delete_collection_409(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    dropped = client.delete(f"/v1/{NS}/namespaces/{COL}")
    assert dropped.status_code == 409
    assert dropped.json()["error"]["type"] == "NamespaceNotEmptyException"
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 204
