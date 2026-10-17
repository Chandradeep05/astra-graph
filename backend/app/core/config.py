from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application and FalkorDB configuration settings.
    Reads from environment variables and optional .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application settings
    app_name: str = "Anvaya"
    app_env: str = "development"
    app_port: int = 8000

    # FalkorDB settings
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: Optional[str] = None
    falkordb_graph_name: str = "anvaya"


settings = Settings()
