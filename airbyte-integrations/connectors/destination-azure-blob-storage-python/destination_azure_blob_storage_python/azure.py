import logging
from typing import Any, Dict, List, Optional

from azure.storage.blob import ContainerClient, BlobClient, BlobProperties
from airbyte_cdk.destinations import Destination
from retrying import retry

from .config_reader import ConnectorConfig, CredentialsType, OutputFormat

logger = logging.getLogger("airbyte")


class AzureHandler:
    def __init__(self, connector_config: ConnectorConfig, destination: Destination) -> None:
        self._config: ConnectorConfig = connector_config
        self._client: ContainerClient = None
        self._destination: Destination = destination

        self.create_client()

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def create_client(self) -> None:
        if self._config.credentials_type == CredentialsType.SAS_TOKEN:
            self._client = ContainerClient(
                # The value can be:
                # - a SAS token string
                # - an instance of a AzureSasCredential or AzureNamedKeyCredential from azure.core.credentials
                # - an account shared access key
                # - an instance of a TokenCredentials class from azure.identity
                account_url=self._config.account_url,
                credential=self._config.sas_token,
                container_name=self._config.container_name,
            )
        elif self._config.credentials_type == CredentialsType.STORAGE_ACCOUNT_KEY:
            self._client = ContainerClient(
                account_url=self._config.account_url,
                credential=self._config.storage_account_key,
                container_name=self._config.container_name,
            )


    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def head_blob(self) -> bool:
        return self._client.exists()


