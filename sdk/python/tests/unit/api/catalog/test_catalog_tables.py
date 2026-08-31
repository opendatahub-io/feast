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
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool

from feast.api.catalog.catalog_utils import (
    CATALOG_PROJECT,
    DEFAULT_COLLECTION,
    create_catalog_table,
    create_namespace_meta,
    delete_namespace_meta,
    ns_meta_key,
    scoped_name,
)
from feast.api.catalog.config import CATALOG_CONFIG_ENDPOINTS, get_config_router
from feast.api.catalog.errors import (
    NamespaceNotEmptyException,
    NoSuchNamespaceException,
    TableAlreadyExistsException,
    register_error_handlers,
)
from feast.api.catalog.namespaces import get_namespace_router
from feast.api.catalog.tables import get_table_router
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig
from feast.saved_dataset import SavedDataset

NS = "demo-user-1"
NS2 = "demo-user-2"
COL = "underwriting"
TABLE = "events"

SCHEMA = {
    "type": "struct",
    "schema-id": 0,
    "fields": [
        {"id": 1, "name": "user_id", "required": True, "type": "long"},
        {"id": 2, "name": "event", "required": False, "type": "string"},
    ],
}


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
    app.include_router(get_table_router())
    return TestClient(app, raise_server_exceptions=False)


def _create_body(name: str = TABLE, **extra) -> dict:
    body = {
        "name": name,
        "schema": SCHEMA,
        "location": "s3://bucket/events/",
        "properties": {"owner": "uw"},
    }
    body.update(extra)
    return body


def _ensure_collection(client: TestClient, project: str = NS, collection: str = COL):
    client.post(
        f"/v1/{project}/namespaces",
        json={"namespace": [collection], "properties": {}},
    )


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


def test_t3_create_round_trip(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == {"metadata-location", "metadata", "config"}
    assert body["config"] == {}
    metadata = body["metadata"]
    assert metadata["format-version"] == 2
    assert metadata["current-snapshot-id"] == -1
    assert metadata["location"] == "s3://bucket/events/"
    assert metadata["properties"]["format"] == "iceberg"
    assert metadata["properties"]["owner"] == "uw"
    fields = metadata["schemas"][0]["fields"]
    assert fields[0]["name"] == "user_id"
    assert fields[0]["required"] is True
    assert fields[1]["required"] is False
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert listed.status_code == 200
    assert listed.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]
    loaded = client.get(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}")
    assert loaded.status_code == 200
    assert loaded.json()["metadata"]["table-uuid"] == metadata["table-uuid"]
    head = client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}")
    assert head.status_code == 204
    stored = sqlite_registry.get_saved_dataset(
        scoped_name(NS, COL, TABLE), CATALOG_PROJECT, allow_cache=False
    )
    assert stored.namespace == NS
    assert stored.collection == COL
    assert [col.name for col in stored.columns] == ["user_id", "event"]


def test_t4_duplicate_create_409(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body()
    )
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "AlreadyExistsException"


def test_t5_same_name_other_project_ok(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _ensure_collection(client, NS2, COL)
    first = client.post(
        f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body()
    )
    second = client.post(
        f"/v1/{NS2}/namespaces/{COL}/tables", json=_create_body()
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert (
        first.json()["metadata"]["table-uuid"]
        != second.json()["metadata"]["table-uuid"]
    )


def test_t6_create_missing_collection_404(sqlite_registry):
    response = _client(sqlite_registry).post(
        f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body()
    )
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NoSuchNamespaceException"


def test_t7_missing_schema_is_iceberg_400(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/tables",
        json={"name": TABLE},
    )
    assert response.status_code == 400
    body = response.json()
    assert "detail" not in body
    assert body["error"]["type"] == "BadRequestException"
    assert "schema" in body["error"]["message"]


def test_t8_get_unknown_table_404(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    response = client.get(f"/v1/{NS}/namespaces/{COL}/tables/missing")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NoSuchTableException"


def test_t9_head_missing_404(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    response = client.head(f"/v1/{NS}/namespaces/{COL}/tables/missing")
    assert response.status_code == 404


def test_t10_delete_204(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    response = client.delete(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}")
    assert response.status_code == 204
    assert (
        client.get(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 404
    )
    assert client.get(f"/v1/{NS}/namespaces/{COL}/tables").json() == {
        "identifiers": []
    }


def test_t11_update_properties(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}",
        json={
            "updates": [
                {
                    "action": "set-properties",
                    "updates": {"owner": "data-team", "tier": "gold"},
                },
                {"action": "remove-properties", "removals": ["owner"]},
            ]
        },
    )
    assert response.status_code == 200, response.text
    props = response.json()["metadata"]["properties"]
    assert props["tier"] == "gold"
    assert "owner" not in props
    assert props["format"] == "iceberg"
    assert props["_catalog_managed"] == "true"


def test_t12_unsupported_update_action_501(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}",
        json={"updates": [{"action": "add-schema", "schema": SCHEMA}]},
    )
    assert response.status_code == 501
    assert response.json()["error"]["type"] == "NotImplementedException"


def test_t13_rename_across_collections(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": ["curated"], "properties": {}},
    )
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    response = client.post(
        f"/v1/{NS}/tables/rename",
        json={
            "source": {"namespace": [COL], "name": TABLE},
            "destination": {"namespace": ["curated"], "name": "events_v2"},
        },
    )
    assert response.status_code == 204, response.text
    assert (
        client.get(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 404
    )
    moved = client.get(f"/v1/{NS}/namespaces/curated/tables/events_v2")
    assert moved.status_code == 200
    assert moved.json()["metadata"]["properties"]["format"] == "iceberg"


def test_t14_rename_dest_exists_409(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    client.post(
        f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body(name="other")
    )
    response = client.post(
        f"/v1/{NS}/tables/rename",
        json={
            "source": {"namespace": [COL], "name": TABLE},
            "destination": {"namespace": [COL], "name": "other"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "AlreadyExistsException"


def test_t15_list_skips_volumes(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
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


def test_t16_config_advertises_tables_not_volumes(sqlite_registry):
    endpoints = _client(sqlite_registry).get("/v1/config").json()["endpoints"]
    assert endpoints == CATALOG_CONFIG_ENDPOINTS
    assert "GET /v1/{prefix}/namespaces/{namespace}/tables" in endpoints
    assert "POST /v1/{prefix}/tables/rename" in endpoints
    assert all("/volumes" not in sig for sig in endpoints)


def test_t17_create_in_default_without_namespace_post(sqlite_registry):
    client = _client(sqlite_registry)
    response = client.post(
        f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}/tables",
        json=_create_body(),
    )
    assert response.status_code == 200, response.text


def test_l2_delete_empty_collection_then_post_table_404(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    dropped = client.delete(f"/v1/{NS}/namespaces/{COL}")
    assert dropped.status_code == 204
    response = client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NoSuchNamespaceException"


def test_post_table_then_delete_collection_409(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    created = client.post(f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body())
    assert created.status_code == 200, created.text
    dropped = client.delete(f"/v1/{NS}/namespaces/{COL}")
    assert dropped.status_code == 409
    assert dropped.json()["error"]["type"] == "NamespaceNotEmptyException"
    assert client.get(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 200


def _threaded_sqlite_registry():
    _fd, registry_path = tempfile.mkstemp()
    registry = SqlRegistry(
        SqlRegistryConfig(
            registry_type="sql",
            path=f"sqlite:///{registry_path}",
            purge_feast_metadata=False,
            sqlalchemy_config_kwargs={
                "echo": False,
                "connect_args": {"check_same_thread": False, "timeout": 30},
                "poolclass": NullPool,
            },
        ),
        "scratch",
        None,
    )
    return registry, registry_path


def _catalog_table_dataset(name: str = TABLE) -> SavedDataset:
    return SavedDataset(
        name=scoped_name(NS, COL, name),
        features=["fv:feature"],
        join_keys=["entity_id"],
        storage=SavedDatasetFileStorage(path="s3://bucket/events/"),
        namespace=NS,
        collection=COL,
        tags={
            "_catalog_managed": "true",
            "asset_type": "table",
            "format": "iceberg",
        },
    )


def _catalog_managed_in_collection(registry) -> list[SavedDataset]:
    return [
        dataset
        for dataset in registry.list_saved_datasets(
            CATALOG_PROJECT, namespace=NS, collection=COL
        )
        if (dataset.tags or {}).get("_catalog_managed") == "true"
    ]


def test_concurrent_delete_vs_table_post():
    registry, _path = _threaded_sqlite_registry()
    try:
        create_namespace_meta(registry, NS, COL, {})
        barrier = threading.Barrier(2)
        delete_status: list[int] = []
        post_status: list[int] = []
        errors: list[BaseException] = []

        def do_delete():
            barrier.wait()
            try:
                delete_namespace_meta(registry, NS, COL)
                delete_status.append(204)
            except NamespaceNotEmptyException:
                delete_status.append(409)
            except NoSuchNamespaceException:
                delete_status.append(404)
            except BaseException as exc:
                errors.append(exc)

        def do_post():
            barrier.wait()
            try:
                create_catalog_table(registry, NS, COL, _catalog_table_dataset())
                post_status.append(200)
            except NoSuchNamespaceException:
                post_status.append(404)
            except TableAlreadyExistsException:
                post_status.append(409)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=do_delete),
            threading.Thread(target=do_post),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()

        assert not errors, errors
        assert len(delete_status) == 1 and len(post_status) == 1
        remaining = _catalog_managed_in_collection(registry)
        assert not (delete_status[0] == 204 and remaining), (
            f"DELETE 204 left catalog tables {[d.name for d in remaining]} "
            f"post={post_status[0]}"
        )
        allowed = (
            (delete_status[0] == 204 and post_status[0] == 404 and not remaining)
            or (post_status[0] == 200 and delete_status[0] == 409 and remaining)
        )
        assert allowed, (
            f"delete={delete_status[0]} post={post_status[0]} remaining={len(remaining)}"
        )
        if delete_status[0] == 204:
            project = registry.get_project(CATALOG_PROJECT, allow_cache=False)
            assert ns_meta_key(NS, COL) not in project.tags
    finally:
        registry.teardown()


def test_concurrent_table_posts_same_name_200_and_409():
    registry, _path = _threaded_sqlite_registry()
    try:
        create_namespace_meta(registry, NS, COL, {})
        barrier = threading.Barrier(2)
        outcomes: list[int] = []
        errors: list[BaseException] = []

        def worker():
            barrier.wait()
            try:
                create_catalog_table(registry, NS, COL, _catalog_table_dataset())
                outcomes.append(200)
            except TableAlreadyExistsException:
                outcomes.append(409)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()

        assert not errors, errors
        assert sorted(outcomes) == [200, 409]
        remaining = _catalog_managed_in_collection(registry)
        assert len(remaining) == 1
    finally:
        registry.teardown()


def test_concurrent_create_one_409():
    registry, _path = _threaded_sqlite_registry()
    try:
        client = _client(registry)
        _ensure_collection(client)
        statuses: list[int] = []

        def _post():
            response = client.post(
                f"/v1/{NS}/namespaces/{COL}/tables", json=_create_body()
            )
            statuses.append(response.status_code)

        threads = [threading.Thread(target=_post) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
        assert sorted(statuses) == [200, 409]
    finally:
        registry.teardown()
