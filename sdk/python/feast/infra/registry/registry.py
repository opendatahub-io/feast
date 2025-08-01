# Copyright 2019 The Feast Authors
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
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from google.protobuf.internal.containers import RepeatedCompositeFieldContainer
from google.protobuf.message import Message

from feast.base_feature_view import BaseFeatureView
from feast.data_source import DataSource
from feast.entity import Entity
from feast.errors import (
    ConflictingFeatureViewNames,
    DataSourceNotFoundException,
    EntityNotFoundException,
    FeatureServiceNotFoundException,
    FeatureViewNotFoundException,
    PermissionNotFoundException,
    ProjectNotFoundException,
    ProjectObjectNotFoundException,
    ValidationReferenceNotFound,
)
from feast.feature_service import FeatureService
from feast.feature_view import FeatureView
from feast.importer import import_class
from feast.infra.infra_object import Infra
from feast.infra.registry import proto_registry_utils
from feast.infra.registry.base_registry import BaseRegistry
from feast.infra.registry.registry_store import NoopRegistryStore
from feast.on_demand_feature_view import OnDemandFeatureView
from feast.permissions.auth_model import AuthConfig, NoAuthConfig
from feast.permissions.permission import Permission
from feast.project import Project
from feast.project_metadata import ProjectMetadata
from feast.protos.feast.core.Registry_pb2 import Registry as RegistryProto
from feast.repo_config import RegistryConfig
from feast.repo_contents import RepoContents
from feast.saved_dataset import SavedDataset, ValidationReference
from feast.stream_feature_view import StreamFeatureView
from feast.utils import _utc_now

REGISTRY_SCHEMA_VERSION = "1"

REGISTRY_STORE_CLASS_FOR_TYPE = {
    "GCSRegistryStore": "feast.infra.registry.gcs.GCSRegistryStore",
    "S3RegistryStore": "feast.infra.registry.s3.S3RegistryStore",
    "FileRegistryStore": "feast.infra.registry.file.FileRegistryStore",
    "AzureRegistryStore": "feast.infra.registry.contrib.azure.azure_registry_store.AzBlobRegistryStore",
}

REGISTRY_STORE_CLASS_FOR_SCHEME = {
    "gs": "GCSRegistryStore",
    "s3": "S3RegistryStore",
    "file": "FileRegistryStore",
    "": "FileRegistryStore",
}


class FeastObjectType(Enum):
    PROJECT = "project"
    DATA_SOURCE = "data source"
    ENTITY = "entity"
    FEATURE_VIEW = "feature view"
    ON_DEMAND_FEATURE_VIEW = "on demand feature view"
    STREAM_FEATURE_VIEW = "stream feature view"
    FEATURE_SERVICE = "feature service"
    PERMISSION = "permission"

    @staticmethod
    def get_objects_from_registry(
        registry: "BaseRegistry", project: str
    ) -> Dict["FeastObjectType", List[Any]]:
        return {
            FeastObjectType.PROJECT: [
                project_obj
                for project_obj in registry.list_projects()
                if project_obj.name == project
            ],
            FeastObjectType.DATA_SOURCE: registry.list_data_sources(project=project),
            FeastObjectType.ENTITY: registry.list_entities(project=project),
            FeastObjectType.FEATURE_VIEW: registry.list_feature_views(project=project),
            FeastObjectType.ON_DEMAND_FEATURE_VIEW: registry.list_on_demand_feature_views(
                project=project
            ),
            FeastObjectType.STREAM_FEATURE_VIEW: registry.list_stream_feature_views(
                project=project,
            ),
            FeastObjectType.FEATURE_SERVICE: registry.list_feature_services(
                project=project
            ),
            FeastObjectType.PERMISSION: registry.list_permissions(project=project),
        }

    @staticmethod
    def get_objects_from_repo_contents(
        repo_contents: RepoContents,
    ) -> Dict["FeastObjectType", List[Any]]:
        return {
            FeastObjectType.PROJECT: repo_contents.projects,
            FeastObjectType.DATA_SOURCE: repo_contents.data_sources,
            FeastObjectType.ENTITY: repo_contents.entities,
            FeastObjectType.FEATURE_VIEW: repo_contents.feature_views,
            FeastObjectType.ON_DEMAND_FEATURE_VIEW: repo_contents.on_demand_feature_views,
            FeastObjectType.STREAM_FEATURE_VIEW: repo_contents.stream_feature_views,
            FeastObjectType.FEATURE_SERVICE: repo_contents.feature_services,
            FeastObjectType.PERMISSION: repo_contents.permissions,
        }


FEAST_OBJECT_TYPES = [feast_object_type for feast_object_type in FeastObjectType]

logger = logging.getLogger(__name__)


def get_registry_store_class_from_type(registry_store_type: str):
    if not registry_store_type.endswith("RegistryStore"):
        raise Exception('Registry store class name should end with "RegistryStore"')
    if registry_store_type in REGISTRY_STORE_CLASS_FOR_TYPE:
        registry_store_type = REGISTRY_STORE_CLASS_FOR_TYPE[registry_store_type]
    module_name, registry_store_class_name = registry_store_type.rsplit(".", 1)

    return import_class(module_name, registry_store_class_name, "RegistryStore")


def get_registry_store_class_from_scheme(registry_path: str):
    uri = urlparse(registry_path)
    if uri.scheme not in REGISTRY_STORE_CLASS_FOR_SCHEME:
        raise Exception(
            f"Registry path {registry_path} has unsupported scheme {uri.scheme}. "
            f"Supported schemes are file, s3 and gs."
        )
    else:
        registry_store_type = REGISTRY_STORE_CLASS_FOR_SCHEME[uri.scheme]
        return get_registry_store_class_from_type(registry_store_type)


class Registry(BaseRegistry):
    def apply_user_metadata(
        self,
        project: str,
        feature_view: BaseFeatureView,
        metadata_bytes: Optional[bytes],
    ):
        pass

    def get_user_metadata(
        self, project: str, feature_view: BaseFeatureView
    ) -> Optional[bytes]:
        pass

    def set_project_metadata(self, project: str, key: str, value: str):
        """Set a custom project metadata key-value pair in the registry backend."""
        if hasattr(self._registry_store, "set_project_metadata"):
            self._registry_store.set_project_metadata(project, key, value)
        else:
            raise NotImplementedError(
                "set_project_metadata not implemented for this registry backend"
            )

    def get_project_metadata(self, project: str, key: str) -> Optional[str]:
        """Get a custom project metadata value by key from the registry backend."""
        if hasattr(self._registry_store, "get_project_metadata"):
            return self._registry_store.get_project_metadata(project, key)
        else:
            raise NotImplementedError(
                "get_project_metadata not implemented for this registry backend"
            )

    # The cached_registry_proto object is used for both reads and writes. In particular,
    # all write operations refresh the cache and modify it in memory; the write must
    # then be persisted to the underlying RegistryStore with a call to commit().
    cached_registry_proto: RegistryProto
    cached_registry_proto_created: datetime
    cached_registry_proto_ttl: timedelta

    def __init__(
        self,
        project: str,
        registry_config: Optional[RegistryConfig],
        repo_path: Optional[Path],
        auth_config: AuthConfig = NoAuthConfig(),
    ):
        """
        Create the Registry object.

        Args:
            registry_config: RegistryConfig object containing the destination path and cache ttl,
            repo_path: Path to the base of the Feast repository
            or where it will be created if it does not exist yet.
        """

        self._refresh_lock = Lock()
        self._auth_config = auth_config

        registry_proto = RegistryProto()
        registry_proto.registry_schema_version = REGISTRY_SCHEMA_VERSION
        self.cached_registry_proto = registry_proto
        self.cached_registry_proto_created = _utc_now()

        self.purge_feast_metadata = (
            registry_config.purge_feast_metadata
            if registry_config is not None
            else False
        )

        if registry_config:
            registry_store_type = registry_config.registry_store_type
            registry_path = registry_config.path
            if registry_store_type is None:
                cls = get_registry_store_class_from_scheme(registry_path)
            else:
                cls = get_registry_store_class_from_type(str(registry_store_type))

            self._registry_store = cls(registry_config, repo_path)
            self.cached_registry_proto_ttl = timedelta(
                seconds=(
                    registry_config.cache_ttl_seconds
                    if registry_config.cache_ttl_seconds is not None
                    else 0
                )
            )

            try:
                registry_proto = self._registry_store.get_registry_proto()
                self.cached_registry_proto = registry_proto
                self.cached_registry_proto_created = _utc_now()
                # Sync feast_metadata to projects table
                # when purge_feast_metadata is set to True, Delete data from
                # feast_metadata table and list_project_metadata will not return any data
                self._sync_feast_metadata_to_projects_table()
            except FileNotFoundError:
                logger.info("Registry file not found. Creating new registry.")
                self.commit()

    def _sync_feast_metadata_to_projects_table(self):
        """
        Sync feast_metadata to projects table
        """
        feast_metadata_projects = []
        projects_set = []
        # List of project in project_metadata
        for project_metadata in self.cached_registry_proto.project_metadata:
            project = ProjectMetadata.from_proto(project_metadata)
            feast_metadata_projects.append(project.project_name)
        if len(feast_metadata_projects) > 0:
            # List of project in projects
            for project_metadata in self.cached_registry_proto.projects:
                project = Project.from_proto(project_metadata)
                projects_set.append(project.name)

            # Find object in feast_metadata_projects but not in projects
            projects_to_sync = set(feast_metadata_projects) - set(projects_set)
            # Sync feast_metadata to projects table
            for project_name in projects_to_sync:
                project = Project(name=project_name)
                self.cached_registry_proto.projects.append(project.to_proto())

            if self.purge_feast_metadata:
                self.cached_registry_proto.project_metadata = []

    def clone(self) -> "Registry":
        new_registry = Registry("project", None, None, self._auth_config)
        new_registry.cached_registry_proto_ttl = timedelta(seconds=0)
        new_registry.cached_registry_proto = (
            self.cached_registry_proto.__deepcopy__()
            if self.cached_registry_proto
            else RegistryProto()
        )
        new_registry.cached_registry_proto_created = _utc_now()
        new_registry._registry_store = NoopRegistryStore()
        return new_registry

    def update_infra(self, infra: Infra, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        self.cached_registry_proto.infra.CopyFrom(infra.to_proto())
        if commit:
            self.commit()

    def get_infra(self, project: str, allow_cache: bool = False) -> Infra:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return Infra.from_proto(registry_proto.infra)

    def apply_entity(self, entity: Entity, project: str, commit: bool = True):
        entity.is_valid()

        now = _utc_now()
        if not entity.created_timestamp:
            entity.created_timestamp = now
        entity.last_updated_timestamp = now

        entity_proto = entity.to_proto()
        entity_proto.spec.project = project
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, existing_entity_proto in enumerate(
            self.cached_registry_proto.entities
        ):
            if (
                existing_entity_proto.spec.name == entity_proto.spec.name
                and existing_entity_proto.spec.project == project
            ):
                entity.created_timestamp = (
                    existing_entity_proto.meta.created_timestamp.ToDatetime()
                )
                entity_proto = entity.to_proto()
                entity_proto.spec.project = project
                del self.cached_registry_proto.entities[idx]
                break
        self.cached_registry_proto.entities.append(entity_proto)
        if commit:
            self.commit()

    def list_entities(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[Entity]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_entities(registry_proto, project, tags)

    def list_data_sources(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[DataSource]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_data_sources(registry_proto, project, tags)

    def apply_data_source(
        self, data_source: DataSource, project: str, commit: bool = True
    ):
        now = _utc_now()
        if not data_source.created_timestamp:
            data_source.created_timestamp = now
        data_source.last_updated_timestamp = now

        registry = self._prepare_registry_for_changes(project)

        for idx, existing_data_source_proto in enumerate(registry.data_sources):
            if existing_data_source_proto.name == data_source.name:
                existing_data_source = DataSource.from_proto(existing_data_source_proto)
                # Check if the data source has actually changed
                if existing_data_source == data_source:
                    return
                else:
                    # Preserve created_timestamp from existing data source
                    data_source.created_timestamp = (
                        existing_data_source.created_timestamp
                    )
                    del registry.data_sources[idx]
                    break

        data_source_proto = data_source.to_proto()
        data_source_proto.project = project
        data_source_proto.data_source_class_type = (
            f"{data_source.__class__.__module__}.{data_source.__class__.__name__}"
        )
        self.cached_registry_proto.data_sources.append(data_source_proto)
        if commit:
            self.commit()

    def delete_data_source(self, name: str, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, data_source_proto in enumerate(
            self.cached_registry_proto.data_sources
        ):
            if data_source_proto.name == name:
                del self.cached_registry_proto.data_sources[idx]
                if commit:
                    self.commit()
                return
        raise DataSourceNotFoundException(name)

    def apply_feature_service(
        self, feature_service: FeatureService, project: str, commit: bool = True
    ):
        now = _utc_now()
        if not feature_service.created_timestamp:
            feature_service.created_timestamp = now
        feature_service.last_updated_timestamp = now

        feature_service_proto = feature_service.to_proto()
        feature_service_proto.spec.project = project

        registry = self._prepare_registry_for_changes(project)

        for idx, existing_feature_service_proto in enumerate(registry.feature_services):
            if (
                existing_feature_service_proto.spec.name
                == feature_service_proto.spec.name
                and existing_feature_service_proto.spec.project == project
            ):
                feature_service.created_timestamp = (
                    existing_feature_service_proto.meta.created_timestamp.ToDatetime()
                )
                feature_service_proto = feature_service.to_proto()
                feature_service_proto.spec.project = project
                del registry.feature_services[idx]
        self.cached_registry_proto.feature_services.append(feature_service_proto)
        if commit:
            self.commit()

    def list_feature_services(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[FeatureService]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_feature_services(registry_proto, project, tags)

    def get_feature_service(
        self, name: str, project: str, allow_cache: bool = False
    ) -> FeatureService:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_feature_service(registry_proto, name, project)

    def get_entity(self, name: str, project: str, allow_cache: bool = False) -> Entity:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_entity(registry_proto, name, project)

    def apply_feature_view(
        self, feature_view: BaseFeatureView, project: str, commit: bool = True
    ):
        feature_view.ensure_valid()

        now = _utc_now()
        if not feature_view.created_timestamp:
            feature_view.created_timestamp = now
        feature_view.last_updated_timestamp = now

        feature_view_proto = feature_view.to_proto()
        feature_view_proto.spec.project = project
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        self._check_conflicting_feature_view_names(feature_view)
        existing_feature_views_of_same_type: RepeatedCompositeFieldContainer
        if isinstance(feature_view, StreamFeatureView):
            existing_feature_views_of_same_type = (
                self.cached_registry_proto.stream_feature_views
            )
        elif isinstance(feature_view, FeatureView):
            existing_feature_views_of_same_type = (
                self.cached_registry_proto.feature_views
            )
        elif isinstance(feature_view, OnDemandFeatureView):
            existing_feature_views_of_same_type = (
                self.cached_registry_proto.on_demand_feature_views
            )
        else:
            raise ValueError(f"Unexpected feature view type: {type(feature_view)}")

        for idx, existing_feature_view_proto in enumerate(
            existing_feature_views_of_same_type
        ):
            if (
                existing_feature_view_proto.spec.name == feature_view_proto.spec.name
                and existing_feature_view_proto.spec.project == project
            ):
                if (
                    feature_view.__class__.from_proto(existing_feature_view_proto)
                    == feature_view
                ):
                    return
                else:
                    existing_feature_view = type(feature_view).from_proto(
                        existing_feature_view_proto
                    )
                    feature_view.created_timestamp = (
                        existing_feature_view.created_timestamp
                    )
                    if isinstance(feature_view, (FeatureView, StreamFeatureView)):
                        feature_view.update_materialization_intervals(
                            existing_feature_view.materialization_intervals
                        )
                    feature_view_proto = feature_view.to_proto()
                    feature_view_proto.spec.project = project
                    del existing_feature_views_of_same_type[idx]
                    break

        existing_feature_views_of_same_type.append(feature_view_proto)
        if commit:
            self.commit()

    def list_stream_feature_views(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[StreamFeatureView]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_stream_feature_views(
            registry_proto, project, tags
        )

    def list_on_demand_feature_views(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[OnDemandFeatureView]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_on_demand_feature_views(
            registry_proto, project, tags
        )

    def get_on_demand_feature_view(
        self, name: str, project: str, allow_cache: bool = False
    ) -> OnDemandFeatureView:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_on_demand_feature_view(
            registry_proto, name, project
        )

    def get_data_source(
        self, name: str, project: str, allow_cache: bool = False
    ) -> DataSource:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_data_source(registry_proto, name, project)

    def apply_materialization(
        self,
        feature_view: Union[FeatureView, OnDemandFeatureView],
        project: str,
        start_date: datetime,
        end_date: datetime,
        commit: bool = True,
    ):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, existing_feature_view_proto in enumerate(
            self.cached_registry_proto.feature_views
        ):
            if (
                existing_feature_view_proto.spec.name == feature_view.name
                and existing_feature_view_proto.spec.project == project
            ):
                existing_feature_view = FeatureView.from_proto(
                    existing_feature_view_proto
                )
                existing_feature_view.materialization_intervals.append(
                    (start_date, end_date)
                )
                existing_feature_view.last_updated_timestamp = _utc_now()
                feature_view_proto = existing_feature_view.to_proto()
                feature_view_proto.spec.project = project
                del self.cached_registry_proto.feature_views[idx]
                self.cached_registry_proto.feature_views.append(feature_view_proto)
                if commit:
                    self.commit()
                return

        for idx, existing_stream_feature_view_proto in enumerate(
            self.cached_registry_proto.stream_feature_views
        ):
            if (
                existing_stream_feature_view_proto.spec.name == feature_view.name
                and existing_stream_feature_view_proto.spec.project == project
            ):
                existing_stream_feature_view = StreamFeatureView.from_proto(
                    existing_stream_feature_view_proto
                )
                existing_stream_feature_view.materialization_intervals.append(
                    (start_date, end_date)
                )
                existing_stream_feature_view.last_updated_timestamp = _utc_now()
                stream_feature_view_proto = existing_stream_feature_view.to_proto()
                stream_feature_view_proto.spec.project = project
                del self.cached_registry_proto.stream_feature_views[idx]
                self.cached_registry_proto.stream_feature_views.append(
                    stream_feature_view_proto
                )
                if commit:
                    self.commit()
                return

    def list_all_feature_views(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[BaseFeatureView]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_all_feature_views(
            registry_proto, project, tags
        )

    def get_any_feature_view(
        self, name: str, project: str, allow_cache: bool = False
    ) -> BaseFeatureView:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_any_feature_view(registry_proto, name, project)

    def list_feature_views(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[FeatureView]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_feature_views(registry_proto, project, tags)

    def get_feature_view(
        self, name: str, project: str, allow_cache: bool = False
    ) -> FeatureView:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_feature_view(registry_proto, name, project)

    def get_stream_feature_view(
        self, name: str, project: str, allow_cache: bool = False
    ) -> StreamFeatureView:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_stream_feature_view(
            registry_proto, name, project
        )

    def delete_feature_service(self, name: str, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, feature_service_proto in enumerate(
            self.cached_registry_proto.feature_services
        ):
            if (
                feature_service_proto.spec.name == name
                and feature_service_proto.spec.project == project
            ):
                del self.cached_registry_proto.feature_services[idx]
                if commit:
                    self.commit()
                return
        raise FeatureServiceNotFoundException(name, project)

    def delete_feature_view(self, name: str, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, existing_feature_view_proto in enumerate(
            self.cached_registry_proto.feature_views
        ):
            if (
                existing_feature_view_proto.spec.name == name
                and existing_feature_view_proto.spec.project == project
            ):
                del self.cached_registry_proto.feature_views[idx]
                if commit:
                    self.commit()
                return

        for idx, existing_on_demand_feature_view_proto in enumerate(
            self.cached_registry_proto.on_demand_feature_views
        ):
            if (
                existing_on_demand_feature_view_proto.spec.name == name
                and existing_on_demand_feature_view_proto.spec.project == project
            ):
                del self.cached_registry_proto.on_demand_feature_views[idx]
                if commit:
                    self.commit()
                return

        for idx, existing_stream_feature_view_proto in enumerate(
            self.cached_registry_proto.stream_feature_views
        ):
            if (
                existing_stream_feature_view_proto.spec.name == name
                and existing_stream_feature_view_proto.spec.project == project
            ):
                del self.cached_registry_proto.stream_feature_views[idx]
                if commit:
                    self.commit()
                return

        raise FeatureViewNotFoundException(name, project)

    def delete_entity(self, name: str, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, existing_entity_proto in enumerate(
            self.cached_registry_proto.entities
        ):
            if (
                existing_entity_proto.spec.name == name
                and existing_entity_proto.spec.project == project
            ):
                del self.cached_registry_proto.entities[idx]
                if commit:
                    self.commit()
                return

        raise EntityNotFoundException(name, project)

    def apply_saved_dataset(
        self,
        saved_dataset: SavedDataset,
        project: str,
        commit: bool = True,
    ):
        now = _utc_now()
        if not saved_dataset.created_timestamp:
            saved_dataset.created_timestamp = now
        saved_dataset.last_updated_timestamp = now

        saved_dataset_proto = saved_dataset.to_proto()
        saved_dataset_proto.spec.project = project
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, existing_saved_dataset_proto in enumerate(
            self.cached_registry_proto.saved_datasets
        ):
            if (
                existing_saved_dataset_proto.spec.name == saved_dataset_proto.spec.name
                and existing_saved_dataset_proto.spec.project == project
            ):
                saved_dataset.created_timestamp = (
                    existing_saved_dataset_proto.meta.created_timestamp.ToDatetime()
                )
                saved_dataset.min_event_timestamp = (
                    existing_saved_dataset_proto.meta.min_event_timestamp.ToDatetime()
                )
                saved_dataset.max_event_timestamp = (
                    existing_saved_dataset_proto.meta.max_event_timestamp.ToDatetime()
                )
                saved_dataset_proto = saved_dataset.to_proto()
                saved_dataset_proto.spec.project = project
                del self.cached_registry_proto.saved_datasets[idx]
                break

        self.cached_registry_proto.saved_datasets.append(saved_dataset_proto)
        if commit:
            self.commit()

    def get_saved_dataset(
        self, name: str, project: str, allow_cache: bool = False
    ) -> SavedDataset:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_saved_dataset(registry_proto, name, project)

    def list_saved_datasets(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[SavedDataset]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_saved_datasets(registry_proto, project, tags)

    def apply_validation_reference(
        self,
        validation_reference: ValidationReference,
        project: str,
        commit: bool = True,
    ):
        validation_reference_proto = validation_reference.to_proto()
        validation_reference_proto.project = project

        registry_proto = self._prepare_registry_for_changes(project)
        for idx, existing_validation_reference in enumerate(
            registry_proto.validation_references
        ):
            if (
                existing_validation_reference.name == validation_reference_proto.name
                and existing_validation_reference.project == project
            ):
                del registry_proto.validation_references[idx]
                break

        registry_proto.validation_references.append(validation_reference_proto)
        if commit:
            self.commit()

    def get_validation_reference(
        self, name: str, project: str, allow_cache: bool = False
    ) -> ValidationReference:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_validation_reference(
            registry_proto, name, project
        )

    def list_validation_references(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[ValidationReference]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_validation_references(
            registry_proto, project, tags
        )

    def delete_validation_reference(self, name: str, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto
        for idx, existing_validation_reference in enumerate(
            self.cached_registry_proto.validation_references
        ):
            if (
                existing_validation_reference.name == name
                and existing_validation_reference.project == project
            ):
                del self.cached_registry_proto.validation_references[idx]
                if commit:
                    self.commit()
                return
        raise ValidationReferenceNotFound(name, project=project)

    def list_project_metadata(
        self, project: str, allow_cache: bool = False
    ) -> List[ProjectMetadata]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_project_metadata(registry_proto, project)

    def commit(self):
        """Commits the state of the registry cache to the remote registry store."""
        if self.cached_registry_proto:
            self._registry_store.update_registry_proto(self.cached_registry_proto)

    def refresh(self, project: Optional[str] = None):
        """Refreshes the state of the registry cache by fetching the registry state from the remote registry store."""
        self._get_registry_proto(project=project, allow_cache=False)

    def teardown(self):
        """Tears down (removes) the registry."""
        self._registry_store.teardown()

    def proto(self) -> RegistryProto:
        return self.cached_registry_proto or RegistryProto()

    def _prepare_registry_for_changes(self, project_name: str):
        """Prepares the Registry for changes by refreshing the cache if necessary."""

        assert self.cached_registry_proto is not None

        try:
            # Check if the project exists in the registry cache
            self.get_project(name=project_name, allow_cache=True)
            return self.cached_registry_proto
        except ProjectObjectNotFoundException:
            # If the project does not exist in cache, refresh cache from store
            registry_proto = self._registry_store.get_registry_proto()
            self.cached_registry_proto = registry_proto
            self.cached_registry_proto_created = _utc_now()

        try:
            # Check if the project exists in the registry cache after refresh from store
            self.get_project(name=project_name)
        except ProjectObjectNotFoundException:
            # If the project still does not exist, create it
            project_proto = Project(name=project_name).to_proto()
            self.cached_registry_proto.projects.append(project_proto)
            if not self.purge_feast_metadata:
                project_metadata_proto = ProjectMetadata(
                    project_name=project_name
                ).to_proto()
                self.cached_registry_proto.project_metadata.append(
                    project_metadata_proto
                )
            self.commit()
        return self.cached_registry_proto

    def _get_registry_proto(
        self, project: Optional[str], allow_cache: bool = False
    ) -> RegistryProto:
        """Returns the cached or remote registry state

        Args:
            project: Name of the Feast project (optional)
            allow_cache: Whether to allow the use of the registry cache when fetching the RegistryProto

        Returns: Returns a RegistryProto object which represents the state of the registry
        """
        with self._refresh_lock:
            expired = (self.cached_registry_proto_created is None) or (
                self.cached_registry_proto_ttl.total_seconds()
                > 0  # 0 ttl means infinity
                and (
                    _utc_now()
                    > (
                        self.cached_registry_proto_created
                        + self.cached_registry_proto_ttl
                    )
                )
            )

            if allow_cache and not expired:
                return self.cached_registry_proto
            logger.info("Registry cache expired, so refreshing")
            registry_proto = self._registry_store.get_registry_proto()
            self.cached_registry_proto = registry_proto
            self.cached_registry_proto_created = _utc_now()
            return registry_proto

    def _check_conflicting_feature_view_names(self, feature_view: BaseFeatureView):
        name_to_fv_protos = self._existing_feature_view_names_to_fvs()
        if feature_view.name in name_to_fv_protos:
            if not isinstance(
                name_to_fv_protos.get(feature_view.name), feature_view.proto_class
            ):
                raise ConflictingFeatureViewNames(feature_view.name)

    def _existing_feature_view_names_to_fvs(self) -> Dict[str, Message]:
        assert self.cached_registry_proto
        odfvs = {
            fv.spec.name: fv
            for fv in self.cached_registry_proto.on_demand_feature_views
        }
        fvs = {fv.spec.name: fv for fv in self.cached_registry_proto.feature_views}
        sfv = {
            fv.spec.name: fv for fv in self.cached_registry_proto.stream_feature_views
        }
        return {**odfvs, **fvs, **sfv}

    def get_permission(
        self, name: str, project: str, allow_cache: bool = False
    ) -> Permission:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.get_permission(registry_proto, name, project)

    def list_permissions(
        self,
        project: str,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[Permission]:
        registry_proto = self._get_registry_proto(
            project=project, allow_cache=allow_cache
        )
        return proto_registry_utils.list_permissions(registry_proto, project, tags)

    def apply_permission(
        self, permission: Permission, project: str, commit: bool = True
    ):
        now = _utc_now()
        if not permission.created_timestamp:
            permission.created_timestamp = now
        permission.last_updated_timestamp = now

        registry = self._prepare_registry_for_changes(project)
        for idx, existing_permission_proto in enumerate(registry.permissions):
            if (
                existing_permission_proto.spec.name == permission.name
                and existing_permission_proto.spec.project == project
            ):
                permission.created_timestamp = (
                    existing_permission_proto.meta.created_timestamp.ToDatetime()
                )
                del registry.permissions[idx]

        permission_proto = permission.to_proto()
        permission_proto.spec.project = project
        self.cached_registry_proto.permissions.append(permission_proto)
        if commit:
            self.commit()

    def delete_permission(self, name: str, project: str, commit: bool = True):
        self._prepare_registry_for_changes(project)
        assert self.cached_registry_proto

        for idx, permission_proto in enumerate(self.cached_registry_proto.permissions):
            if (
                permission_proto.spec.name == name
                and permission_proto.spec.project == project
            ):
                del self.cached_registry_proto.permissions[idx]
                if commit:
                    self.commit()
                return
        raise PermissionNotFoundException(name, project)

    def apply_project(
        self,
        project: Project,
        commit: bool = True,
    ):
        registry = self.cached_registry_proto

        for idx, existing_project_proto in enumerate(registry.projects):
            if existing_project_proto.spec.name == project.name:
                project.created_timestamp = (
                    existing_project_proto.meta.created_timestamp.ToDatetime().replace(
                        tzinfo=timezone.utc
                    )
                )
                del registry.projects[idx]

        project_proto = project.to_proto()
        self.cached_registry_proto.projects.append(project_proto)
        if commit:
            self.commit()

    def get_project(
        self,
        name: str,
        allow_cache: bool = False,
    ) -> Project:
        registry_proto = self._get_registry_proto(project=name, allow_cache=allow_cache)
        return proto_registry_utils.get_project(registry_proto, name)

    def list_projects(
        self,
        allow_cache: bool = False,
        tags: Optional[dict[str, str]] = None,
    ) -> List[Project]:
        registry_proto = self._get_registry_proto(project=None, allow_cache=allow_cache)
        return proto_registry_utils.list_projects(
            registry_proto=registry_proto, tags=tags
        )

    def delete_project(
        self,
        name: str,
        commit: bool = True,
    ):
        assert self.cached_registry_proto

        for idx, project_proto in enumerate(self.cached_registry_proto.projects):
            if project_proto.spec.name == name:
                list_validation_references = self.list_validation_references(name)
                for validation_reference in list_validation_references:
                    self.delete_validation_reference(validation_reference.name, name)

                list_saved_datasets = self.list_saved_datasets(name)
                for saved_dataset in list_saved_datasets:
                    self.delete_saved_dataset(saved_dataset.name, name)

                list_feature_services = self.list_feature_services(name)
                for feature_service in list_feature_services:
                    self.delete_feature_service(feature_service.name, name)

                list_on_demand_feature_views = self.list_on_demand_feature_views(name)
                for on_demand_feature_view in list_on_demand_feature_views:
                    self.delete_feature_view(on_demand_feature_view.name, name)

                list_stream_feature_views = self.list_stream_feature_views(name)
                for stream_feature_view in list_stream_feature_views:
                    self.delete_feature_view(stream_feature_view.name, name)

                list_feature_views = self.list_feature_views(name)
                for feature_view in list_feature_views:
                    self.delete_feature_view(feature_view.name, name)

                list_data_sources = self.list_data_sources(name)
                for data_source in list_data_sources:
                    self.delete_data_source(data_source.name, name)

                list_entities = self.list_entities(name)
                for entity in list_entities:
                    self.delete_entity(entity.name, name)
                list_permissions = self.list_permissions(name)
                for permission in list_permissions:
                    self.delete_permission(permission.name, name)
                del self.cached_registry_proto.projects[idx]
                if commit:
                    self.commit()
                return
        raise ProjectNotFoundException(name)
