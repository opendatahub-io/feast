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

"""Catalog translation helpers plus Iceberg config/error types.

Does not mount routes on RestRegistryServer (RHAI-390). Tests include
``get_config_router()`` on a dedicated FastAPI app.
"""

from feast.api.catalog.catalog_utils import (
    CATALOG_PROJECT,
    DEFAULT_COLLECTION,
    ensure_catalog_project,
    list_namespaces,
    parse_scoped_name,
    resolve_namespace,
    scoped_name,
    unscoped_name,
    validate_namespace_exists,
)
from feast.api.catalog.config import CATALOG_CONFIG_ENDPOINTS, get_config_router
from feast.api.catalog.errors import (
    BadRequestException,
    IcebergRESTException,
    NamespaceAlreadyExistsException,
    NoSuchNamespaceException,
    NoSuchTableException,
    NotImplementedException,
    ServiceFailureException,
    TableAlreadyExistsException,
    missing_required_fields,
    register_error_handlers,
)
from feast.api.catalog.models import DataRegistryConfig, ErrorResponse

__all__ = [
    "CATALOG_CONFIG_ENDPOINTS",
    "CATALOG_PROJECT",
    "DEFAULT_COLLECTION",
    "BadRequestException",
    "DataRegistryConfig",
    "ErrorResponse",
    "IcebergRESTException",
    "NamespaceAlreadyExistsException",
    "NoSuchNamespaceException",
    "NoSuchTableException",
    "NotImplementedException",
    "ServiceFailureException",
    "TableAlreadyExistsException",
    "ensure_catalog_project",
    "get_config_router",
    "list_namespaces",
    "missing_required_fields",
    "parse_scoped_name",
    "register_error_handlers",
    "resolve_namespace",
    "scoped_name",
    "unscoped_name",
    "validate_namespace_exists",
]
