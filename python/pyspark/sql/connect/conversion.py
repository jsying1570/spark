#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime

import pyarrow as pa

from pyspark.sql.types import (
    Row,
    DataType,
    TimestampType,
    TimestampNTZType,
    MapType,
    StructType,
    ArrayType,
    BinaryType,
    NullType,
)

from pyspark.sql.connect.types import to_arrow_schema

from typing import (
    Any,
    Callable,
    Sequence,
)


class LocalDataToArrowConversion:
    """
    Conversion from local data (except pandas DataFrame and numpy ndarray) to Arrow.
    Currently, only :class:`SparkSession` in Spark Connect can use this class.
    """

    @staticmethod
    def _need_converter(dataType: DataType) -> bool:
        if isinstance(dataType, NullType):
            return True
        elif isinstance(dataType, StructType):
            # Struct maybe rows, should convert to dict.
            return True
        elif isinstance(dataType, ArrayType):
            return LocalDataToArrowConversion._need_converter(dataType.elementType)
        elif isinstance(dataType, MapType):
            # Different from PySpark, here always needs conversion,
            # since an Arrow Map requires a list of tuples.
            return True
        elif isinstance(dataType, BinaryType):
            return True
        elif isinstance(dataType, (TimestampType, TimestampNTZType)):
            # Always truncate
            return True
        else:
            return False

    @staticmethod
    def _create_converter(dataType: DataType) -> Callable:
        assert dataType is not None and isinstance(dataType, DataType)

        if not LocalDataToArrowConversion._need_converter(dataType):
            return lambda value: value

        if isinstance(dataType, NullType):
            return lambda value: None

        elif isinstance(dataType, StructType):

            field_names = dataType.fieldNames()

            field_convs = {
                field.name: LocalDataToArrowConversion._create_converter(field.dataType)
                for field in dataType.fields
            }

            def convert_struct(value: Any) -> Any:
                if value is None:
                    return None
                else:
                    assert isinstance(value, (Row, dict)), f"{type(value)} {value}"

                    _dict = {}
                    if isinstance(value, dict):
                        for k, v in value.items():
                            assert isinstance(k, str)
                            _dict[k] = field_convs[k](v)
                    elif isinstance(value, Row) and hasattr(value, "__fields__"):
                        for k, v in value.asDict(recursive=False).items():
                            assert isinstance(k, str)
                            _dict[k] = field_convs[k](v)
                    else:
                        i = 0
                        for v in value:
                            field_name = field_names[i]
                            _dict[field_name] = field_convs[field_name](v)
                            i += 1

                    return _dict

            return convert_struct

        elif isinstance(dataType, ArrayType):

            element_conv = LocalDataToArrowConversion._create_converter(dataType.elementType)

            def convert_array(value: Any) -> Any:
                if value is None:
                    return None
                else:
                    assert isinstance(value, list)
                    return [element_conv(v) for v in value]

            return convert_array

        elif isinstance(dataType, MapType):

            key_conv = LocalDataToArrowConversion._create_converter(dataType.keyType)
            value_conv = LocalDataToArrowConversion._create_converter(dataType.valueType)

            def convert_map(value: Any) -> Any:
                if value is None:
                    return None
                else:
                    assert isinstance(value, dict)

                    _tuples = []
                    for k, v in value.items():
                        _tuples.append((key_conv(k), value_conv(v)))

                    return _tuples

            return convert_map

        elif isinstance(dataType, BinaryType):

            def convert_binary(value: Any) -> Any:
                if value is None:
                    return None
                else:
                    assert isinstance(value, (bytes, bytearray))
                    return bytes(value)

            return convert_binary

        elif isinstance(dataType, (TimestampType, TimestampNTZType)):

            def convert_timestample(value: Any) -> Any:
                if value is None:
                    return None
                else:
                    assert isinstance(value, datetime.datetime)
                    return value.astimezone(datetime.timezone.utc)

            return convert_timestample

        else:

            return lambda value: value

    @staticmethod
    def convert(data: Sequence[Any], schema: StructType) -> "pa.Table":
        assert isinstance(data, list) and len(data) > 0

        assert schema is not None and isinstance(schema, StructType)

        pa_schema = to_arrow_schema(schema)

        column_names = schema.fieldNames()

        column_convs = {
            field.name: LocalDataToArrowConversion._create_converter(field.dataType)
            for field in schema.fields
        }

        pylist = []

        for item in data:
            _dict = {}

            if isinstance(item, dict):
                for col, value in item.items():
                    _dict[col] = column_convs[col](value)
            elif isinstance(item, Row) and hasattr(item, "__fields__"):
                for col, value in item.asDict(recursive=False).items():
                    _dict[col] = column_convs[col](value)
            else:
                i = 0
                for value in item:
                    col = column_names[i]
                    _dict[col] = column_convs[col](value)
                    i += 1

            pylist.append(_dict)

        return pa.Table.from_pylist(pylist, schema=pa_schema)
