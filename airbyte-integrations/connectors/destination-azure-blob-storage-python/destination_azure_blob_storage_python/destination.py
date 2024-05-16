#
# Copyright (c) 2024 Airbyte, Inc., all rights reserved.
#


from typing import Any, Iterable, Mapping
import random, string

from airbyte_cdk import AirbyteLogger
from airbyte_cdk.destinations import Destination
from airbyte_cdk.models import AirbyteConnectionStatus, AirbyteMessage, ConfiguredAirbyteCatalog, Status
from azure.core.exceptions import ClientAuthenticationError

from .azure import AzureHandler
from .config_reader import ConnectorConfig


class DestinationAzureBlobStoragePython(Destination):
    @staticmethod
    def _get_random_string(length: int) -> str:
        return "".join(random.choice(string.ascii_letters) for i in range(length))

    def write(
            self, config: Mapping[str, Any], configured_catalog: ConfiguredAirbyteCatalog, input_messages: Iterable[AirbyteMessage]
    ) -> Iterable[AirbyteMessage]:

        """
        TODO
        Reads the input stream of messages, config, and catalog to write data to the destination.

        This method returns an iterable (typically a generator of AirbyteMessages via yield) containing state messages received
        in the input message stream. Outputting a state message means that every AirbyteRecordMessage which came before it has been
        successfully persisted to the destination. This is used to ensure fault tolerance in the case that a sync fails before fully completing,
        then the source is given the last state message output from this method as the starting point of the next sync.

        :param config: dict of JSON configuration matching the configuration declared in spec.json
        :param configured_catalog: The Configured Catalog describing the schema of the data being received and how it should be persisted in the
                                    destination
        :param input_messages: The stream of input messages received from the source
        :return: Iterable of AirbyteStateMessages wrapped in AirbyteMessage structs
        """

        pass

    def check(self, logger: AirbyteLogger, config: Mapping[str, Any]) -> AirbyteConnectionStatus:
        """
        Tests if the input configuration can be used to successfully connect to the destination with the needed permissions
            e.g: if a provided API token or password can be used to connect and write to the destination.

        :param logger: Logging object to display debug/info/error to the logs
            (logs will not be accessible via airbyte UI if they are not passed to this logger)
        :param config: Json object containing the configuration of this destination, content of this json is as specified in
        the properties of the spec.json file

        :return: AirbyteConnectionStatus indicating a Success or Failure
        """
        connector_config = ConnectorConfig(**config)


        try:
            logger.info(f"Testing connection to {connector_config.storage_account_name}")
            azure_handler = AzureHandler(connector_config, self)
            logger.info(f"Successfully authenticated to {connector_config.storage_account_name}")
        except (AttributeError) as e:
            logger.error(f"Could not authenticate using {connector_config.credentials_type} on Account {connector_config.storage_account_name} Exception: {repr(e)}")
            message = f"Could not authenticate using {connector_config.credentials_type} on Account {connector_config.storage_account_name} Exception: {repr(e)}"
            return AirbyteConnectionStatus(status=Status.FAILED, message=message)

        try:
            logger.info(f"Testing that container {connector_config.container_name} exists in {connector_config.storage_account_name}")
            if not azure_handler.head_blob():
                raise ValueError(f"Could not find container {connector_config.container_name} in storage")
        except (ValueError) as e:
            return AirbyteConnectionStatus(status=Status.FAILED, message=f"Could not find container")

        # TODO: try: azure_handler.create_container() -- check if you can create a container


        return AirbyteConnectionStatus(status=Status.SUCCEEDED)
