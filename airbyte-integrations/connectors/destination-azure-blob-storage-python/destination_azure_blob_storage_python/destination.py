from typing import Any, Iterable, Mapping, Dict
import random, string

from airbyte_cdk import AirbyteLogger
from airbyte_cdk.destinations import Destination
from airbyte_cdk.models import AirbyteConnectionStatus, AirbyteMessage, AirbyteStateType, ConfiguredAirbyteCatalog, Status, Type
from azure.core.exceptions import ClientAuthenticationError

from .azure import AzureHandler
from .config_reader import ConnectorConfig
from .stream_writer import StreamWriter

# Flush records every 25000 records to limit memory consumption
RECORD_FLUSH_INTERVAL = 25000

class DestinationAzureBlobStoragePython(Destination):
    def _flush_streams(self, streams: Dict[str, StreamWriter]) -> None:
        for stream in streams:
            streams[stream].flush()
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
        connector_config = ConnectorConfig(**config)

        try:
            azure_handler = AzureHandler(connector_config, self)
        except ClientError as e:
            logger.error(f"Could not create session due to exception {repr(e)}")
            raise Exception(f"Could not create session due to exception {repr(e)}")

        # creating stream writers
        streams = {
            s.stream.name: StreamWriter(azure_handler=azure_handler, config=connector_config, configured_stream=s)
            for s in configured_catalog.streams
        }





        for message in input_messages:

            if message.type == Type.STATE and message.state.type == AirbyteStateType.STREAM:
                state_stream = message.state.stream

                if not state_stream.stream_state:
                    stream = state_stream.stream_descriptor.name
                    print(f"Received empty state for stream {stream}, resetting stream")
                    if stream in streams:
                        streams[stream].reset()
                    else:
                        print(f"Trying to reset stream {stream} that is not in the configured catalog")

                # Flush records when state is received
                else:
                    stream = state_stream.stream_descriptor.name
                    if stream in streams:
                        print(f"Got state message from source: flushing records for {stream}")
                        streams[stream].flush(partial=True)
                    else:
                        print(f"Trying to flush stream {stream} that is not in the configured catalog")

                yield message

            elif message.type == Type.RECORD:
                data = message.record.data
                stream = message.record.stream
                streams[stream].append_message(data)

                # Flush records every RECORD_FLUSH_INTERVAL records to limit memory consumption
                # Records will either get flushed when a state message is received or when hitting the RECORD_FLUSH_INTERVAL
                if len(streams[stream]._messages) > RECORD_FLUSH_INTERVAL:
                    print(f"Reached size limit: flushing records for {stream}")
                    streams[stream].flush(partial=True)
                    print(streams[stream]._table)
            else:
                print(f"Unhandled message type {message.type}: {message}")

        # Flush all or remaining records
        self._flush_streams(streams)

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
