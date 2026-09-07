from backend.auth.token_store import TokenStore, get_token_store


def test_save_get_has_delete_round_trip():
    store = TokenStore()
    assert store.has("session-1") is False
    assert store.get("session-1") is None

    store.save("session-1", {"token": "abc"})
    assert store.has("session-1") is True
    assert store.get("session-1") == {"token": "abc"}

    store.delete("session-1")
    assert store.has("session-1") is False
    assert store.get("session-1") is None


def test_sessions_are_isolated_from_each_other():
    store = TokenStore()
    store.save("session-a", {"token": "a"})
    store.save("session-b", {"token": "b"})
    assert store.get("session-a") == {"token": "a"}
    assert store.get("session-b") == {"token": "b"}
    store.delete("session-a")
    assert store.get("session-b") == {"token": "b"}


def test_get_token_store_returns_a_process_wide_singleton():
    assert get_token_store() is get_token_store()
