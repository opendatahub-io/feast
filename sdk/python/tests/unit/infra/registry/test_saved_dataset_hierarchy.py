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
from sqlalchemy import create_engine, inspect, text

from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.infra.registry.sql import SqlRegistry, SqlRegistryConfig
from feast.saved_dataset import SavedDataset, SavedDatasetColumn


@pytest.fixture
def sqlite_registry():
    fd, registry_path = tempfile.mkstemp()
    registry_config = SqlRegistryConfig(
        registry_type="sql",
        path=f"sqlite:///{registry_path}",
        purge_feast_metadata=False,
    )
    registry = SqlRegistry(registry_config, "test_project", None)
    yield registry
    registry.teardown()


def _make_saved_dataset(
    name: str,
    namespace: str = "",
    collection: str = "",
    columns=None,
) -> SavedDataset:
    return SavedDataset(
        name=name,
        features=["fv:feature"],
        join_keys=["entity_id"],
        storage=SavedDatasetFileStorage(path="file:///tmp/dataset.parquet"),
        namespace=namespace,
        collection=collection,
        description="test dataset",
        columns=columns or [],
    )


def test_saved_dataset_columns_round_trip(sqlite_registry):
    dataset = _make_saved_dataset(
        name="claims",
        namespace="underwriting",
        collection="curated",
        columns=[
            SavedDatasetColumn(name="claim_id", type="string", description="id"),
            SavedDatasetColumn(name="amount", type="double"),
        ],
    )

    sqlite_registry.apply_saved_dataset(dataset, project="test_project")
    loaded = sqlite_registry.get_saved_dataset("claims", project="test_project")

    assert loaded.namespace == "underwriting"
    assert loaded.collection == "curated"
    assert loaded.description == "test dataset"
    assert loaded.columns == [
        SavedDatasetColumn(name="claim_id", type="string", description="id"),
        SavedDatasetColumn(name="amount", type="double"),
    ]


def test_list_saved_datasets_filters_by_namespace_sql(sqlite_registry):
    sqlite_registry.apply_saved_dataset(
        _make_saved_dataset("a", namespace="ns1"), project="test_project"
    )
    sqlite_registry.apply_saved_dataset(
        _make_saved_dataset("b", namespace="ns2"), project="test_project"
    )
    sqlite_registry.apply_saved_dataset(
        _make_saved_dataset("c", namespace="ns1", collection="raw"),
        project="test_project",
    )

    ns1 = sqlite_registry.list_saved_datasets(project="test_project", namespace="ns1")
    assert sorted(d.name for d in ns1) == ["a", "c"]

    ns1_raw = sqlite_registry.list_saved_datasets(
        project="test_project", namespace="ns1", collection="raw"
    )
    assert [d.name for d in ns1_raw] == ["c"]


def test_saved_datasets_sql_has_hierarchy_columns_and_index(sqlite_registry):
    engine = sqlite_registry.write_engine
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("saved_datasets")}
    assert "namespace" in column_names
    assert "collection" in column_names

    index_names = {index["name"] for index in inspector.get_indexes("saved_datasets")}
    assert "idx_saved_datasets_project_namespace" in index_names


def test_ensure_hierarchy_columns_migrates_legacy_table():
    """Existing DBs without hierarchy columns get them via additive migration."""
    fd, registry_path = tempfile.mkstemp()
    engine = create_engine(f"sqlite:///{registry_path}")

    # Simulate a pre-hierarchy schema (no namespace/collection columns).
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE saved_datasets ("
                "saved_dataset_name VARCHAR(255) NOT NULL, "
                "project_id VARCHAR(255) NOT NULL, "
                "last_updated_timestamp BIGINT NOT NULL, "
                "saved_dataset_proto BLOB NOT NULL, "
                "PRIMARY KEY (saved_dataset_name, project_id))"
            )
        )

    SqlRegistry._ensure_saved_dataset_hierarchy_columns(engine)

    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("saved_datasets")}
    assert "namespace" in column_names
    assert "collection" in column_names
    index_names = {index["name"] for index in inspector.get_indexes("saved_datasets")}
    assert "idx_saved_datasets_project_namespace" in index_names

    # Second call is a no-op and must not fail.
    SqlRegistry._ensure_saved_dataset_hierarchy_columns(engine)


def test_saved_dataset_column_proto_round_trip():
    column = SavedDatasetColumn(name="x", type="long", description="desc")
    restored = SavedDatasetColumn.from_proto(column.to_proto())
    assert restored == column


def test_sql_hierarchy_columns_match_proto_after_apply(sqlite_registry):
    """Denormalized SQL columns must stay in sync with proto (source of truth)."""
    from feast.protos.feast.core.SavedDataset_pb2 import SavedDataset as SavedDatasetProto

    dataset = _make_saved_dataset(
        name="claims",
        namespace="underwriting",
        collection="curated",
    )
    sqlite_registry.apply_saved_dataset(dataset, project="test_project")

    with sqlite_registry.write_engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT namespace, collection, saved_dataset_proto "
                "FROM saved_datasets "
                "WHERE saved_dataset_name = :name AND project_id = :project"
            ),
            {"name": "claims", "project": "test_project"},
        ).one()

    loaded = SavedDataset.from_proto(
        SavedDatasetProto.FromString(row.saved_dataset_proto)
    )
    assert row.namespace == loaded.namespace == "underwriting"
    assert row.collection == loaded.collection == "curated"


def test_warn_if_missing_hierarchy_columns_does_not_alter():
    """Read-engine path must inspect/warn only — never ALTER TABLE."""
    fd, registry_path = tempfile.mkstemp()
    engine = create_engine(f"sqlite:///{registry_path}")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE saved_datasets ("
                "saved_dataset_name VARCHAR(255) NOT NULL, "
                "project_id VARCHAR(255) NOT NULL, "
                "last_updated_timestamp BIGINT NOT NULL, "
                "saved_dataset_proto BLOB NOT NULL, "
                "PRIMARY KEY (saved_dataset_name, project_id))"
            )
        )

    SqlRegistry._warn_if_missing_saved_dataset_hierarchy_columns(engine)

    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("saved_datasets")}
    assert "namespace" not in column_names
    assert "collection" not in column_names
