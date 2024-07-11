FROM airbyte/python-connector-base:1.1.0 AS base

COPY . ./airbyte/integration_code
WORKDIR /airbyte/integration_code
RUN pip install poetry
RUN poetry install -C ./airbyte/integration_code

ENTRYPOINT ["poetry", "run", "-q", "-C", "/airbyte/integration_code/", "python", "/airbyte/integration_code/main.py"]
