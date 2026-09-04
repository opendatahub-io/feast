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

import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from feast.api.catalog.catalog_utils import CATALOG_PROJECT, DEFAULT_COLLECTION, scoped_name
from feast.api.catalog.config import get_config_router
from feast.api.catalog.errors import register_error_handlers
from feast.api.catalog.generic_tables import get_generic_table_router
from feast.api.catalog.namespaces import get_namespace_router
from feast.api.catalog.tables import get_table_router
from feast.api.catalog.volumes import get_volume_router
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig
from feast.saved_dataset import SavedDataset

NS = "demo-user-1"
COL = "underwriting"
TABLE = "events"
PARQUET = "claims-parquet"


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
    app.include_router(get_volume_router())
    app.include_router(get_generic_table_router())
    return TestClient(app, raise_server_exceptions=False)


def _ensure_collection(client: TestClient, project: str = NS, collection: str = COL):
    client.post(
        f"/v1/{project}/namespaces",
        json={"namespace": [collection], "properties": {}},
    )


def _assert_501(response) -> None:
    assert response.status_code == 501, response.text
    assert response.json()["error"]["type"] == "NotImplementedException"


def _seed_iceberg_table(registry, name: str = TABLE) -> None:
    registry.apply_saved_dataset(
        SavedDataset(
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
        ),
        CATALOG_PROJECT,
    )


def test_iceberg_format_post_inserts_catalog_row(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    omitted = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": TABLE, "location": "s3://bucket/events/"},
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["format"] == "iceberg"
    iceberg_rest = client.post(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg_rest.status_code == 501
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/generic-tables")
    assert [asset["name"] for asset in listed.json()["assets"]] == [TABLE]
    iceberg = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]
    named = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": "events-2", "format": "iceberg"},
    )
    assert named.status_code == 201, named.text
    assert named.json()["format"] == "iceberg"


def test_create_parquet_201_no_invented_user(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    created = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": PARQUET,
            "format": "parquet",
            "location": "s3://bucket/claims.parquet",
            "schema_fields": [
                {"name": "claim_id", "type": "string", "nullable": False}
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == PARQUET
    assert body["asset_type"] == "table"
    assert body["format"] == "parquet"
    assert body["location"] == "s3://bucket/claims.parquet"
    assert body["collection"] == COL
    assert body["registered_by"] is None
    assert body["columns"][0]["name"] == "claim_id"
    assert "document_count" not in body

    iceberg = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg.json() == {"identifiers": []}


def test_registered_by_from_header_only(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    created = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "csv"},
        headers={"X-User": "uw-analyst"},
    )
    assert created.status_code == 201
    assert created.json()["registered_by"] == "uw-analyst"


def test_duplicate_is_409(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    payload = {"name": PARQUET, "format": "parquet"}
    assert (
        client.post(
            f"/v1/{NS}/namespaces/{COL}/generic-tables", json=payload
        ).status_code
        == 201
    )
    again = client.post(f"/v1/{NS}/namespaces/{COL}/generic-tables", json=payload)
    assert again.status_code == 409
    assert again.json()["error"]["type"] == "AlreadyExistsException"


def test_list_includes_seeded_iceberg_and_skips_volumes(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "parquet"},
    )
    client.post(
        f"/v1/{NS}/namespaces/{COL}/volumes",
        json={"name": "docs", "location": "s3://bucket/docs/"},
    )
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/generic-tables")
    assert listed.status_code == 200
    names = sorted(asset["name"] for asset in listed.json()["assets"])
    assert names == [PARQUET, TABLE]
    iceberg = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg.json()["identifiers"] == [{"namespace": [COL], "name": TABLE}]


def test_patch_replaces_schema_and_may_set_iceberg_format(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": PARQUET,
            "format": "parquet",
            "schema_fields": [{"name": "a", "type": "string"}],
            "labels": ["uw"],
        },
    )
    patched = client.patch(
        f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}",
        json={
            "description": "claims",
            "schema_fields": [
                {"name": "b", "type": "long"},
                {"name": "c", "type": "string"},
            ],
            "add_labels": ["pii"],
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["description"] == "claims"
    assert [col["name"] for col in body["columns"]] == ["b", "c"]
    assert body["labels"] == ["uw", "pii"]
    as_iceberg = client.patch(
        f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}",
        json={"format": "iceberg"},
    )
    assert as_iceberg.status_code == 200, as_iceberg.text
    assert as_iceberg.json()["format"] == "iceberg"
    iceberg = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg.json()["identifiers"] == [{"namespace": [COL], "name": PARQUET}]


def test_get_delete_and_missing(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "csv"},
    )
    got = client.get(f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}")
    assert got.status_code == 200
    assert got.json()["format"] == "csv"
    deleted = client.delete(f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}")
    assert deleted.status_code == 204
    missing = client.get(f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}")
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "NoSuchTableException"


def test_missing_collection_404(sqlite_registry):
    client = _client(sqlite_registry)
    listed = client.get(f"/v1/{NS}/namespaces/{COL}/generic-tables")
    assert listed.status_code == 404
    created = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "parquet"},
    )
    assert created.status_code == 404


def test_create_in_default(sqlite_registry):
    client = _client(sqlite_registry)
    created = client.post(
        f"/v1/{NS}/namespaces/{DEFAULT_COLLECTION}/generic-tables",
        json={"name": PARQUET, "format": "postgresql"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["collection"] == DEFAULT_COLLECTION


def test_label_query_filters_list(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "parquet", "labels": ["uw"]},
    )
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": "other", "format": "csv", "labels": ["finance"]},
    )
    filtered = client.get(
        f"/v1/{NS}/namespaces/{COL}/generic-tables", params={"label": "uw"}
    )
    assert [asset["name"] for asset in filtered.json()["assets"]] == [PARQUET]


def test_properties_cannot_set_format_to_iceberg(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "parquet"},
    )
    patched = client.patch(
        f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}",
        json={"properties": {"team": "uw", "format": "iceberg", "asset_type": "volume"}},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["format"] == "parquet"
    assert body["asset_type"] == "table"
    assert body["properties"] == {"team": "uw"}
    iceberg = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg.json() == {"identifiers": []}


def test_generic_delete_unregisters_iceberg_catalog_row(sqlite_registry):
    """Data Hub unregister (option A). Iceberg DELETE stays 501."""
    client = _client(sqlite_registry)
    _ensure_collection(client)
    _seed_iceberg_table(sqlite_registry)
    _assert_501(client.delete(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}"))
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 204
    patched = client.patch(
        f"/v1/{NS}/namespaces/{COL}/generic-tables/{TABLE}",
        json={"description": "stale warehouse table"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "stale warehouse table"
    assert patched.json()["format"] == "iceberg"
    dropped = client.delete(f"/v1/{NS}/namespaces/{COL}/generic-tables/{TABLE}")
    assert dropped.status_code == 204
    iceberg = client.get(f"/v1/{NS}/namespaces/{COL}/tables")
    assert iceberg.json() == {"identifiers": []}
    assert client.head(f"/v1/{NS}/namespaces/{COL}/tables/{TABLE}").status_code == 404


def test_connection_ref_round_trips(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    dch_id = "11111111-1111-1111-1111-111111111111"
    created = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": PARQUET,
            "format": "parquet",
            "connection_ref": {"type": "dch", "id": dch_id},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["connection_ref"] == {"type": "dch", "id": dch_id}
    got = client.get(f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}")
    assert got.json()["connection_ref"] == {"type": "dch", "id": dch_id}


def test_patch_maturity_enum(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={"name": PARQUET, "format": "parquet"},
    )
    bad = client.patch(
        f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}",
        json={"maturity": "banana"},
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["type"] == "BadRequestException"
    ok = client.patch(
        f"/v1/{NS}/namespaces/{COL}/generic-tables/{PARQUET}",
        json={"maturity": "production"},
    )
    assert ok.status_code == 200, ok.text


def test_schema_field_empty_name_is_400_hyphen_name_ok(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    empty = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": PARQUET,
            "format": "parquet",
            "schema_fields": [{"name": "", "type": "string"}],
        },
    )
    assert empty.status_code == 400, empty.text
    hyphen = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": PARQUET,
            "format": "parquet",
            "schema_fields": [{"name": "claim-id", "type": "string"}],
        },
    )
    assert hyphen.status_code == 201, hyphen.text
    assert hyphen.json()["columns"][0]["name"] == "claim-id"


def test_oversized_labels_are_400(sqlite_registry):
    client = _client(sqlite_registry)
    _ensure_collection(client)
    huge = client.post(
        f"/v1/{NS}/namespaces/{COL}/generic-tables",
        json={
            "name": PARQUET,
            "format": "parquet",
            "labels": ["x" * 256],
        },
    )
    assert huge.status_code == 400, huge.text
    assert huge.json()["error"]["type"] == "BadRequestException"
