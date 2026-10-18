from typing import Optional
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application and FalkorDB configuration settings.
    Reads from environment variables and optional .env file.
    Supports production cloud deployments (Render, Vercel, FalkorDB Cloud).
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application settings
    app_name: str = "Anvaya"
    app_env: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "app_env"))
    app_port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "APP_PORT", "app_port"))
    cors_origins: str = Field(default="*", validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"))

    # FalkorDB settings
    falkordb_host: str = Field(default="localhost", validation_alias=AliasChoices("FALKORDB_HOST", "falkordb_host"))
    falkordb_port: int = Field(default=6379, validation_alias=AliasChoices("FALKORDB_PORT", "falkordb_port"))
    falkordb_username: Optional[str] = Field(default=None, validation_alias=AliasChoices("FALKORDB_USERNAME", "falkordb_username"))
    falkordb_password: Optional[str] = Field(default=None, validation_alias=AliasChoices("FALKORDB_PASSWORD", "falkordb_password"))
    falkordb_graph_name: str = Field(default="anvaya", validation_alias=AliasChoices("FALKORDB_GRAPH_NAME", "falkordb_graph_name"))
    falkordb_ssl: bool = Field(default=False, validation_alias=AliasChoices("FALKORDB_SSL", "falkordb_ssl"))
    falkordb_url: Optional[str] = Field(default=None, validation_alias=AliasChoices("FALKORDB_URL", "falkordb_url"))

    @field_validator("falkordb_ssl", mode="before")
    @classmethod
    def parse_ssl(cls, v):
        if isinstance(v, str):
            v_clean = v.strip().lower()
            return v_clean in ("true", "1", "t", "yes", "y")
        return bool(v)

    @field_validator("falkordb_host", mode="before")
    @classmethod
    def parse_host(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if ":" in v and not v.startswith("["):
                parts = v.split(":")
                return parts[0]
        return v



settings = Settings()
