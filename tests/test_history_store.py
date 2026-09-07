from backend.history_store import MAX_ENTRIES_PER_SESSION, InMemoryHistoryStore, get_history_store


def test_append_list_round_trip():
    store = InMemoryHistoryStore()
    assert store.list("session-1") == []

    store.append("session-1", {"prompt": "first"})
    store.append("session-1", {"prompt": "second"})
    assert store.list("session-1") == [{"prompt": "first"}, {"prompt": "second"}]


def test_sessions_are_isolated_from_each_other():
    store = InMemoryHistoryStore()
    store.append("session-a", {"prompt": "a"})
    store.append("session-b", {"prompt": "b"})
    assert store.list("session-a") == [{"prompt": "a"}]
    assert store.list("session-b") == [{"prompt": "b"}]


def test_history_is_capped_at_max_entries_per_session():
    store = InMemoryHistoryStore()
    for i in range(MAX_ENTRIES_PER_SESSION + 10):
        store.append("session-1", {"prompt": f"task-{i}"})

    entries = store.list("session-1")
    assert len(entries) == MAX_ENTRIES_PER_SESSION
    # The oldest entries are dropped first; the most recent ones survive, in order.
    assert entries[0] == {"prompt": "task-10"}
    assert entries[-1] == {"prompt": f"task-{MAX_ENTRIES_PER_SESSION + 9}"}


def test_get_history_store_returns_a_process_wide_singleton():
    assert get_history_store() is get_history_store()
