import enum


class CredentialsType(enum.Enum):
    SAS_TOKEN = "SAS Token"
    STORAGE_ACCOUNT_KEY = "Storage Account Key"
    SERVICE_PRINCIPAL_TOKEN = "Service Principal Token"

    @staticmethod
    def from_string(s: str):
        if s == "SAS Token":
            return CredentialsType.SAS_TOKEN
        elif s == "Storage Account Key":
            return CredentialsType.STORAGE_ACCOUNT_KEY
        elif s == "Service Principal Token":
            return CredentialsType.SERVICE_PRINCIPAL_TOKEN
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
        print("Test")
        self.storage_account_name = storage_account_name
        self.account_url = f"https://{self.storage_account_name}.blob.core.windows.net"
        self.container_name = container_name
        self.credentials = credentials
        self.credentials_type = CredentialsType.from_string(credentials.get("auth_type"))
        print(self.credentials_type)

        if self.credentials_type == CredentialsType.SAS_TOKEN:
            print("SAS")
            self.sas_token = self.credentials.get("sas_token")
        elif self.credentials_type == CredentialsType.STORAGE_ACCOUNT_KEY:
            print("SA")
            self.storage_account_key = self.credentials.get("azure_blob_storage_account_key")
        elif self.credentials_type == CredentialsType.SERVICE_PRINCIPAL_TOKEN:
            print("SPT")
            self.tenant_id = self.credentials.get("tenant_id")
            self.client_id = self.credentials.get("client_id")
            self.client_secret = self.credentials.get("client_secret")
        else:
            raise Exception("Auth Mode not recognized.")


def __str__(self):
    return f"<AzureBlobStorage(StorageAccountName={self.storage_account_name},ContainerName={self.container_name}>"
