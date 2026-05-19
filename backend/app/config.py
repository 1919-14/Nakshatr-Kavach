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
GROQ_CHAT_API_KEY = os.environ.get("GROQ_API_KEY1", GROQ_API_KEY)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
INGESTION_INTERVAL_SECONDS = int(os.environ.get("INGESTION_INTERVAL_SECONDS", "60"))
