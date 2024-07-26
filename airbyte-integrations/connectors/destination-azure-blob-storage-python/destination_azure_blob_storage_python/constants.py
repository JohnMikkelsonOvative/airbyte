#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

EMPTY_VALUES = ["", " ", "#N/A", "#N/A N/A", "#NA", "<NA>", "N/A", "NA", "NULL", "none", "None", "NaN", "n/a", "nan", "null", "[]", "{}"]
BOOLEAN_VALUES = ["true", "1", "1.0", "t", "y", "yes"]

PANDAS_TYPE_MAPPING = {
    "string": "string",
    "integer": "Int64",
    "number": "float64",
    "boolean": "bool",
    "object": "object",
    "array": "object",
    "date": "datetime",
    "date-time": "datetime"
}

TYPE_MAPPING_DOUBLE = {
    "string": "string",
    "integer": "bigint",
    "number": "double",
    "boolean": "boolean",
    "null": "string",
}
