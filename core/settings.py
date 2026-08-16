"""
Central settings loader.

Local laptop use: reads .env from the project root.
VPS/cloud later: same code — you just set real environment variables instead
of (or in addition to) a .env file. Nothing in the rest of the codebase needs
to change when you move hosts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    # --- Email ---
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_APP_PASSWORD: str = os.getenv("SMTP_APP_PASSWORD", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
    EMAIL_TO: List[str] = [
        e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()
    ]

    # --- Database ---
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{PROJECT_ROOT}/data/tenders.db")

    # --- Behavior ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    REQUEST_DELAY_SECONDS: float = float(os.getenv("REQUEST_DELAY_SECONDS", "2"))

    # --- Paths ---
    PROJECT_ROOT: Path = PROJECT_ROOT
    CATEGORIES_FILE: Path = PROJECT_ROOT / "config" / "categories.yaml"
    LOG_DIR: Path = PROJECT_ROOT / "logs"


settings = Settings()


def load_categories() -> dict:
    """Reloaded fresh every run so you can edit keywords without touching code."""
    with open(settings.CATEGORIES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
