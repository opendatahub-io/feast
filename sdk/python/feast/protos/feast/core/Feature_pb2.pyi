"""
@generated by mypy-protobuf.  Do not edit manually!
isort:skip_file

Copyright 2020 The Feast Authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import builtins
import collections.abc
import feast.types.Value_pb2
import google.protobuf.descriptor
import google.protobuf.internal.containers
import google.protobuf.message
import sys

if sys.version_info >= (3, 8):
    import typing as typing_extensions
else:
    import typing_extensions

DESCRIPTOR: google.protobuf.descriptor.FileDescriptor

class FeatureSpecV2(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor

    class TagsEntry(google.protobuf.message.Message):
        DESCRIPTOR: google.protobuf.descriptor.Descriptor

        KEY_FIELD_NUMBER: builtins.int
        VALUE_FIELD_NUMBER: builtins.int
        key: builtins.str
        value: builtins.str
        def __init__(
            self,
            *,
            key: builtins.str = ...,
            value: builtins.str = ...,
        ) -> None: ...
        def ClearField(self, field_name: typing_extensions.Literal["key", b"key", "value", b"value"]) -> None: ...

    NAME_FIELD_NUMBER: builtins.int
    VALUE_TYPE_FIELD_NUMBER: builtins.int
    TAGS_FIELD_NUMBER: builtins.int
    DESCRIPTION_FIELD_NUMBER: builtins.int
    VECTOR_INDEX_FIELD_NUMBER: builtins.int
    VECTOR_SEARCH_METRIC_FIELD_NUMBER: builtins.int
    VECTOR_LENGTH_FIELD_NUMBER: builtins.int
    name: builtins.str
    """Name of the feature. Not updatable."""
    value_type: feast.types.Value_pb2.ValueType.Enum.ValueType
    """Value type of the feature. Not updatable."""
    @property
    def tags(self) -> google.protobuf.internal.containers.ScalarMap[builtins.str, builtins.str]:
        """Tags for user defined metadata on a feature"""
    description: builtins.str
    """Description of the feature."""
    vector_index: builtins.bool
    """Field indicating the vector will be indexed for vector similarity search"""
    vector_search_metric: builtins.str
    """Metric used for vector similarity search."""
    vector_length: builtins.int
    """Field indicating the vector length"""
    def __init__(
        self,
        *,
        name: builtins.str = ...,
        value_type: feast.types.Value_pb2.ValueType.Enum.ValueType = ...,
        tags: collections.abc.Mapping[builtins.str, builtins.str] | None = ...,
        description: builtins.str = ...,
        vector_index: builtins.bool = ...,
        vector_search_metric: builtins.str = ...,
        vector_length: builtins.int = ...,
    ) -> None: ...
    def ClearField(self, field_name: typing_extensions.Literal["description", b"description", "name", b"name", "tags", b"tags", "value_type", b"value_type", "vector_index", b"vector_index", "vector_length", b"vector_length", "vector_search_metric", b"vector_search_metric"]) -> None: ...

global___FeatureSpecV2 = FeatureSpecV2
