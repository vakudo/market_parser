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
    region_name: str = "Москва, центр"
    category_name: str = "Детское питание"
    max_items_per_store: int = 2000
    use_browser: bool = True
    request_timeout_seconds: float = 30.0
    page_delay_seconds: float = 0.6
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
