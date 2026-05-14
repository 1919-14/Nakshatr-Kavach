# backend/config.py
"""
NAKSHATRA-KAVACH — Layer 1: Application Configuration
Loads environment variables, configures logging, defines all runtime settings.
"""

import logging
import logging.handlers
import os
from pathlib import Path

from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────
# LOAD .env FILE
# ─────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# ─────────────────────────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s | %(levelname)8s | %(name)s | %(message)s"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "nakshatra.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5


def configure_logging(level: str = "INFO") -> None:
    """
    Configure root logger with rotating file handler and console handler.

    Args:
        level: Logging level string (DEBUG/INFO/WARNING/ERROR/CRITICAL).
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid adding duplicate handlers when Flask reloader re-imports
    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)


# ─────────────────────────────────────────────────────────────────
# FLASK APPLICATION CONFIG CLASSES
# ─────────────────────────────────────────────────────────────────

class BaseConfig:
    """Shared configuration for all environments."""

    # Flask core
    SECRET_KEY: str = os.getenv("SECRET_KEY", "nakshatra-kavach-dev-secret-2026")
    DEBUG: bool = False
    TESTING: bool = False

    # Database
    DB_DIR: Path = BASE_DIR / "app" / "database"
    DB_PATH: Path = DB_DIR / "nakshatra.db"
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DB_DIR / 'nakshatra.db'}"
    )

    # CORS
    CORS_ORIGINS: list = os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")

    # SocketIO
    SOCKETIO_ASYNC_MODE: str = os.getenv("SOCKETIO_ASYNC_MODE", "eventlet")
    SOCKETIO_CORS_ALLOWED_ORIGINS: str = "*"

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Scheduler guard flag (set at runtime in app factory)
    SCHEDULER_STARTED: bool = False

    # API timeout and retry
    REQUEST_TIMEOUT_S: int = int(os.getenv("REQUEST_TIMEOUT_S", "10"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

    # Groq (Layer 6 — not used by Layer 1, stored for global config)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    @classmethod
    def init_app(cls, app) -> None:
        """Perform post-creation initialization for Flask app."""
        configure_logging(cls.LOG_LEVEL)
        cls.DB_DIR.mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(BaseConfig):
    """Development environment — verbose logging, debug mode on."""

    DEBUG = True
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")


class ProductionConfig(BaseConfig):
    """Production environment — tighter security, INFO-level logs."""

    DEBUG = False
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "CHANGE_ME_IN_PRODUCTION")

    @classmethod
    def init_app(cls, app) -> None:
        super().init_app(app)
        # In production, ensure SECRET_KEY is properly set
        if cls.SECRET_KEY == "CHANGE_ME_IN_PRODUCTION":
            import warnings
            warnings.warn(
                "SECRET_KEY is not set. Set it via the SECRET_KEY env var.",
                RuntimeWarning,
                stacklevel=2,
            )


class TestingConfig(BaseConfig):
    """Testing environment — in-memory DB, testing mode."""

    TESTING = True
    DEBUG = True
    DB_PATH: Path = BaseConfig.DB_DIR / "nakshatra_test.db"
    DATABASE_URL: str = f"sqlite:///{BaseConfig.DB_DIR / 'nakshatra_test.db'}"
    LOG_LEVEL: str = "DEBUG"


# ─────────────────────────────────────────────────────────────────
# ENVIRONMENT → CONFIG CLASS MAPPING
# ─────────────────────────────────────────────────────────────────

CONFIG_MAP: dict = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config() -> BaseConfig:
    """
    Return the appropriate config class based on FLASK_ENV environment variable.

    Returns:
        A config class (not an instance — Flask expects the class).
    """
    env = os.getenv("FLASK_ENV", "development").lower()
    return CONFIG_MAP.get(env, DevelopmentConfig)
