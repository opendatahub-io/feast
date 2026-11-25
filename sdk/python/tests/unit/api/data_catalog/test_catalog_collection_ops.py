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

"""Collection rename and asset move. Existing delete-if-empty behavior stays."""

import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool

from feast.api.data_catalog.assets import get_asset_router
from feast.api.data_catalog.catalog_assets import insert_catalog_dataset
from feast.api.data_catalog.catalog_utils import (
    CATALOG_PROJECT,
    DEFAULT_COLLECTION,
    ns_meta_key,
    scoped_name,
)
from feast.api.data_catalog.config import CATALOG_CONFIG_ENDPOINTS, get_config_router
from feast.api.data_catalog.errors import register_error_handlers
from feast.api.data_catalog.namespaces import get_namespace_router
from feast.errors import SavedDatasetNotFound
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig
from feast.saved_dataset import SavedDataset

NS = "demo-user-1"
NS2 = "demo-user-2"
COL = "underwriting"
DEST = "pricing"


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
    app.include_router(get_asset_router())
    return TestClient(app, raise_server_exceptions=False)


def _managed_dataset(
    namespace: str,
    collection: str,
    display_name: str,
    *,
    managed: bool = True,
    extra_tags: dict[str, str] | None = None,
) -> SavedDataset:
    tags = {
        "asset_type": "table",
        "format": "parquet",
        "owner": "uw",
        "uuid": "keep-me",
    }
    if managed:
        tags["_catalog_managed"] = "true"
    if extra_tags:
        tags.update(extra_tags)
    return SavedDataset(
        name=scoped_name(namespace, collection, display_name),
        features=["fv:feature"],
        join_keys=["entity_id"],
        storage=SavedDatasetFileStorage(path="file:///tmp/dataset.parquet"),
        namespace=namespace,
        collection=collection,
        description="risk model",
        tags=tags,
    )


def _insert_asset(registry, collection: str, display_name: str) -> None:
    insert_catalog_dataset(
        registry,
        rhai_ns=NS,
        collection=collection,
        display_name=display_name,
        location="file:///tmp/dataset.parquet",
        tags={
            "asset_type": "table",
            "format": "parquet",
            "owner": "uw",
            "purpose": "scores",
            "_labels": '["risk"]',
        },
        description="risk model",
    )


def test_rename_moves_tag_and_assets_and_keeps_metadata(sqlite_registry):
    client = _client(sqlite_registry)
    created = client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": [COL], "properties": {"owner": "uw", "status": "active"}},
    )
    assert created.status_code == 200
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(
            NS,
            COL,
            "risk_scores",
            extra_tags={
                "_connection_ref": '{"type":"secret","secret_name":"s3"}',  # pragma: allowlist secret
            },
        ),
        CATALOG_PROJECT,
    )
    before = sqlite_registry.get_saved_dataset(
        scoped_name(NS, COL, "risk_scores"), CATALOG_PROJECT, allow_cache=False
    )

    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": ["underwriting-v2"]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "namespace": ["underwriting-v2"],
        "properties": {"owner": "uw", "status": "active"},
    }

    project = sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False)
    assert ns_meta_key(NS, COL) not in project.tags
    assert project.tags[ns_meta_key(NS, "underwriting-v2")]

    with pytest.raises(SavedDatasetNotFound):
        sqlite_registry.get_saved_dataset(
            scoped_name(NS, COL, "risk_scores"), CATALOG_PROJECT, allow_cache=False
        )
    after = sqlite_registry.get_saved_dataset(
        scoped_name(NS, "underwriting-v2", "risk_scores"),
        CATALOG_PROJECT,
        allow_cache=False,
    )
    assert after.collection == "underwriting-v2"
    assert after.name == scoped_name(NS, "underwriting-v2", "risk_scores")
    assert after.description == before.description
    assert after.tags == before.tags
    assert after.created_timestamp == before.created_timestamp
    assert after.storage.to_proto().SerializeToString() == (
        before.storage.to_proto().SerializeToString()
    )
    listed = client.get(f"/v1/{NS}/namespaces").json()["namespaces"]
    assert [COL] not in listed
    assert ["underwriting-v2"] in listed
    assert client.delete(f"/v1/{NS}/namespaces/{COL}").status_code == 404


def test_rename_rejects_default_same_name_and_missing_or_duplicate(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL], "properties": {}})
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [DEST]})

    default_source = client.post(
        f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}/rename",
        json={"newNamespace": ["renamed-default"]},
    )
    assert default_source.status_code == 400
    assert default_source.json()["error"]["type"] == "BadRequestException"

    default_dest = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": [DEFAULT_COLLECTION]},
    )
    assert default_dest.status_code == 400

    same = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": [COL]},
    )
    assert same.status_code == 400
    assert "same" in same.json()["error"]["message"]

    missing = client.post(
        f"/v1/{NS}/namespaces/missing/rename",
        json={"newNamespace": ["fresh-name"]},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "NoSuchNamespaceException"

    duplicate = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": [DEST]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["type"] == "AlreadyExistsException"
    assert (
        ns_meta_key(NS, COL)
        in sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False).tags
    )


def test_rename_invalid_name_and_overflow_do_not_write(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": ["ab"]})
    display = "n" * 240
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, "ab", display),
        CATALOG_PROJECT,
    )
    invalid = client.post(
        f"/v1/{NS}/namespaces/ab/rename",
        json={"newNamespace": ["Bad Name"]},
    )
    assert invalid.status_code == 400
    overflow = client.post(
        f"/v1/{NS}/namespaces/ab/rename",
        json={"newNamespace": ["abcd"]},
    )
    assert overflow.status_code == 400
    assert "255" in overflow.json()["error"]["message"]
    still = sqlite_registry.get_saved_dataset(
        scoped_name(NS, "ab", display), CATALOG_PROJECT, allow_cache=False
    )
    assert still.collection == "ab"


def test_rename_skips_unmanaged_rows_and_other_tenants(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    client.post(
        f"/v1/{NS2}/namespaces",
        json={"namespace": [COL], "properties": {"owner": "other"}},
    )
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, COL, "kept"),
        CATALOG_PROJECT,
    )
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, COL, "foreign", managed=False),
        CATALOG_PROJECT,
    )
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS2, COL, "other-tenant"),
        CATALOG_PROJECT,
    )
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": ["renamed"]},
    )
    assert response.status_code == 200
    assert (
        sqlite_registry.get_saved_dataset(
            scoped_name(NS, "renamed", "kept"), CATALOG_PROJECT, allow_cache=False
        ).collection
        == "renamed"
    )
    unmanaged = sqlite_registry.get_saved_dataset(
        scoped_name(NS, COL, "foreign"), CATALOG_PROJECT, allow_cache=False
    )
    assert unmanaged.collection == COL
    other = sqlite_registry.get_saved_dataset(
        scoped_name(NS2, COL, "other-tenant"), CATALOG_PROJECT, allow_cache=False
    )
    assert other.collection == COL
    assert (
        ns_meta_key(NS2, COL)
        in sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False).tags
    )


def test_rename_table_only_collection_does_not_invent_a_tag(sqlite_registry):
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, COL, "only-asset"),
        CATALOG_PROJECT,
    )
    client = _client(sqlite_registry)
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": ["renamed"]},
    )
    assert response.status_code == 200
    assert response.json()["properties"] == {}
    project = sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False)
    assert ns_meta_key(NS, "renamed") not in project.tags
    assert ns_meta_key(NS, COL) not in project.tags
    moved = sqlite_registry.get_saved_dataset(
        scoped_name(NS, "renamed", "only-asset"), CATALOG_PROJECT, allow_cache=False
    )
    assert moved.collection == "renamed"


def test_rename_empty_tag_only_collection(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(
        f"/v1/{NS}/namespaces",
        json={"namespace": [COL], "properties": {"owner": "uw"}},
    )
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": ["renamed"]},
    )
    assert response.status_code == 200
    assert response.json()["properties"] == {"owner": "uw"}
    project = sqlite_registry.get_project(CATALOG_PROJECT, allow_cache=False)
    assert ns_meta_key(NS, "renamed") in project.tags
    assert ns_meta_key(NS, COL) not in project.tags


def test_unmanaged_destination_name_blocks_rename(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, COL, "risk_scores"),
        CATALOG_PROJECT,
    )
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, "renamed", "risk_scores", managed=False),
        CATALOG_PROJECT,
    )
    response = client.post(
        f"/v1/{NS}/namespaces/{COL}/rename",
        json={"newNamespace": ["renamed"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "AlreadyExistsException"
    assert (
        sqlite_registry.get_saved_dataset(
            scoped_name(NS, COL, "risk_scores"), CATALOG_PROJECT, allow_cache=False
        ).collection
        == COL
    )


def test_delete_of_non_empty_collection_is_unchanged(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    sqlite_registry.apply_saved_dataset(
        _managed_dataset(NS, COL, "risk_scores"),
        CATALOG_PROJECT,
    )
    response = client.delete(f"/v1/{NS}/namespaces/{COL}")
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "NamespaceNotEmptyException"


def test_tables_rename_stays_unimplemented_and_unadvertised(sqlite_registry):
    from feast.api.data_catalog.tables import get_table_router

    app = FastAPI()
    app.state.registry = sqlite_registry
    register_error_handlers(app)
    app.include_router(get_config_router())
    app.include_router(get_table_router())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/v1/{NS}/tables/rename", json={})
    assert response.status_code == 501
    endpoints = client.get("/v1/config").json()["endpoints"]
    assert endpoints == CATALOG_CONFIG_ENDPOINTS
    assert "POST /v1/{prefix}/namespaces/{namespace}/rename" in endpoints
    assert "POST /v1/{prefix}/namespaces/{namespace}/assets/{asset}/move" in endpoints
    assert "POST /v1/{prefix}/tables/rename" not in endpoints


def test_move_asset_preserves_identity_and_rejects_edges(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [DEST]})
    _insert_asset(sqlite_registry, COL, "risk_scores")
    _insert_asset(sqlite_registry, DEST, "taken")
    before = sqlite_registry.get_saved_dataset(
        scoped_name(NS, COL, "risk_scores"), CATALOG_PROJECT, allow_cache=False
    )

    moved = client.post(
        f"/v1/{NS}/namespaces/{COL}/assets/risk_scores/move",
        json={"destinationNamespace": [DEST]},
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["name"] == "risk_scores"
    assert body["collection"] == DEST
    assert body["owner"] == "uw"
    assert body["uuid"] == before.tags["uuid"]
    assert body["description"] == "risk model"
    assert body["properties"]["purpose"] == "scores"

    after = sqlite_registry.get_saved_dataset(
        scoped_name(NS, DEST, "risk_scores"), CATALOG_PROJECT, allow_cache=False
    )
    assert after.created_timestamp == before.created_timestamp
    assert after.tags["uuid"] == before.tags["uuid"]
    assert after.tags["purpose"] == "scores"
    with pytest.raises(SavedDatasetNotFound):
        sqlite_registry.get_saved_dataset(
            scoped_name(NS, COL, "risk_scores"), CATALOG_PROJECT, allow_cache=False
        )

    missing_asset = client.post(
        f"/v1/{NS}/namespaces/{COL}/assets/missing/move",
        json={"destinationNamespace": [DEST]},
    )
    assert missing_asset.status_code == 404
    assert missing_asset.json()["error"]["type"] == "NoSuchAssetException"

    missing_dest = client.post(
        f"/v1/{NS}/namespaces/{DEST}/assets/risk_scores/move",
        json={"destinationNamespace": ["does-not-exist"]},
    )
    assert missing_dest.status_code == 404
    assert missing_dest.json()["error"]["type"] == "NoSuchNamespaceException"

    same = client.post(
        f"/v1/{NS}/namespaces/{DEST}/assets/risk_scores/move",
        json={"destinationNamespace": [DEST]},
    )
    assert same.status_code == 400

    _insert_asset(sqlite_registry, COL, "taken")
    conflict = client.post(
        f"/v1/{NS}/namespaces/{COL}/assets/taken/move",
        json={"destinationNamespace": [DEST]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["type"] == "AlreadyExistsException"
    assert (
        sqlite_registry.get_saved_dataset(
            scoped_name(NS, COL, "taken"), CATALOG_PROJECT, allow_cache=False
        ).collection
        == COL
    )


def test_move_into_default_and_back(sqlite_registry):
    client = _client(sqlite_registry)
    client.post(f"/v1/{NS}/namespaces", json={"namespace": [COL]})
    _insert_asset(sqlite_registry, DEFAULT_COLLECTION, "from-default")
    into_custom = client.post(
        f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}/assets/from-default/move",
        json={"destinationNamespace": [COL]},
    )
    assert into_custom.status_code == 200
    assert into_custom.json()["collection"] == COL
    back = client.post(
        f"/v1/{NS}/namespaces/{COL}/assets/from-default/move",
        json={"destinationNamespace": [DEFAULT_COLLECTION]},
    )
    assert back.status_code == 200
    assert back.json()["collection"] == DEFAULT_COLLECTION


def test_concurrent_renames_of_different_collections_both_succeed():
    import threading

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
    try:
        client = _client(registry)
        client.post(f"/v1/{NS}/namespaces", json={"namespace": ["alpha"]})
        client.post(f"/v1/{NS}/namespaces", json={"namespace": ["beta"]})
        registry.apply_saved_dataset(
            _managed_dataset(NS, "alpha", "a-asset"), CATALOG_PROJECT
        )
        registry.apply_saved_dataset(
            _managed_dataset(NS, "beta", "b-asset"), CATALOG_PROJECT
        )
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker(source: str, target: str):
            barrier.wait()
            try:
                response = _client(registry).post(
                    f"/v1/{NS}/namespaces/{source}/rename",
                    json={"newNamespace": [target]},
                )
                if response.status_code != 200:
                    errors.append(AssertionError(response.text))
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("alpha", "alpha-v2")),
            threading.Thread(target=worker, args=("beta", "beta-v2")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
        assert not errors, errors
        assert (
            registry.get_saved_dataset(
                scoped_name(NS, "alpha-v2", "a-asset"),
                CATALOG_PROJECT,
                allow_cache=False,
            ).collection
            == "alpha-v2"
        )
        assert (
            registry.get_saved_dataset(
                scoped_name(NS, "beta-v2", "b-asset"),
                CATALOG_PROJECT,
                allow_cache=False,
            ).collection
            == "beta-v2"
        )
    finally:
        registry.teardown()
