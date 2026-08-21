from google.protobuf import timestamp_pb2 as _timestamp_pb2
from feast.core import DataSource_pb2 as _DataSource_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SavedDatasetSpec(_message.Message):
    __slots__ = ("name", "project", "features", "join_keys", "full_feature_names", "storage", "feature_service_name", "tags", "namespace", "collection", "description", "columns")
    class TagsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    JOIN_KEYS_FIELD_NUMBER: _ClassVar[int]
    FULL_FEATURE_NAMES_FIELD_NUMBER: _ClassVar[int]
    STORAGE_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COLUMNS_FIELD_NUMBER: _ClassVar[int]
    name: str
    project: str
    features: _containers.RepeatedScalarFieldContainer[str]
    join_keys: _containers.RepeatedScalarFieldContainer[str]
    full_feature_names: bool
    storage: SavedDatasetStorage
    feature_service_name: str
    tags: _containers.ScalarMap[str, str]
    namespace: str
    collection: str
    description: str
    columns: _containers.RepeatedCompositeFieldContainer[SavedDatasetColumn]
    def __init__(self, name: _Optional[str] = ..., project: _Optional[str] = ..., features: _Optional[_Iterable[str]] = ..., join_keys: _Optional[_Iterable[str]] = ..., full_feature_names: bool = ..., storage: _Optional[_Union[SavedDatasetStorage, _Mapping]] = ..., feature_service_name: _Optional[str] = ..., tags: _Optional[_Mapping[str, str]] = ..., namespace: _Optional[str] = ..., collection: _Optional[str] = ..., description: _Optional[str] = ..., columns: _Optional[_Iterable[_Union[SavedDatasetColumn, _Mapping]]] = ...) -> None: ...

class SavedDatasetColumn(_message.Message):
    __slots__ = ("name", "type", "description", "nullable")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NULLABLE_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: str
    description: str
    nullable: bool
    def __init__(self, name: _Optional[str] = ..., type: _Optional[str] = ..., description: _Optional[str] = ..., nullable: bool = ...) -> None: ...

class SavedDatasetStorage(_message.Message):
    __slots__ = ("file_storage", "bigquery_storage", "redshift_storage", "snowflake_storage", "trino_storage", "spark_storage", "custom_storage", "athena_storage")
    FILE_STORAGE_FIELD_NUMBER: _ClassVar[int]
    BIGQUERY_STORAGE_FIELD_NUMBER: _ClassVar[int]
    REDSHIFT_STORAGE_FIELD_NUMBER: _ClassVar[int]
    SNOWFLAKE_STORAGE_FIELD_NUMBER: _ClassVar[int]
    TRINO_STORAGE_FIELD_NUMBER: _ClassVar[int]
    SPARK_STORAGE_FIELD_NUMBER: _ClassVar[int]
    CUSTOM_STORAGE_FIELD_NUMBER: _ClassVar[int]
    ATHENA_STORAGE_FIELD_NUMBER: _ClassVar[int]
    file_storage: _DataSource_pb2.DataSource.FileOptions
    bigquery_storage: _DataSource_pb2.DataSource.BigQueryOptions
    redshift_storage: _DataSource_pb2.DataSource.RedshiftOptions
    snowflake_storage: _DataSource_pb2.DataSource.SnowflakeOptions
    trino_storage: _DataSource_pb2.DataSource.TrinoOptions
    spark_storage: _DataSource_pb2.DataSource.SparkOptions
    custom_storage: _DataSource_pb2.DataSource.CustomSourceOptions
    athena_storage: _DataSource_pb2.DataSource.AthenaOptions
    def __init__(self, file_storage: _Optional[_Union[_DataSource_pb2.DataSource.FileOptions, _Mapping]] = ..., bigquery_storage: _Optional[_Union[_DataSource_pb2.DataSource.BigQueryOptions, _Mapping]] = ..., redshift_storage: _Optional[_Union[_DataSource_pb2.DataSource.RedshiftOptions, _Mapping]] = ..., snowflake_storage: _Optional[_Union[_DataSource_pb2.DataSource.SnowflakeOptions, _Mapping]] = ..., trino_storage: _Optional[_Union[_DataSource_pb2.DataSource.TrinoOptions, _Mapping]] = ..., spark_storage: _Optional[_Union[_DataSource_pb2.DataSource.SparkOptions, _Mapping]] = ..., custom_storage: _Optional[_Union[_DataSource_pb2.DataSource.CustomSourceOptions, _Mapping]] = ..., athena_storage: _Optional[_Union[_DataSource_pb2.DataSource.AthenaOptions, _Mapping]] = ...) -> None: ...

class SavedDatasetMeta(_message.Message):
    __slots__ = ("created_timestamp", "last_updated_timestamp", "min_event_timestamp", "max_event_timestamp")
    CREATED_TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    LAST_UPDATED_TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    MIN_EVENT_TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    MAX_EVENT_TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    created_timestamp: _timestamp_pb2.Timestamp
    last_updated_timestamp: _timestamp_pb2.Timestamp
    min_event_timestamp: _timestamp_pb2.Timestamp
    max_event_timestamp: _timestamp_pb2.Timestamp
    def __init__(self, created_timestamp: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., last_updated_timestamp: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., min_event_timestamp: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., max_event_timestamp: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class SavedDataset(_message.Message):
    __slots__ = ("spec", "meta")
    SPEC_FIELD_NUMBER: _ClassVar[int]
    META_FIELD_NUMBER: _ClassVar[int]
    spec: SavedDatasetSpec
    meta: SavedDatasetMeta
    def __init__(self, spec: _Optional[_Union[SavedDatasetSpec, _Mapping]] = ..., meta: _Optional[_Union[SavedDatasetMeta, _Mapping]] = ...) -> None: ...
