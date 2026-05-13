"""Central configuration from environment."""
import os
from dotenv import load_dotenv

load_dotenv()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    path = os.environ.get("DATABASE_PATH", "nakshatra_kavach.db")
    return f"sqlite:///{path}"


def is_mysql(url: str) -> bool:
    return "mysql" in url.lower()


GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
INGESTION_INTERVAL_SECONDS = int(os.environ.get("INGESTION_INTERVAL_SECONDS", "60"))
