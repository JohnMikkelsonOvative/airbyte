import logging
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
import pickle


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

    def write_parquet(self, messages, stream_name, stream_schema, file_extension):
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
        if self._config.path_name is not "":
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        # write parquet file from table to buffer stream
        pq.write_table(table, buf)
        current_timestamp = int(time() * 1000)
        name = str(current_timestamp)
        if file_extension:
            name = name + ".parquet"
        #uploads blob with name to the container
        self._client.upload_blob(path_name + name, buf.getvalue().to_pybytes(), overwrite=True)




    def write_csv(self, messages, stream_name, flattening, file_extension):
        if flattening == "Root level flattening":
            messages = pd.json_normalize(
                messages, max_level=1
            )
        df = pd.DataFrame(messages)
        buf = pa.BufferOutputStream()

        df.to_csv(buf)  # filling that buffer

        #build path
        path_name = ""
        if self._config.path_name is not "":
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        current_timestamp = int(time() * 1000)
        name = str(current_timestamp)
        if file_extension:
            name = name + ".csv"
        #uploads blob with name to the container
        self._client.upload_blob(path_name + name, buf.getvalue().to_pybytes(), overwrite=True)
