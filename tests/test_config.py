import pytest

from backend import config
from backend.config import ConfigurationError, get_settings


def _isolate(monkeypatch):
    # Prevent load_dotenv() from reading this project's real .env file, so
    # these tests are isolated from whatever GROQ_ACTIVE_KEY/keys are
    # actually configured on disk.
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    for var in ("GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_ACTIVE_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_key_1_when_active_key_is_unset(monkeypatch):
    get_settings.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY_1", "key-one")
    monkeypatch.setenv("GROQ_API_KEY_2", "key-two")
    assert get_settings().groq_api_key == "key-one"
    get_settings.cache_clear()


def test_switches_to_key_2_when_active_key_is_2(monkeypatch):
    get_settings.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY_1", "key-one")
    monkeypatch.setenv("GROQ_API_KEY_2", "key-two")
    monkeypatch.setenv("GROQ_ACTIVE_KEY", "2")
    assert get_settings().groq_api_key == "key-two"
    get_settings.cache_clear()


def test_rejects_an_invalid_active_key_value(monkeypatch):
    get_settings.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY_1", "key-one")
    monkeypatch.setenv("GROQ_ACTIVE_KEY", "3")
    with pytest.raises(ConfigurationError, match="must be '1' or '2'"):
        get_settings()
    get_settings.cache_clear()


def test_missing_active_keys_env_var_raises_a_clear_error(monkeypatch):
    get_settings.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("GROQ_ACTIVE_KEY", "2")
    with pytest.raises(ConfigurationError, match="GROQ_API_KEY_2"):
        get_settings()
    get_settings.cache_clear()


def test_openrouter_models_defaults_to_an_empty_tuple_when_unset(monkeypatch):
    get_settings.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY_1", "key-one")
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    assert get_settings().openrouter_models == ()
    get_settings.cache_clear()


def test_openrouter_models_parses_a_comma_separated_list(monkeypatch):
    get_settings.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY_1", "key-one")
    monkeypatch.setenv("OPENROUTER_MODELS", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free, cohere/north-mini-code:free ,inclusionai/ling-3.0-flash-fin:free")
    assert get_settings().openrouter_models == (
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "cohere/north-mini-code:free",
        "inclusionai/ling-3.0-flash-fin:free",
    )
    get_settings.cache_clear()
