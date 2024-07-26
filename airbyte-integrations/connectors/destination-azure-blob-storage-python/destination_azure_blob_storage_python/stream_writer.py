import json
import logging
from datetime import date, datetime
from decimal import Decimal, getcontext
from typing import Any, Dict, List, Optional, Tuple, Union

import re

import pandas as pd
from airbyte_cdk.models import ConfiguredAirbyteStream, DestinationSyncMode

from .azure import AzureHandler
from .config_reader import ConnectorConfig, PartitionOptions
from .constants import EMPTY_VALUES, TYPE_MAPPING_DOUBLE, PANDAS_TYPE_MAPPING

# By default we set glue decimal type to decimal(28,25)
# this setting matches that precision.
getcontext().prec = 25
logger = logging.getLogger("airbyte")
RE_INT = re.compile(r'^[-+]?[0-9]+$')


class DictEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)

        if isinstance(obj, (pd.Timestamp, datetime)):
            # all timestamps and datetimes are converted to UTC
            return obj.strftime("%Y-%m-%dT%H:%M:%SZ")

        if isinstance(obj, date):
            return obj.strftime("%Y-%m-%d")

        return super(DictEncoder, self).default(obj)


class StreamWriter:
    def __init__(self, azure_handler: AzureHandler, config: ConnectorConfig, configured_stream: ConfiguredAirbyteStream) -> None:
        self._azure_handler: AzureHandler = azure_handler
        self._config: ConnectorConfig = config
        self._configured_stream: ConfiguredAirbyteStream = configured_stream
        self._schema: Dict[str, Any] = configured_stream.stream.json_schema["properties"]
        self._sync_mode: DestinationSyncMode = configured_stream.destination_sync_mode

        self._table_exists: bool = False
        self._table: str = configured_stream.stream.name

        self._messages = []
        self._partial_flush_count = 0

        logger.info(f"Creating StreamWriter")

    def _get_date_columns(self) -> List[str]:
        date_columns = []
        for key, val in self._schema.items():
            typ = val.get("type")
            typ = self._get_json_schema_type(typ)
            if isinstance(typ, str) and typ == "string":
                if val.get("format") in ["date-time", "date"]:
                    date_columns.append(key)
        print("date column", date_columns)
        return date_columns

    def _add_partition_column(self, col: str, df: pd.DataFrame) -> Dict[str, str]:
        partitioning = self._config.partitioning

        if partitioning == PartitionOptions.NONE:
            return {}

        partitions = partitioning.value.split("/")

        fields = {}
        for partition in partitions:
            date_col = f"{col}_{partition.lower()}"
            fields[date_col] = "bigint"

            # defaulting to 0 since both governed tables
            # and pyarrow don't play well with __HIVE_DEFAULT_PARTITION__
            # - pyarrow will fail to cast the column to any other type than string
            # - governed tables will fail when trying to query a table with partitions that have __HIVE_DEFAULT_PARTITION__
            # aside from the above, awswrangler will remove data from a table if the partition value is null
            # see: https://github.com/aws/aws-sdk-pandas/issues/921
            if partition == "YEAR":
                df[date_col] = df[col].dt.strftime("%Y").fillna("0").astype("Int64")

            elif partition == "MONTH":
                df[date_col] = df[col].dt.strftime("%m").fillna("0").astype("Int64")

            elif partition == "DAY":
                df[date_col] = df[col].dt.strftime("%d").fillna("0").astype("Int64")

            elif partition == "DATE":
                fields[date_col] = "date"
                df[date_col] = df[col].dt.strftime("%Y-%m-%d")

        return fields

    def _drop_additional_top_level_properties(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Helper that removes any unexpected top-level properties from the record.
        Since the json schema is used to build the table and cast types correctly,
        we need to remove any unexpected properties that can't be casted accurately.
        """
        schema_keys = self._schema.keys()
        records_keys = record.keys()
        difference = list(set(records_keys).difference(set(schema_keys)))

        for key in difference:
            del record[key]

        return record

    def _json_schema_cast_value(self, value, schema_entry) -> Any:
        typ = schema_entry.get("type")
        typ = self._get_json_schema_type(typ)
        props = schema_entry.get("properties")
        items = schema_entry.get("items")

        if typ == "string":
            format = schema_entry.get("format")
            if format == "date-time":
                return pd.to_datetime(value, errors="coerce", utc=True)

            return str(value) if value and value != "" else None

        elif typ == "integer":
            return pd.to_numeric(value, errors="coerce")

        elif typ == "number":
            return pd.to_numeric(value, errors="coerce")

        elif typ == "boolean":
            return bool(value)

        elif typ == "null":
            return None

        elif typ == "object":
            if value in EMPTY_VALUES:
                return None

            if isinstance(value, dict) and props:
                for key, val in value.items():
                    if key in props:
                        value[key] = self._json_schema_cast_value(val, props[key])
                return value

        elif typ == "array" and items:
            if value in EMPTY_VALUES:
                return None

            if isinstance(value, list):
                return [self._json_schema_cast_value(item, items) for item in value]

        return value

    def _json_schema_cast(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Helper that fixes obvious type violations in a record's top level keys that may
        cause issues when casting data to pyarrow types. Such as:
        - Objects having empty strings or " " or "-" as value instead of null or {}
        - Arrays having empty strings or " " or "-" as value instead of null or []
        """
        for key, schema_type in self._schema.items():
            typ = self._schema[key].get("type")
            typ = self._get_json_schema_type(typ)
            record[key] = self._json_schema_cast_value(record.get(key), schema_type)

        return record

    def _get_non_null_json_schema_types(self, typ: Union[str, List[str]]) -> Union[str, List[str]]:
        if isinstance(typ, list):
            return list(filter(lambda x: x != "null", typ))

        return typ

    def _json_schema_type_has_mixed_types(self, typ: Union[str, List[str]]) -> bool:
        if isinstance(typ, list):
            typ = self._get_non_null_json_schema_types(typ)
            if len(typ) > 1:
                return True

        return False

    def _get_json_schema_type(self, types: Union[List[str], str]) -> str:
        if isinstance(types, str):
            return types

        if not isinstance(types, list):
            return "string"

        types = self._get_non_null_json_schema_types(types)
        # when multiple types, cast to string
        if self._json_schema_type_has_mixed_types(types):
            return "string"

        return types[0]

    def _get_pandas_dtypes_from_json_schema(self, df: pd.DataFrame) -> Dict[str, str]:
        column_types = {}
        typ = "string"

        for col, definition in self._schema.items():
            typ = definition.get("type")
            airbyte_type = definition.get("airbyte_type")
            col_format = definition.get("format")
            typ = self._get_json_schema_type(typ)

            # special case where the json schema type contradicts the airbyte type
            if airbyte_type and typ == "number" and airbyte_type == "integer":
                typ = "integer"

            if typ == "string" and col_format == "date-time":
                typ = "string"

            if typ == "string" and col_format == "date":
                typ = "string"

            if typ == "object":
                typ = "string"

            if typ == "array":
                typ = "string"

            if typ is None:
                typ = "string"

            column_types[col] = PANDAS_TYPE_MAPPING.get(typ, "string")

        return column_types

    def _get_json_schema_types(self) -> Dict[str, str]:
        types = {}
        for key, val in self._schema.items():
            typ = val.get("type")
            types[key] = self._get_json_schema_type(typ)

        return types

    def get_dtypes_from_json_schema(self, schema: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
        """
        Helper that infers glue dtypes from a json schema.
        """
        type_mapper = PANDAS_TYPE_MAPPING #if self._config.glue_catalog_float_as_decimal else GLUE_TYPE_MAPPING_DOUBLE

        column_types = {}
        json_columns = set()
        for col, definition in schema.items():
            result_typ = None
            col_typ = definition.get("type")
            airbyte_type = definition.get("airbyte_type")
            col_format = definition.get("format")

            col_typ = self._get_json_schema_type(col_typ)

            # special case where the json schema type contradicts the airbyte type
            if airbyte_type and col_typ == "number" and airbyte_type == "integer":
                col_typ = "integer"

            if col_typ == "string" and col_format == "date-time":
                result_typ = "datetime"

            if col_typ == "string" and col_format == "date":
                result_typ = "datetime"

            if col_typ == "object":
                json_columns.add(col)
                result_typ = "string"

            if col_typ == "array":
                result_typ = "string"

            if result_typ is None:
                result_typ = type_mapper.get(col_typ, "string")

            column_types[col] = result_typ

        return column_types, json_columns

    @property
    def _cursor_fields(self) -> Optional[List[str]]:
        return self._configured_stream.cursor_field

    def append_message(self, message: Dict[str, Any]):
        clean_message = self._drop_additional_top_level_properties(message)
        clean_message = self._json_schema_cast(clean_message)
        self._messages.append(clean_message)

    def flush(self, partial: bool = False):
        logger.debug(f"Flushing {len(self._messages)} messages")

        df = pd.DataFrame(self._messages)


        print("flushing # of records: ", len(df.index))
        # best effort to convert pandas types

        column_types = self._get_pandas_dtypes_from_json_schema(df)

        for col in column_types:
            if col in df.columns:
                df[col] = df[col].astype(column_types[col])
        #df = df.astype(, errors="ignore")

        if len(df) < 1:
            logger.info(f"No messages to write")
            return

        partition_fields = {}
        date_columns = self._get_date_columns()
        for col in date_columns:
            if col in df.columns:
                #df[col] = pd.to_datetime(df[col], format="mixed", utc=True)

                # Create date column for partitioning
                if self._cursor_fields and col in self._cursor_fields:
                    fields = self._add_partition_column(col, df)
                    partition_fields.update(fields)

        #dtype, json_casts = self.get_dtypes_from_json_schema(self._schema)
        #dtype = {**dtype, **partition_fields}
        #partition_fields = list(partition_fields.keys())
        #print("datatype", dtype)

        # Make sure complex types that can't be converted
        # to a struct or array are converted to a json string
        # so they can be queried with json_extract
        #for col in json_casts:
        #    if col in df.columns:
        #        df[col] = df[col].apply(lambda x: json.dumps(x, cls=DictEncoder))



        #print(df.dtypes)
        for col in range(len(df.columns)):
            column = df.columns[col]
            if column[0].isdigit():
                df.rename(columns={column: "n" + column}, inplace=True)

        #print(df.columns)

        self._azure_handler.write_parquet(df, self._table)

        if partial:
            self._partial_flush_count += 1

        del df
        self._messages.clear()
