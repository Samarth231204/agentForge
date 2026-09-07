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
    # The primary model tried first for every workflow (see llm_fallback.py
    # for the full resilience story: fixed Groq fallback chain, then
    # OpenRouter once configured, plus per-call recovery from gpt-oss's
    # "harmony" tool-call corruption bug). Groq's rate limits are scoped per
    # model, not per key or account, so a differently-exhausted model on the
    # *same* key often has an entirely untouched quota — that's what the
    # fallback chain in llm_fallback.py exploits automatically.
    groq_model: str = "openai/gpt-oss-20b"
    # Optional final fallback after every Groq candidate is exhausted. Only
    # used once both are non-empty — leave blank until you have an
    # OpenRouter account; no code changes needed to start using it later.
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    gemini_api_key: str = ""
    # A floating alias rather than a pinned version: Google moves it forward
    # automatically, whereas pinned versions (gemini-2.0-flash, gemini-2.5-flash)
    # have already been deprecated on this account. Same lesson learned the
    # hard way with a pinned Groq model in Phase 1 (see IMPLEMENTATION_PHASE_1.md).
    gemini_model: str = "gemini-flash-latest"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    backend_base_url: str = "http://localhost:8000"
    streamlit_port: int = 8501
    sandbox_image: str = "agentforge-sandbox:local"
    # Gmail OAuth2 (Phase 3). Optional — unset means the /auth/google routes
    # respond with a clear "not configured" error rather than failing
    # startup, since not every deployment needs email sending.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    # Optional — unset means Gmail tokens stay in the process-memory TokenStore
    # (erased on backend restart, never shared across instances). Set this to
    # move them to Redis instead, so a connected user's token expires on its
    # own schedule (gmail_token_ttl_seconds, sliding on each use) rather than
    # only ever being erased by chance of a restart.
    redis_url: str = ""
    gmail_token_ttl_seconds: int = 86400


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load configuration once, without ever logging secret values."""
    load_dotenv()
    # Two interchangeable keys, switched via GROQ_ACTIVE_KEY (1 or 2) — e.g.
    # to swap to a fresh key once the active one hits its daily quota.
    active_key = os.getenv("GROQ_ACTIVE_KEY", "1").strip()
    if active_key not in ("1", "2"):
        raise ConfigurationError("GROQ_ACTIVE_KEY must be '1' or '2'.")
    groq_api_key = os.getenv(f"GROQ_API_KEY_{active_key}", "").strip()
    if not groq_api_key:
        raise ConfigurationError(
            f"GROQ_API_KEY_{active_key} is required (GROQ_ACTIVE_KEY={active_key}). Add it to the project .env file before starting AgentForge."
        )

    try:
        backend_port = int(os.getenv("BACKEND_PORT", "8000"))
        streamlit_port = int(os.getenv("STREAMLIT_PORT", "8501"))
    except ValueError as exc:
        raise ConfigurationError("BACKEND_PORT and STREAMLIT_PORT must be integers.") from exc

    return Settings(
        groq_api_key=groq_api_key,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip(),
        backend_host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        backend_port=backend_port,
        backend_base_url=os.getenv("BACKEND_BASE_URL", "http://localhost:8000"),
        streamlit_port=streamlit_port,
        sandbox_image=os.getenv("SANDBOX_IMAGE", "agentforge-sandbox:local").strip(),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback").strip(),
        redis_url=os.getenv("REDIS_URL", "").strip(),
        gmail_token_ttl_seconds=int(os.getenv("GMAIL_TOKEN_TTL_SECONDS", "86400")),
    )
