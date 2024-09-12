import json
import logging
from datetime import date, datetime
from decimal import Decimal, getcontext
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from airbyte_cdk.models import ConfiguredAirbyteStream, DestinationSyncMode

from .azure import AzureHandler
from .config_reader import ConnectorConfig, PartitionOptions
from .constants import EMPTY_VALUES

# By default we set glue decimal type to decimal(28,25)
# this setting matches that precision.
getcontext().prec = 25
logger = logging.getLogger("airbyte")


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

    @property
    def _cursor_fields(self) -> Optional[List[str]]:
        return self._configured_stream.cursor_field

    def append_message(self, message: Dict[str, Any]):
        clean_message = self._drop_additional_top_level_properties(message)
        clean_message = self._json_schema_cast(clean_message)
        self._messages.append(clean_message)

    def flush(self, partial: bool = False):
        logger.debug(f"Flushing {len(self._messages)} messages")

        print("flushing # of records: ", len(self._messages))

        if len(self._messages) < 1:
            logger.info(f"No messages to write")
            return

        format_type = self._config.file_type.get("format_type")
        flattening = self._config.file_type.get("flattening")
        file_extension = self._config.file_type.get("file_extension")

        if format_type == "parquet":
            self._azure_handler.write_parquet(self._messages, self._table, self._schema, file_extension)

        if format_type == "csv":
            self._azure_handler.write_csv(self._messages, self._table, flattening, file_extension)

        if format_type == "avro":
            self._azure_handler.write_avro(self._messages, self._table, self._schema, file_extension)

        if partial:
            self._partial_flush_count += 1

        self._messages.clear()
