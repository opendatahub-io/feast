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

"""Pydantic models for Iceberg REST JSON (OpenAPI DataRegistryConfig / ErrorResponse)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IcebergError(BaseModel):
    message: str
    type: str
    code: int


class ErrorResponse(BaseModel):
    error: IcebergError


class DataRegistryConfig(BaseModel):
    defaults: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, str] = Field(default_factory=dict)
    endpoints: list[str] = Field(default_factory=list)


class CreateNamespaceRequest(BaseModel):
    namespace: list[str]
    properties: dict[str, str] = Field(default_factory=dict)


class NamespaceResponse(BaseModel):
    namespace: list[str]
    properties: dict[str, str] = Field(default_factory=dict)


class ListNamespacesResponse(BaseModel):
    namespaces: list[list[str]]


class UpdateNamespacePropertiesRequest(BaseModel):
    updates: dict[str, str] = Field(default_factory=dict)
    removals: list[str] = Field(default_factory=list)


class UpdateNamespacePropertiesResponse(BaseModel):
    updated: list[str]
    removed: list[str]
    missing: list[str]


class TableIdentifier(BaseModel):
    namespace: list[str]
    name: str


class IcebergField(BaseModel):
    id: int
    name: str
    required: bool
    type: str


class IcebergSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = "struct"
    schema_id: int = Field(default=0, alias="schema-id")
    fields: list[IcebergField] = Field(default_factory=list)


class PartitionSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    spec_id: int = Field(default=0, alias="spec-id")
    fields: list = Field(default_factory=list)


class SortOrder(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    order_id: int = Field(default=0, alias="order-id")
    fields: list = Field(default_factory=list)


class TableMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format_version: int = Field(default=2, alias="format-version")
    table_uuid: str = Field(alias="table-uuid")
    location: str
    last_updated_ms: int = Field(alias="last-updated-ms")
    properties: dict[str, str] = Field(default_factory=dict)
    schemas: list[IcebergSchema]
    current_schema_id: int = Field(default=0, alias="current-schema-id")
    partition_specs: list[PartitionSpec] = Field(
        default_factory=lambda: [PartitionSpec()], alias="partition-specs"
    )
    default_spec_id: int = Field(default=0, alias="default-spec-id")
    sort_orders: list[SortOrder] = Field(
        default_factory=lambda: [SortOrder()], alias="sort-orders"
    )
    default_sort_order_id: int = Field(default=0, alias="default-sort-order-id")
    last_column_id: int = Field(default=0, alias="last-column-id")
    last_sequence_number: int = Field(default=0, alias="last-sequence-number")
    last_partition_id: int = Field(default=999, alias="last-partition-id")
    snapshots: list = Field(default_factory=list)
    current_snapshot_id: int = Field(default=-1, alias="current-snapshot-id")


class LoadTableResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metadata_location: str = Field(alias="metadata-location")
    metadata: TableMetadata
    config: dict[str, str] = Field(default_factory=dict)


class CreateTableRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    schema_: IcebergSchema = Field(alias="schema")
    location: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    partition_spec: PartitionSpec | None = Field(default=None, alias="partition-spec")
    sort_order: SortOrder | None = Field(default=None, alias="sort-order")


class ListTablesResponse(BaseModel):
    identifiers: list[TableIdentifier]


class TableUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action: str
    updates: dict[str, str] = Field(default_factory=dict)
    removals: list[str] = Field(default_factory=list)
    schema_: IcebergSchema | None = Field(default=None, alias="schema")


class TableRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    ref: str | None = None
    uuid: str | None = None
    last_assigned_field_id: int | None = Field(
        default=None, alias="last-assigned-field-id"
    )


class UpdateTableRequest(BaseModel):
    requirements: list[TableRequirement] = Field(default_factory=list)
    updates: list[TableUpdate]


class RenameTableRequest(BaseModel):
    source: TableIdentifier
    destination: TableIdentifier
