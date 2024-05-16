#
# Copyright (c) 2024 Airbyte, Inc., all rights reserved.
#


import sys

from destination_azure_blob_storage_python import DestinationAzureBlobStoragePython

if __name__ == "__main__":
    DestinationAzureBlobStoragePython().run(sys.argv[1:])
