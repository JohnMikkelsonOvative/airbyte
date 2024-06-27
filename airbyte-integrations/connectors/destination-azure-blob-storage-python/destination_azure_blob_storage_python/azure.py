import logging
from typing import Any, Dict, List, Optional

from azure.storage.blob import ContainerClient, BlobClient, BlobProperties
from azure.identity import ClientSecretCredential
from airbyte_cdk.destinations import Destination
from retrying import retry

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from time import time


from .config_reader import ConnectorConfig, CredentialsType, OutputFormat

logger = logging.getLogger("airbyte")


class AzureHandler:
    def __init__(self, connector_config: ConnectorConfig, destination: Destination) -> None:
        self._config: ConnectorConfig = connector_config
        self._client: ContainerClient = None
        self._destination: Destination = destination
        self._blob_client: BlobClient = None
        self._stream_name: StreamName = None

        self.create_client()

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def create_client(self) -> None:
        client_credential: str | ClientSecretCredential

        if self._config.credentials_type == CredentialsType.SAS_TOKEN:
            client_credential = self._config.sas_token
        elif self._config.credentials_type == CredentialsType.STORAGE_ACCOUNT_KEY:
            client_credential = self._config.storage_account_key
        elif self._config.credentials_type == CredentialsType.SERVICE_PRINCIPAL_TOKEN:
            client_credential = ClientSecretCredential(tenant_id=self._config.tenant_id, client_id=self._config.client_id, client_secret=self._config.client_secret)

        self._client = ContainerClient(
            account_url=self._config.account_url,
            credential=client_credential,
            container_name=self._config.container_name,
        )

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def head_blob(self) -> bool:
        return self._client.exists()


    def write_parquet(self, df: pd.DataFrame, stream_name):
        # write pandas df to table
        table = pa.Table.from_pandas(df)
        buf = pa.BufferOutputStream()

        # write parquet file from table to buffer stream
        pq.write_table(table, buf)
        current_timestamp = int(time() * 1000)
        name = stream_name + str(current_timestamp) + ".parquet"
        #uploads blob with name to the container
        self._client.upload_blob(name, buf.getvalue().to_pybytes(), overwrite=True)
