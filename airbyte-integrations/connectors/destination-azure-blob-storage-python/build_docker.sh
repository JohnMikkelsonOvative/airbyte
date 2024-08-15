#!/bin/zsh
TAG_VERSION=`poetry version --short`
docker buildx build . --platform=linux/amd64 \
  -t us-central1-docker.pkg.dev/airbyte-custom-connectors/ovative/airbyte-destination-azure-blob-storage-python-dev:$TAG_VERSION
