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

"""SavedDataset ↔ Iceberg LoadTableResponse (OpenAPI v0.7.5).

Schema persistence is proto ``SavedDataset.columns``, not ``meta:schema``.
LoadTable is synthetic Iceberg v2 metadata. ``config`` is empty.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from feast.api.catalog.catalog_utils import unscoped_name
from feast.api.catalog.models import (
    IcebergField,
    IcebergSchema,
    LoadTableResponse,
    PartitionSpec,
    SortOrder,
    TableMetadata,
)
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.saved_dataset import SavedDataset, SavedDatasetColumn

ASSET_TYPE_TAG = "asset_type"
CATALOG_MANAGED_TAG = "_catalog_managed"
FORMAT_TAG = "format"
ICEBERG_FORMAT = "iceberg"
TABLE_ASSET = "table"
PROTECTED_TAGS = frozenset({ASSET_TYPE_TAG, CATALOG_MANAGED_TAG, FORMAT_TAG})


def table_uuid(rhai_ns: str, collection: str, name: str) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"feast://{rhai_ns}/{collection}/{name}")
    )


def default_location(
    rhai_ns: str, collection: str, name: str, location: str | None
) -> str:
    if location:
        return location
    return f"feast://{rhai_ns}/{collection}/{name}"


def metadata_location(rhai_ns: str, collection: str, name: str) -> str:
    return f"feast://{rhai_ns}/{collection}/{name}/metadata"


def iceberg_schema_to_columns(schema: IcebergSchema) -> list[SavedDatasetColumn]:
    columns: list[SavedDatasetColumn] = []
    for field in schema.fields:
        columns.append(
            SavedDatasetColumn(
                name=field.name,
                type=field.type,
                description="",
                nullable=not field.required,
            )
        )
    return columns


def columns_to_iceberg_schema(columns: list[SavedDatasetColumn]) -> IcebergSchema:
    fields: list[IcebergField] = []
    for index, column in enumerate(columns, start=1):
        fields.append(
            IcebergField(
                id=index,
                name=column.name,
                required=not column.nullable,
                type=column.type,
            )
        )
    return IcebergSchema(type="struct", schema_id=0, fields=fields)


def catalog_table_tags(properties: dict[str, str]) -> dict[str, str]:
    tags = dict(properties)
    tags[ASSET_TYPE_TAG] = TABLE_ASSET
    tags[CATALOG_MANAGED_TAG] = "true"
    tags[FORMAT_TAG] = ICEBERG_FORMAT
    return tags


def is_iceberg_table(dataset: SavedDataset) -> bool:
    tags = dataset.tags or {}
    if tags.get(ASSET_TYPE_TAG) == "volume":
        return False
    if tags.get(FORMAT_TAG, ICEBERG_FORMAT) != ICEBERG_FORMAT:
        return False
    return tags.get(ASSET_TYPE_TAG, TABLE_ASSET) == TABLE_ASSET


def storage_uri(dataset: SavedDataset) -> str:
    storage = dataset.storage
    if isinstance(storage, SavedDatasetFileStorage):
        return storage.file_options.uri
    return ""


def _last_updated_ms(dataset: SavedDataset) -> int:
    stamp = dataset.last_updated_timestamp or dataset.created_timestamp
    if stamp is None:
        stamp = datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def saved_dataset_to_load_table(
    dataset: SavedDataset, rhai_ns: str, collection: str
) -> LoadTableResponse:
    display = unscoped_name(dataset.name)
    schema = columns_to_iceberg_schema(list(dataset.columns))
    last_column_id = max((field.id for field in schema.fields), default=0)
    metadata = TableMetadata(
        format_version=2,
        table_uuid=table_uuid(rhai_ns, collection, display),
        location=storage_uri(dataset) or default_location(rhai_ns, collection, display, None),
        last_updated_ms=_last_updated_ms(dataset),
        properties=dict(dataset.tags or {}),
        schemas=[schema],
        current_schema_id=0,
        partition_specs=[PartitionSpec()],
        default_spec_id=0,
        sort_orders=[SortOrder()],
        default_sort_order_id=0,
        last_column_id=last_column_id,
        last_sequence_number=0,
        last_partition_id=999,
        snapshots=[],
        current_snapshot_id=-1,
    )
    return LoadTableResponse(
        metadata_location=metadata_location(rhai_ns, collection, display),
        metadata=metadata,
        config={},
    )
