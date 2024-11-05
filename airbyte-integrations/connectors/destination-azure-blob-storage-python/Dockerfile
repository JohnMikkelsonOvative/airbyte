FROM airbyte/python-connector-base:2.0.0 AS base

COPY . ./airbyte/integration_code
WORKDIR /airbyte/integration_code
RUN pip install poetry
RUN poetry install --no-cache -C ./airbyte/integration_code


ENV AIRBYTE_ENTRYPOINT="python /airbyte/integration_code/main.py"
ENTRYPOINT ["python", "/airbyte/integration_code/main.py"]