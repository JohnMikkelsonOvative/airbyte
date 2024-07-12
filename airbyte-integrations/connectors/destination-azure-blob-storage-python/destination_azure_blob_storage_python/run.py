import logging
import sys

from airbyte_cdk.entrypoint import launch
from .destination import DestinationAzureBlobStoragePython


def run():
    destination = DestinationAzureBlobStoragePython()
    # print("$$$$ DEBUGGING $$$$")
    # print(sys.argv[3])
    # args = destination.parse_args(sys.argv[1:])
    # messages = destination.run_cmd(args)
    # for message in messages:
    #     print(message)
    #     print(message.json(exclude_unset=True))
    # print(destination)
    # print("$$$$ DEBUGGING $$$$")
    destination.run(sys.argv[1:])
