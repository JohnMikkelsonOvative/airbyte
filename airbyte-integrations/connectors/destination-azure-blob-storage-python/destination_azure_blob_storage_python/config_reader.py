import enum


class CredentialsType(enum.Enum):
    SAS_TOKEN = "SAS Token"
    STORAGE_ACCOUNT_KEY = "Storage Account Key"

    @staticmethod
    def from_string(s: str):
        if s == "SAS Token":
            return CredentialsType.SAS_TOKEN
        elif s == "Storage Account Key":
            return CredentialsType.STORAGE_ACCOUNT_KEY
        else:
            raise ValueError(f"Unknown auth mode: {s}")


class OutputFormat(enum.Enum):
    AVRO = "Avro"
    CSV = "CSV"
    PARQUET = "Parquet"
    JSONL = "JSONL"

    @staticmethod
    def from_string(s: str):
        if s == "Avro":
            return OutputFormat.AVRO
        elif s == "CSV":
            return OutputFormat.CSV
        elif s == "Parquet":
            return OutputFormat.PARQUET
        elif s == "JSONL":
            return OutputFormat.JSONL
        else:
            raise ValueError(f"Unknown output format: {s}")


class PartitionOptions(enum.Enum):
    NONE = "NO PARTITIONING"
    DATE = "DATE"
    YEAR = "YEAR"
    MONTH = "MONTH"
    DAY = "DAY"

    @staticmethod
    def from_string(s: str):
        if s == "DATE":
            return PartitionOptions.DATE
        elif s == "YEAR":
            return PartitionOptions.YEAR
        elif s == "MONTH":
            return PartitionOptions.MONTH
        elif s == "DAY":
            return PartitionOptions.DAY

        return PartitionOptions.NONE


class ConnectorConfig:
    def __init__(
            self,
            storage_account_name: str = None,
            container_name: str = None,
            credentials: dict = None,

    ):
        self.storage_account_name = storage_account_name
        self.account_url = f"https://{self.storage_account_name}.blob.core.windows.net"
        self.container_name = container_name
        self.credentials = credentials
        self.credentials_type = CredentialsType.from_string(credentials.get("auth_type"))

        if self.credentials_type == CredentialsType.SAS_TOKEN:
            self.sas_token = self.credentials.get("sas_token")
        if self.credentials_type == CredentialsType.STORAGE_ACCOUNT_KEY:
            self.storage_account_key = self.credentials.get("azure_blob_storage_account_key")
        else:
            raise Exception("Auth Mode not recognized.")


def __str__(self):
    return f"<AzureBlobStorage(StorageAccountName={self.storage_account_name},ContainerName={self.container_name}>"
