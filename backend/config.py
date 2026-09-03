"""Application configuration loaded from the project .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Raised when the application cannot be started safely."""


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str = "openai/gpt-oss-20b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    backend_base_url: str = "http://localhost:8000"
    streamlit_port: int = 8501
    sandbox_image: str = "agentforge-sandbox:local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load configuration once, without ever logging secret values."""
    load_dotenv()
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_api_key:
        raise ConfigurationError(
            "GROQ_API_KEY is required. Add it to the project .env file before starting AgentForge."
        )

    try:
        backend_port = int(os.getenv("BACKEND_PORT", "8000"))
        streamlit_port = int(os.getenv("STREAMLIT_PORT", "8501"))
    except ValueError as exc:
        raise ConfigurationError("BACKEND_PORT and STREAMLIT_PORT must be integers.") from exc

    return Settings(
        groq_api_key=groq_api_key,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip(),
        backend_host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        backend_port=backend_port,
        backend_base_url=os.getenv("BACKEND_BASE_URL", "http://localhost:8000"),
        streamlit_port=streamlit_port,
        sandbox_image=os.getenv("SANDBOX_IMAGE", "agentforge-sandbox:local").strip(),
    )
