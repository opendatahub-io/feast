"""Unit tests for Milvus online store varchar_max_length configuration."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from feast import Entity, FeatureView
from feast.field import Field
from feast.infra.online_stores.milvus_online_store.milvus import (
    MilvusOnlineStore,
    MilvusOnlineStoreConfig,
)
from feast.types import Float32, String
from feast.value_type import ValueType


def _make_feature_view(name="driver_stats"):
    entity = Entity(
        name="driver_id",
        join_keys=["driver_id"],
        value_type=ValueType.INT64,
    )
    fv = FeatureView(
        name=name,
        entities=[entity],
        ttl=timedelta(days=1),
        schema=[
            Field(name="trips_today", dtype=Float32),
            Field(name="wiki_summary", dtype=String),
        ],
    )
    return fv


def _make_config(project="test_project", varchar_max_length=None):
    config = MagicMock()
    config.project = project
    config.entity_key_serialization_version = 2
    config.registry.enable_online_feature_view_versioning = False

    online_store_config = MilvusOnlineStoreConfig()
    if varchar_max_length is not None:
        online_store_config.varchar_max_length = varchar_max_length
    config.online_store = online_store_config
    config.provider = "local"
    config.repo_path = None
    return config


class TestMilvusOnlineStoreConfigDefaults:
    """Test MilvusOnlineStoreConfig default values."""

    def test_default_varchar_max_length(self):
        cfg = MilvusOnlineStoreConfig()
        assert cfg.varchar_max_length == 65535

    def test_custom_varchar_max_length(self):
        cfg = MilvusOnlineStoreConfig(varchar_max_length=1024)
        assert cfg.varchar_max_length == 1024


class TestVarcharMaxLengthInCollectionCreation:
    """Test that varchar_max_length is used when creating Milvus collections."""

    @patch(
        "feast.infra.online_stores.milvus_online_store.milvus.MilvusClient",
        autospec=True,
    )
    def test_default_varchar_max_length_in_schema(self, mock_milvus_client_cls):
        """Collection fields should use the default varchar_max_length (65535)."""
        mock_client = MagicMock()
        mock_milvus_client_cls.return_value = mock_client
        mock_client.has_collection.return_value = False

        store = MilvusOnlineStore()
        config = _make_config()
        fv = _make_feature_view()

        store.client = mock_client
        store._get_or_create_collection(config, fv)

        # Verify create_collection was called
        assert mock_client.create_collection.called
        call_kwargs = mock_client.create_collection.call_args
        schema = call_kwargs.kwargs.get("schema") or call_kwargs[1].get("schema")

        # Check all VARCHAR fields use varchar_max_length=65535
        for field in schema.fields:
            if hasattr(field, "max_length") and field.max_length is not None:
                assert field.max_length == 65535, (
                    f"Field '{field.name}' has max_length={field.max_length}, expected 65535"
                )

    @patch(
        "feast.infra.online_stores.milvus_online_store.milvus.MilvusClient",
        autospec=True,
    )
    def test_custom_varchar_max_length_in_schema(self, mock_milvus_client_cls):
        """Collection fields should use a custom varchar_max_length when configured."""
        mock_client = MagicMock()
        mock_milvus_client_cls.return_value = mock_client
        mock_client.has_collection.return_value = False

        store = MilvusOnlineStore()
        config = _make_config(varchar_max_length=4096)
        fv = _make_feature_view(name="driver_stats_custom")

        store.client = mock_client
        store._get_or_create_collection(config, fv)

        # Verify create_collection was called
        assert mock_client.create_collection.called
        call_kwargs = mock_client.create_collection.call_args
        schema = call_kwargs.kwargs.get("schema") or call_kwargs[1].get("schema")

        # Check all VARCHAR fields use varchar_max_length=4096
        for field in schema.fields:
            if hasattr(field, "max_length") and field.max_length is not None:
                assert field.max_length == 4096, (
                    f"Field '{field.name}' has max_length={field.max_length}, expected 4096"
                )
