FROM airbyte/python-connector-base:2.0.0 AS base

COPY pyproject.toml ./airbyte/integration_code/pyproject.toml
WORKDIR /airbyte/integration_code
RUN pip install poetry
RUN poetry install --no-root --no-cache -C ./airbyte/integration_code

COPY . ./airbyte/integration_code
RUN poetry install --only-root --no-cache -C ./airbyte/integration_code

ENV AIRBYTE_ENTRYPOINT="python /airbyte/integration_code/main.py"
ENTRYPOINT ["python", "/airbyte/integration_code/main.py"]