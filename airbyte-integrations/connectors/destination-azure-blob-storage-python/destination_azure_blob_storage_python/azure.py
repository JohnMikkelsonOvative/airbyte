import logging
from typing import Any, Dict, List, Optional

import fastavro
from azure.storage.blob import ContainerClient, BlobClient, BlobProperties
from azure.identity import ClientSecretCredential
from airbyte_cdk.destinations import Destination
from retrying import retry

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from fastavro import writer, parse_schema, reader
from io import BytesIO
import datetime
import json
import pickle

from time import time

from .config_reader import ConnectorConfig, CredentialsType, OutputFormat

logger = logging.getLogger("airbyte")


class AzureHandler:
    def __init__(self, connector_config: ConnectorConfig, destination: Destination) -> None:
        self._config: ConnectorConfig = connector_config
        self._client: ContainerClient = None
        self._destination: Destination = destination
        self._blob_client: BlobClient = None
        self._stream_time = None

        self.create_client()

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def create_client(self) -> None:
        client_credential: str | ClientSecretCredential

        #create stream timestamp
        self._stream_time = datetime.datetime.today().strftime('%Y-%m-%d')

        if self._config.credentials_type == CredentialsType.SAS_TOKEN:
            client_credential = self._config.sas_token
        elif self._config.credentials_type == CredentialsType.STORAGE_ACCOUNT_KEY:
            client_credential = self._config.storage_account_key
        elif self._config.credentials_type == CredentialsType.SERVICE_PRINCIPAL_TOKEN:
            client_credential = ClientSecretCredential(tenant_id=self._config.tenant_id, client_id=self._config.client_id,
                                                       client_secret=self._config.client_secret)

        self._client = ContainerClient(
            account_url=self._config.account_url,
            credential=client_credential,
            container_name=self._config.container_name,
        )

    @retry(stop_max_attempt_number=10, wait_random_min=1000)
    def head_blob(self) -> bool:
        return self._client.exists()

    def write_parquet(self, messages, stream_name, stream_schema):
        table_fields = []
        array_cols = []
        array_dates = []
        for col, definition in stream_schema.items():
            col_typ = definition.get("type")
            col_format = definition.get("format")

            if "number" in col_typ:
                table_fields.append(pa.field(col, pa.float64()))

            elif "integer" in col_typ:
                table_fields.append(pa.field(col, pa.int64()))

            elif "string" in col_typ and col_format == "date-time":
                table_fields.append(pa.field(col, pa.timestamp('ms')))

            elif "string" in col_typ and col_format == "date":
                array_dates.append(col)
                table_fields.append(pa.field(col, pa.date32()))

            elif "array" in col_typ:
                array_cols.append(col)
                table_fields.append(pa.field(col, pa.list_(pa.string())))

            else:
                table_fields.append(pa.field(col, pa.string()))

        for i, full_row in enumerate(messages):
            for column in full_row.keys():
                if column in array_cols:
                    list_string = []
                    if full_row[column] is not None:
                        for item in full_row[column]:
                            json_string = str(item)
                            list_string.append(json_string)
                    messages[i][column] = list_string
                elif column in array_dates:
                    if full_row[column] is not None:
                        converted_date = datetime.datetime.strptime(full_row[column], '%Y-%m-%d').date()
                        messages[i][column] = converted_date

        pyarrow_schema = pa.schema(table_fields)

        df = pd.DataFrame(messages)
        table = pa.Table.from_pandas(df, schema=pyarrow_schema)

        buf = pa.BufferOutputStream()

        #build path
        path_name = ""
        if self._config.path_name is not "":
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        # write parquet file from table to buffer stream
        pq.write_table(table, buf)
        current_timestamp = int(time() * 1000)
        name = str(current_timestamp) + ".parquet"
        #uploads blob with name to the container
        self._client.upload_blob(path_name + name, buf.getvalue().to_pybytes(), overwrite=True)


    def write_avro(self, df: pd.DataFrame, stream_name, stream_schema):



        # stream_schema = str(stream_schema)
        # stream_schema = stream_schema.replace("number","int")
        #
        # stream_schema = stream_schema.replace('"',"")
        # print("stream", stream_schema)

        write_schema = {"cpc": {"type": ["null", "double"], "description": "Cost per click"}, "cpm": {"type": ["null", "double"], "description": "Cost per thousand impressions"}, "cpp": {"type": ["null", "double"], "description": "Cost per thousand people reached"}, "ctr": {"type": ["null", "double"], "description": "Click-through rate"}, "ad_id": {"type": ["null", "string"], "description": "ID of the ad"}, "reach": {"type": ["null", "integer"], "description": "double of people who saw the ad"}, "spend": {"type": ["null", "double"], "description": "Total amount spent"}, "clicks": {"type": ["null", "integer"], "description": "Total double of clicks"}, "actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "ad_name": {"type": ["null", "string"], "description": "Name of the ad"}, "adset_id": {"type": ["null", "string"], "description": "ID of the ad set"}, "wish_bid": {"type": ["null", "double"], "description": "Bid based on the wish to achieve certain results"}, "date_stop": {"type": ["null", "string"], "format": "date", "description": "End date of the data"}, "frequency": {"type": ["null", "double"], "description": "Average double of times each person saw the ad"}, "recordive": {"type": ["null", "string"], "description": "Marketing recordive"}, "account_id": {"type": ["null", "string"], "description": "ID of the account"}, "adset_name": {"type": ["null", "string"], "description": "Name of the ad set"}, "date_start": {"type": ["null", "string"], "format": "date", "description": "Start date of the data"}, "unique_ctr": {"type": ["null", "double"], "description": "Unique click-through rate"}, "auction_bid": {"type": ["null", "double"], "description": "Bid amount in the auction"}, "buying_type": {"type": ["null", "string"], "description": "Type of buying"}, "campaign_id": {"type": ["null", "string"], "description": "ID of the campaign"}, "conversions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "impressions": {"type": ["null", "integer"], "description": "Total double of impressions"}, "website_ctr": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "account_name": {"type": ["null", "string"], "description": "Name of the account"}, "created_time": {"type": ["null", "string"], "format": "date", "description": "Time when the data was created"}, "social_spend": {"type": ["null", "double"], "description": "Spend in social channels"}, "updated_time": {"type": ["null", "string"], "format": "date", "description": "Time when the data was updated"}, "action_values": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "campaign_name": {"type": ["null", "string"], "description": "Name of the campaign"}, "purchase_roas": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "number"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "unique_clicks": {"type": ["null", "integer"], "description": "Total double of unique clicks"}, "unique_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "full_view_reach": {"type": ["null", "double"], "description": "Reach when the ad is fully viewed"}, "outbound_clicks": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "quality_ranking": {"type": ["null", "string"], "description": "Ranking based on quality"}, "account_currency": {"type": ["null", "string"], "description": "Currency used for the account"}, "ad_click_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "conversion_values": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_ad_click": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_thruplay": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "optimization_goal": {"type": ["null", "string"], "description": "Goal for optimization"}, "inline_link_clicks": {"type": ["null", "integer"], "description": "Total double of inline link clicks"}, "video_play_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "attribution_setting": {"type": ["null", "string"], "description": "How conversions are attributed"}, "cost_per_conversion": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "outbound_clicks_ctr": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "canvas_avg_view_time": {"type": ["null", "double"], "description": "Average time spent viewing the canvas"}, "cost_per_action_type": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "ad_impression_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "catalog_segment_value": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_unique_click": {"type": ["null", "double"], "description": "Cost per unique click"}, "full_view_impressions": {"type": ["null", "double"], "description": "Impressions when the ad is fully viewed"}, "inline_link_click_ctr": {"type": ["null", "double"], "description": "Click-through rate for inline link clicks"}, "website_purchase_roas": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "estimated_ad_recallers": {"type": ["null", "double"], "description": "Estimated ad recallers"}, "inline_post_engagement": {"type": ["null", "integer"], "description": "Engagement on inline posts"}, "unique_link_clicks_ctr": {"type": ["null", "double"], "description": "Unique click-through rate for link clicks"}, "unique_outbound_clicks": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "auction_competitiveness": {"type": ["null", "double"], "description": "Competitiveness level in the auction"}, "canvas_avg_view_percent": {"type": ["null", "double"], "description": "Average percentage of the canvas viewed"}, "catalog_segment_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "conversion_rate_ranking": {"type": ["null", "string"], "description": "Ranking based on conversion rates"}, "converted_product_value": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_outbound_click": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "engagement_rate_ranking": {"type": ["null", "string"], "description": "Ranking based on engagement rate"}, "mobile_app_purchase_roas": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_play_curve_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"value": {"type": ["null", "array"], "items": {"type": ["null", "integer"]}}, "action_type": {"type": ["null", "string"]}}}}, "unique_inline_link_clicks": {"type": ["null", "integer"], "description": "Total double of unique inline link clicks"}, "video_p25_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_p50_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_p75_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_p95_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "auction_max_competitor_bid": {"type": ["null", "double"], "description": "Maximum bid among the competitors in the auction"}, "converted_product_quantity": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_15_sec_video_view": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_inline_link_click": {"type": ["null", "double"], "description": "Cost per inline link click"}, "unique_outbound_clicks_ctr": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_p100_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_time_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_unique_action_type": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "unique_inline_link_click_ctr": {"type": ["null", "double"], "description": "Unique click-through rate for inline link clicks"}, "video_15_sec_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_30_sec_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_unique_outbound_click": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_avg_time_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "cost_per_estimated_ad_recallers": {"type": ["null", "double"], "description": "Cost per estimated ad recallers"}, "cost_per_inline_post_engagement": {"type": ["null", "double"], "description": "Cost per inline post engagement"}, "cost_per_unique_inline_link_click": {"type": ["null", "double"], "description": "Cost per unique inline link click"}, "instant_experience_clicks_to_open": {"type": ["null", "double"], "description": "Clicks to open instant experience"}, "instant_experience_clicks_to_start": {"type": ["null", "double"], "description": "Clicks to start instant experience"}, "instant_experience_outbound_clicks": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_play_retention_graph_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"value": {"type": ["null", "array"], "items": {"type": ["null", "integer"]}}, "action_type": {"type": ["null", "string"]}}}}, "cost_per_2_sec_continuous_video_view": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_play_retention_0_to_15s_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"value": {"type": ["null", "array"], "items": {"type": ["null", "integer"]}}, "action_type": {"type": ["null", "string"]}}}}, "video_continuous_2_sec_watched_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "video_play_retention_20_to_60s_actions": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"value": {"type": ["null", "array"], "items": {"type": ["null", "integer"]}}, "action_type": {"type": ["null", "string"]}}}}, "qualifying_question_qualify_answer_rate": {"type": ["null", "double"], "description": "Rate of qualifying question answer qualification"}, "catalog_segment_value_omni_purchase_roas": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "catalog_segment_value_mobile_purchase_roas": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}, "catalog_segment_value_website_purchase_roas": {"type": ["null", "array"], "items": {"type": ["null", "record"], "properties": {"lead": {"type": ["null", "double"]}, "value": {"type": ["null", "double"]}, "1d_view": {"type": ["null", "double"]}, "7d_view": {"type": ["null", "double"]}, "1d_click": {"type": ["null", "double"]}, "28d_view": {"type": ["null", "double"]}, "7d_click": {"type": ["null", "double"]}, "28d_click": {"type": ["null", "double"]}, "action_type": {"type": ["null", "string"]}, "action_target_id": {"type": ["null", "string"]}, "action_destination": {"type": ["null", "string"]}}}}}




        # schema_stream = json.dumps(stream_schema)
        # # print("type", type(schema_stream))
        # # json_object = json.loads(schema_stream)
        # #
        # print("json object", type(schema_stream))
        parsed_schema = parse_schema(write_schema)
        records = df.to_dict()

        #build path
        path_name = ""
        if self._config.path_name:
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"

        print(path_name)

        # write parquet file from table to buffer stream
        fo = BytesIO()
        writer(fo, parsed_schema, records)


        current_timestamp = int(time() * 1000)
        name = str(current_timestamp) + ".avro"
        #uploads blob with name to the container
        self._client.upload_blob(path_name + "/" + name, fo.getvalue(), overwrite=True)


    def write_messages_to_pickle(self, datastream, stream_name):
        path_name = ""
        if self._config.path_name:
            path_name = path_name + self._config.path_name + "/"

        if stream_name:
            path_name = path_name + stream_name + "/"

        if self._stream_time:
            path_name = path_name + self._stream_time + "/"


        current_timestamp = int(time() * 1000)
        name = str(current_timestamp) + ".pickle"

        output = pickle.dumps(datastream)
        self._client.upload_blob(path_name + "/" + name , output, overwrite=True)
