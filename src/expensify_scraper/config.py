from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    expensify_partner_user_id: str | None = Field(
        default=None,
        alias="EXPENSIFY_PARTNER_USER_ID",
        description="Optional Integration Server partnerUserID (export only)",
    )
    expensify_partner_user_secret: str | None = Field(
        default=None,
        alias="EXPENSIFY_PARTNER_USER_SECRET",
        description="Optional Integration Server partnerUserSecret (export only)",
    )
    expensify_output_dir: Path = Field(
        default=Path("out"),
        alias="EXPENSIFY_OUTPUT_DIR",
    )
    expensify_data_dir: Path = Field(
        default=Path("data"),
        alias="EXPENSIFY_DATA_DIR",
    )
    expensify_auth_dir: Path = Field(
        default=Path(".auth"),
        alias="EXPENSIFY_AUTH_DIR",
    )
    expensify_since: date = Field(
        default=date(2015, 1, 1),
        alias="EXPENSIFY_SINCE",
    )
    expensify_download_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        alias="EXPENSIFY_DOWNLOAD_CONCURRENCY",
    )

    integration_server_url: str = (
        "https://integrations.expensify.com/Integration-Server/ExpensifyIntegrations"
    )
    new_expensify_url: str = "https://new.expensify.com"
    expensify_domain: str = "www.expensify.com"

    @property
    def manifest_db_path(self) -> Path:
        return self.expensify_data_dir / "manifest.sqlite"

    @property
    def manifest_csv_path(self) -> Path:
        return self.expensify_data_dir / "manifest.csv"

    @property
    def token_path(self) -> Path:
        return self.expensify_auth_dir / "token.json"

    @property
    def storage_state_path(self) -> Path:
        return self.expensify_auth_dir / "storage_state.json"

    @property
    def template_path(self) -> Path:
        # Ships as package data so it resolves for editable and wheel installs alike.
        return Path(__file__).resolve().parent / "templates" / "manifest.ftl"

    def require_integration_credentials(self) -> tuple[str, str]:
        user_id = (self.expensify_partner_user_id or "").strip()
        secret = (self.expensify_partner_user_secret or "").strip()
        if not user_id or not secret:
            raise ValueError(
                "export requires EXPENSIFY_PARTNER_USER_ID and "
                "EXPENSIFY_PARTNER_USER_SECRET. Copy .env.example to .env "
                "and add credentials from "
                "https://www.expensify.com/tools/integrations/"
            )
        return user_id, secret

    def ensure_dirs(self) -> None:
        self.expensify_output_dir.mkdir(parents=True, exist_ok=True)
        self.expensify_data_dir.mkdir(parents=True, exist_ok=True)
        self.expensify_auth_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
