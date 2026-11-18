from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Office Maintenance"
    secret_key: str = "YOUR_SECRET_KEY_HERE"
    database_url: str = f"sqlite:///{ROOT_DIR / 'maintenance.db'}"
    session_https_only: bool = False
    upload_dir: str = str(ROOT_DIR / "uploads")
    demo_password: str = "OfficeDemo1!"


@lru_cache
def get_settings() -> Settings:
    return Settings()
