from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    # PBKDF2-SHA256 work factor for NEW hashes (verification reads the count stored
    # in each hash). Keep the default in real use; the test suite lowers it.
    password_hash_iterations: int = Field(default=600_000, ge=1_000)
    # Largest accepted request body (413 above it). 6 MB = 5 MB photo + form fields.
    max_request_bytes: int = Field(default=6 * 1024 * 1024, ge=1024)


@lru_cache
def get_settings() -> Settings:
    return Settings()
