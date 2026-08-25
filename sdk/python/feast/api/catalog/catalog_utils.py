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

"""Catalog translation-layer naming (RHAI-384).

Iceberg REST identity is a tuple (project, collection, table). Feast identity is
(project, name) and the catalog uses a single Feast project ``data-registry``,
so SavedDataset.name must carry namespace + collection + display name.

Callers (Iceberg REST, UI, engines) never see the scoped string: prefix on
write / get / delete, strip with unscoped_name on API responses.

Empty collections (RHAI-388): do not store metadata as an unscoped Project tag
``_ns_meta_{collection}`` on the shared data-registry project — that key
collides across RHAI namespaces. Include namespace in any tag key, or derive
collections from SavedDataset.collection filtered by namespace.
"""

from __future__ import annotations

SCOPE_SEP = "/"
MAX_SCOPED_NAME = 255  # saved_datasets.saved_dataset_name VARCHAR(255)


def _require_part(label: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value != value.strip() or any(c.isspace() for c in value):
        raise ValueError(f"{label} must not contain whitespace (got {value!r})")
    if not value:
        raise ValueError(f"{label} must be a non-empty string")
    if SCOPE_SEP in value:
        raise ValueError(f"{label} must not contain {SCOPE_SEP!r} (got {value!r})")
    return value


def _require_namespace(namespace: str) -> str:
    part = _require_part("namespace", namespace)
    if part != part.lower():
        raise ValueError(f"namespace must be lowercase (got {namespace!r})")
    return part


def scoped_name(namespace: str, collection: str, name: str) -> str:
    """Build a unique SavedDataset.name for the shared catalog Feast project.

    Format: ``{namespace}/{collection}/{display_name}``.
    """
    parts = (
        _require_namespace(namespace),
        _require_part("collection", collection),
        _require_part("name", name),
    )
    scoped = SCOPE_SEP.join(parts)
    if len(scoped) > MAX_SCOPED_NAME:
        raise ValueError(
            f"scoped name exceeds {MAX_SCOPED_NAME} characters ({len(scoped)})"
        )
    return scoped


def parse_scoped_name(scoped: str) -> tuple[str, str, str]:
    """Invert scoped_name. Raises ValueError if not exactly three parts."""
    if not isinstance(scoped, str) or not scoped:
        raise ValueError("scoped name must be a non-empty string")
    parts = scoped.split(SCOPE_SEP)
    if len(parts) != 3:
        raise ValueError(
            f"scoped name must be namespace/collection/name (got {scoped!r})"
        )
    return (
        _require_namespace(parts[0]),
        _require_part("collection", parts[1]),
        _require_part("name", parts[2]),
    )


def unscoped_name(scoped: str) -> str:
    """Display name for Iceberg / API JSON — never includes SCOPE_SEP."""
    return parse_scoped_name(scoped)[2]
