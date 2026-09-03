def test_groq_compatibility_patch_preserves_messages():
    import crewai.llms.cache as crewai_cache

    from backend.crewai_compat import disable_unsupported_cache_breakpoints

    disable_unsupported_cache_breakpoints()
    message = {"role": "system", "content": "You are helpful."}
    assert crewai_cache.mark_cache_breakpoint(message) == message
    assert "cache_breakpoint" not in message
