import logging
import math
from typing import Any, Dict, List, Optional

import fastavro
from azure.storage.blob import ContainerClient, BlobClient, BlobProperties
from azure.identity import ClientSecretCredential
from airbyte_cdk.destinations import Destination
from retrying import retry

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import csv
from fastavro import writer, parse_schema, reader
from io import BytesIO
import datetime
import json
import avro.schema
import avro.io
import avro.datafile
import io


from time import time

from .config_reader import ConnectorConfig, CredentialsType, OutputFormat

logger = logging.getLogger("airbyte")

class AzureHandler:
    def __init__(self, connector_config: ConnectorConfig, destination: Destination) -> None:
        self._config: ConnectorConfig = connector_config
        self._client: ContainerClient = None
        self._destination: Destination = destination
        self._blob_client: BlobClient = None
        self._stream_time = None

        self.create_client()

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def create_client(self) -> None:
        client_credential: str | ClientSecretCredential

        #create stream timestamp
        self._stream_time = datetime.datetime.today().strftime('%Y-%m-%d')

        if self._config.credentials_type == CredentialsType.SAS_TOKEN:
            client_credential = self._config.sas_token
        elif self._config.credentials_type == CredentialsType.STORAGE_ACCOUNT_KEY:
            client_credential = self._config.storage_account_key
        elif self._config.credentials_type == CredentialsType.SERVICE_PRINCIPAL_TOKEN:
            client_credential = ClientSecretCredential(tenant_id=self._config.tenant_id, client_id=self._config.client_id,
                                                       client_secret=self._config.client_secret)

        self._client = ContainerClient(
            account_url=self._config.account_url,
            credential=client_credential,
            container_name=self._config.container_name,
        )

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def head_blob(self) -> bool:
        return self._client.exists()

    def write_parquet(self, messages, stream_name, stream_schema):
        table_fields = []
        array_cols = []
        array_dates = []
        for col, definition in stream_schema.items():
            col_typ = definition.get("type")
            col_format = definition.get("format")

            if "number" in col_typ:
                table_fields.append(pa.field(col, pa.float64()))

            elif "integer" in col_typ:
                table_fields.append(pa.field(col, pa.int64()))

            elif "string" in col_typ and col_format == "date-time":
                table_fields.append(pa.field(col, pa.timestamp('ms')))

            elif "string" in col_typ and col_format == "date":
                array_dates.append(col)
                table_fields.append(pa.field(col, pa.date32()))

            elif "array" in col_typ:
                array_cols.append(col)
                table_fields.append(pa.field(col, pa.list_(pa.string())))

            elif "boolean" in col_typ:
                table_fields.append(pa.field(col, pa.bool_()))

            else:
                table_fields.append(pa.field(col, pa.string()))

        for i, full_row in enumerate(messages):
            for column in full_row.keys():
                if column in array_cols:
                    list_string = []
                    if full_row[column] is not None:
                        for item in full_row[column]:
                            json_string = str(item)
                            list_string.append(json_string)
                    messages[i][column] = list_string
                elif column in array_dates:
                    if full_row[column] is not None:
                        converted_date = datetime.datetime.strptime(full_row[column], '%Y-%m-%d').date()
                        messages[i][column] = converted_date

        pyarrow_schema = pa.schema(table_fields)

        df = pd.DataFrame(messages)
        table = pa.Table.from_pandas(df, schema=pyarrow_schema)

        buf = pa.BufferOutputStream()

        #build path
        path_name = ""
        if self._config.path_name != "":
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        # write parquet file from table to buffer stream
        pq.write_table(table, buf)
        current_timestamp = int(time() * 1000)
        name = str(current_timestamp)
        #if file_extension:
        name = name + ".parquet"
        #uploads blob with name to the container
        self._client.upload_blob(path_name + name, buf.getvalue().to_pybytes(), overwrite=True)

    def write_csv(self, messages, stream_name, flattening):
        if flattening == "Root level flattening":
            messages = pd.json_normalize(
                messages, max_level=1
            )
        df = pd.DataFrame(messages)
        buf = pa.BufferOutputStream()

        df.to_csv(buf)  # filling that buffer

        #build path
        path_name = ""
        if self._config.path_name != "":
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        current_timestamp = int(time() * 1000)
        name = str(current_timestamp)
        #if file_extension:
        name = name + ".csv"
        #uploads blob with name to the container
        self._client.upload_blob(path_name + name, buf.getvalue().to_pybytes(), overwrite=True)

    def write_avro(self, messages, stream_name, stream_schema):
        array_cols = []
        for col, definition in stream_schema.items():
            col_typ = definition.get("type")

            if "array" in col_typ:
                array_cols.append(col)

        for i, full_row in enumerate(messages):
            for column in full_row.keys():
                if column in array_cols:
                    list_string = []
                    if full_row[column] is not None:
                        for item in full_row[column]:
                            json_string = str(item)
                            list_string.append(json_string)
                    messages[i][column] = list_string

        def convert_nan_to_none(record):
            # Convert None values to None in a single dictionary
            return {key: (None if isinstance(value, float) and math.isnan(value) else value) for key, value in record.items()}

        def convert_nan_to_none_in_list(records):
            # Apply the conversion function to each dictionary in the list
            return [convert_nan_to_none(record) for record in records]

        converted_records = convert_nan_to_none_in_list(messages)

        def remove_description_and_null_type(schema_data):
            """ Recursively remove 'description' and 'null' type from the dictionary. """
            if isinstance(schema_data, dict):
                # Remove 'description' field if it exists
                schema_data.pop('description', None)

                # Process 'type' field
                if 'type' in schema_data:
                    types = schema_data['type']
                    if isinstance(types, list):
                        # Remove 'null' type if it exists
                        schema_data['type'] = types[1]

                # Recursively process nested dictionaries and lists
                for key in schema_data:
                    if isinstance(schema_data[key], dict):
                        remove_description_and_null_type(schema_data[key])
                    elif isinstance(schema_data[key], list):
                        schema_data[key] = [remove_description_and_null_type(item) for item in schema_data[key]]

            return schema_data

        cleaned_dict = remove_description_and_null_type(stream_schema)

        # Function to infer Avro schema from data with unique record names
        def infer_avro_schema(data):

            def infer_type(value, parent_name=""):
                v = value.get("type")
                if v == "integer":
                    return "int"
                elif "items" in value:
                    return {"type": "array", "items": "string"}
                elif v == "number":
                    return "float"
                elif v == "bool":
                    return "boolean"
                else:
                    return "string"

            fields = [{"name": k, "type": ["null", infer_type(v, parent_name=k)]} for k, v in data.items()]
            schema = {
                "type": "record",
                "name": "schema",
                "fields": fields
            }
            return schema

        # Generate the schema from the schema data
        schema_dict = infer_avro_schema(cleaned_dict)

        # Convert schema to Avro schema object
        schema_json = json.dumps(schema_dict)
        schema_fields = [field['name'] for field in schema_dict['fields']]

        def reorder_dict_by_schema(record, schema_order):
            # Reorder the dictionary based on the schema order
            ordered_record = {field: record[field] for field in schema_order if field in record}
            return ordered_record

        def reorder_list_of_dicts_by_schema(records, schema_order):
            # Reorder each dictionary in the list according to the schema order
            return [reorder_dict_by_schema(record, schema_order) for record in records]

        ordered_records = reorder_list_of_dicts_by_schema(converted_records, schema_fields)

        # Print the reordered list of dictionaries

        schema = avro.schema.parse(schema_json)

        path_name = ""
        if self._config.path_name != "":
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        current_timestamp = int(time() * 1000)
        name = str(current_timestamp)
        #if file_extension:
        name = name + ".avro"

        # Prepare the Avro file writer
        avro_filename = 'output_data.avro'
        with open(avro_filename, 'wb') as out_file:
            writer = avro.io.DatumWriter(schema)
            avro_writer = avro.datafile.DataFileWriter(out_file, writer, schema)

            try:
                # Write the data to the Avro file
                for record in ordered_records:
                    avro_writer.append(record)
                print(f"Data successfully written to {avro_filename}")
            except Exception as e:
                print(f"Error writing data to Avro file: {e}")

            # Close the writer
            avro_writer.close()

        # Upload the file
        with open(avro_filename, 'rb') as data:
            self._client.upload_blob(path_name + name, data, overwrite=True)



