from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketDataSettings(BaseSettings):
    default_universe: list[str] = ["AAPL", "MSFT", "TSLA", "GOOGL", "META", "NVDA", "AMZN", "SPY", "NFLX", "AMD", "PLTR", "COIN"]
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
    # Optional/secondary Alpaca config (data API + WS feed + endpoints).
    # Declared explicitly (with safe defaults) so consumers resolve
    # deterministically instead of relying on ``extra="allow"``.
    alpaca_secret_key: SecretStr | None = None
    alpaca_bars_feed: str = "iex"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_trading_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_live_base_url: str = "https://api.alpaca.markets"
    # "paper" (paper-api real, default) | "dry_run" (intercepted) | "live" (real money)
    alpaca_trading_mode: str = "paper"
    alpaca_paper_trading: bool = True

    # BingX API and Bot config
    bingx_api_key: SecretStr | None = None
    bingx_secret: SecretStr | None = None
    bingx_bot_enable_live: bool = False
    bingx_bot_trading_env: str = "paper"
    bingx_bot_live_symbol_allowlist: str = ""
    bingx_bot_allow_all_live: bool = False
    bingx_bot_live_require_healthcheck: bool = True
    bingx_bot_paper_trading: bool = True
    bingx_bot_audit_db_path: str = "data/bingx_bot_audit.sqlite"
    bingx_bot_live_healthcheck_ttl_s: int = 300

    # Phase A Scanner
    phase_a_scan_interval_s: int = 300

    # Audit Complex
    audit_db_path: str = "data/audit_complex.duckdb"

    # Operator Auth (HMAC-signed session cookies)
    qa_session_secret: str = ""
    qa_app_username: str = "admin"
    qa_app_password_hash: str = ""
    qa_app_display_name: str = "Operator"
    qa_app_email: str | None = None

    def get_bingx_live_allowlist(self) -> frozenset[str]:
        """Return the live-mode symbol allowlist as a frozenset (empty = none allowed)."""
        if not self.bingx_bot_live_symbol_allowlist.strip():
            return frozenset()
        return frozenset(
            s.strip() for s in self.bingx_bot_live_symbol_allowlist.split(",") if s.strip()
        )

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


from functools import lru_cache


@lru_cache(maxsize=1)
def load_settings() -> MarketDataSettings:
    """Load settings for the application."""
    return MarketDataSettings()


Config = MarketDataSettings
