"""Application configuration with environment variable overrides."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Immutable application configuration.

    All values can be overridden via environment variables prefixed with KEEPLINK_.
    """

    # Database
    db_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("KEEPLINK_DB_PATH", "./data/task.db")
        )
    )

    # Worker settings
    worker_poll_interval_sec: float = float(
        os.getenv("KEEPLINK_POLL_INTERVAL", "5.0")
    )
    worker_concurrency: int = int(
        os.getenv("KEEPLINK_WORKER_CONCURRENCY", "1")
    )
    max_retry_count: int = int(os.getenv("KEEPLINK_MAX_RETRIES", "5"))
    base_backoff_sec: float = float(os.getenv("KEEPLINK_BASE_BACKOFF", "60.0"))

    # FastAPI internal API
    api_host: str = os.getenv("KEEPLINK_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("KEEPLINK_API_PORT", "19210"))

    # Internet Archive — keys read from environment, never hardcoded
    ia_access_key: str | None = os.getenv("KEEPLINK_IA_ACCESS_KEY")
    ia_secret_key: str | None = os.getenv("KEEPLINK_IA_SECRET_KEY")

    # Logging
    log_level: str = os.getenv("KEEPLINK_LOG_LEVEL", "INFO")
    log_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("KEEPLINK_LOG_FILE", "./data/archiver.log")
        )
    )


def load_config() -> Config:
    """Load and return the application configuration."""
    return Config()
