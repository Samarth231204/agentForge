from backend import config
from backend.config import get_settings
from backend.memory_manager import Mem0MemoryManager, NullMemoryManager, get_memory_manager


def _isolate(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GROQ_API_KEY_1", "key-one")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GROQ_ACTIVE_KEY", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)


class FakeMem0Client:
    """Mimics mem0's real MemoryClient v2 API shape, confirmed directly
    against the live service: search() takes user_id inside filters (not as
    a top-level kwarg) and returns {"results": [...]}, not a bare list."""

    def __init__(self, search_results=None, raise_on_add=False, raise_on_search=False):
        self.search_results = search_results if search_results is not None else []
        self.raise_on_add = raise_on_add
        self.raise_on_search = raise_on_search
        self.added: list[tuple[str, str, dict | None]] = []

    def add(self, content, user_id, metadata=None):
        if self.raise_on_add:
            raise RuntimeError("Mem0 is down")
        self.added.append((content, user_id, metadata))

    def search(self, query, filters, limit=3, version="v2"):
        if self.raise_on_search:
            raise RuntimeError("Mem0 is down")
        return {"results": self.search_results}


def test_null_memory_manager_recall_returns_nothing():
    manager = NullMemoryManager()
    assert manager.recall("prefs:session-1", "anything") == []


def test_null_memory_manager_remember_is_a_no_op():
    manager = NullMemoryManager()
    manager.remember("prefs:session-1", "some content")  # must not raise


def test_mem0_memory_manager_remember_calls_client_add():
    client = FakeMem0Client()
    manager = Mem0MemoryManager(client)
    manager.remember("prefs:session-1", "User prefers a formal tone.")
    assert client.added == [("User prefers a formal tone.", "prefs:session-1", None)]


def test_mem0_memory_manager_recall_extracts_memory_text():
    client = FakeMem0Client(search_results=[{"memory": "User prefers a formal tone."}, {"memory": "User works at Acme Corp."}])
    manager = Mem0MemoryManager(client)
    assert manager.recall("prefs:session-1", "tone preference") == ["User prefers a formal tone.", "User works at Acme Corp."]


def test_mem0_memory_manager_remember_never_raises_on_client_failure():
    client = FakeMem0Client(raise_on_add=True)
    manager = Mem0MemoryManager(client)
    manager.remember("prefs:session-1", "some content")  # must not raise


def test_mem0_memory_manager_recall_returns_empty_list_on_client_failure():
    client = FakeMem0Client(raise_on_search=True)
    manager = Mem0MemoryManager(client)
    assert manager.recall("prefs:session-1", "anything") == []


def test_get_memory_manager_returns_null_manager_when_key_is_unset(monkeypatch):
    get_settings.cache_clear()
    get_memory_manager.cache_clear()
    _isolate(monkeypatch)
    assert isinstance(get_memory_manager(), NullMemoryManager)
    get_settings.cache_clear()
    get_memory_manager.cache_clear()


def test_get_memory_manager_falls_back_to_null_manager_when_client_init_fails(monkeypatch):
    """MemoryClient's constructor validates the key with a real network call
    and raises if it's invalid/unreachable — an outage or bad key must
    degrade to no-op memory, not crash every task that touches it."""
    get_settings.cache_clear()
    get_memory_manager.cache_clear()
    _isolate(monkeypatch)
    monkeypatch.setenv("MEM0_API_KEY", "some-key")

    class RaisingMemoryClient:
        def __init__(self, api_key):
            raise ValueError("Invalid API key.")

    monkeypatch.setattr("mem0.MemoryClient", RaisingMemoryClient)
    assert isinstance(get_memory_manager(), NullMemoryManager)
    get_settings.cache_clear()
    get_memory_manager.cache_clear()


def test_get_memory_manager_returns_a_process_wide_singleton(monkeypatch):
    get_settings.cache_clear()
    get_memory_manager.cache_clear()
    _isolate(monkeypatch)
    assert get_memory_manager() is get_memory_manager()
    get_settings.cache_clear()
    get_memory_manager.cache_clear()
