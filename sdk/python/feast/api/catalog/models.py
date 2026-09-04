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

from pydantic import BaseModel, Field


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
