import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")
    APP_NAME: str = os.getenv("APP_NAME", "Cloud API Health Monitor")
    APP_VERSION: str = os.getenv("APP_VERSION", "0.1.0")


settings = Settings()
