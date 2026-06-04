from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MARKET_PARSER_",
        extra="ignore",
    )

    db_path: Path = Field(default=Path("data/market_parser.sqlite"))
    export_dir: Path = Field(default=Path("exports"))
    timezone: str = "Europe/Moscow"
    category_name: str = "Детское питание"
    use_browser: bool = True
    request_timeout_seconds: float = 30.0
    page_delay_seconds: float = 2.0
    page_delay_jitter_seconds: float = 0.75
    store_delay_seconds: float = 15.0
    browser_headless: bool = True
    browser_proxy_server: str | None = None
    browser_proxy_username: str | None = None
    browser_proxy_password: str | None = None
    browser_storage_state_dir: Path = Field(default=Path("data/browser_states"))
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    google_sheet_id: str | None = Field(default=None, validation_alias="GOOGLE_SHEET_ID")
    google_sheet_name: str = Field(default="Витрина_30д", validation_alias="GOOGLE_SHEET_NAME")
    google_service_account_file: Path | None = Field(
        default=None,
        validation_alias="GOOGLE_SERVICE_ACCOUNT_FILE",
    )
    google_service_account_json: str | None = Field(
        default=None,
        validation_alias="GOOGLE_SERVICE_ACCOUNT_JSON",
    )

    @property
    def google_configured(self) -> bool:
        return bool(
            self.google_sheet_id
            and (self.google_service_account_file or self.google_service_account_json)
        )
