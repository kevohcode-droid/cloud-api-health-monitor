import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")
    APP_NAME: str = os.getenv("APP_NAME", "Cloud API Health Monitor")
    APP_VERSION: str = os.getenv("APP_VERSION", "0.1.0")
    SLACK_WEBHOOK_URL: str | None = os.getenv("SLACK_WEBHOOK_URL")
    ALERT_FAILURE_THRESHOLD: int = int(os.getenv("ALERT_FAILURE_THRESHOLD", "3"))

    def __init__(self) -> None:
        if self.ALERT_FAILURE_THRESHOLD < 1:
            raise ValueError("ALERT_FAILURE_THRESHOLD must be at least 1.")


settings = Settings()
