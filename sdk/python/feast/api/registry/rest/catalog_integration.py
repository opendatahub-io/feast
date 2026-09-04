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

"""Mount catalog ``/v1`` routes on RestRegistryServer (RHAI-390).

Kept out of ``feast.api.catalog`` so that package stays free of
RestRegistryServer / DATACATALOG_ENABLED identifiers.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from feast.api.catalog.config import get_config_router
from feast.api.catalog.errors import (
    BadRequestException,
    IcebergRESTException,
    validation_message,
)
from feast.api.catalog.generic_tables import get_generic_table_router
from feast.api.catalog.namespaces import get_namespace_router
from feast.api.catalog.tables import get_table_router
from feast.api.catalog.volumes import get_volume_router


def is_datacatalog_enabled() -> bool:
    return os.environ.get("DATACATALOG_ENABLED", "").lower() in ("1", "true", "yes")


def add_catalog_routes(app: FastAPI, registry) -> None:
    """Include Iceberg routers when ``DATACATALOG_ENABLED`` is truthy.

    Iceberg JSON for ``IcebergRESTException`` and for ``/v1`` validation
    errors only. Feast ``/entities`` validation stays 422 ``detail``.
    """
    if not is_datacatalog_enabled():
        return
    app.state.registry = registry
    app.include_router(get_config_router())
    app.include_router(get_namespace_router())
    app.include_router(get_table_router())
    app.include_router(get_volume_router())
    app.include_router(get_generic_table_router())

    @app.exception_handler(IcebergRESTException)
    async def iceberg_rest_exception_handler(
        request: Request, exc: IcebergRESTException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.startswith("/v1"):
            payload = BadRequestException(validation_message(exc)).to_payload()
            return JSONResponse(status_code=400, content=payload)
        return JSONResponse(
            status_code=422,
            content={
                "status_code": 422,
                "detail": str(exc),
                "error_type": "RequestValidationError",
            },
        )
