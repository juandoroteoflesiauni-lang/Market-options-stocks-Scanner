from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketDataSettings(BaseSettings):
    """Configuration class for market data infrastructure and secrets.

    Automatically loads variables from the environment and the .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # Infrastructure
    database_url: str
    redis_url: str

    # Cryptography Secrets
    secret_key: SecretStr

    # Global Market Data Integration
    # FMP
    fmp_api_key: SecretStr

    # Massive
    massive_api_key: SecretStr
    massive_ws_url: str

    # Alpaca
    alpaca_api_key: SecretStr
    alpaca_api_secret: SecretStr

    @field_validator(
        "secret_key",
        "fmp_api_key",
        "massive_api_key",
        "alpaca_api_key",
        "alpaca_api_secret",
    )
    @classmethod
    def validate_secrets_not_empty(cls, value: SecretStr) -> SecretStr:
        """Ensures that secret values are neither empty nor just whitespace."""
        if not value.get_secret_value().strip():
            raise ValueError("Secret field cannot be empty or consist only of whitespace.")
        return value
