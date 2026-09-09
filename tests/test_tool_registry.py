from backend.tool_registry import TOOL_REGISTRY, get_tool_by_name, get_tool_names, get_tool_registry


def test_registry_contains_exactly_the_four_existing_tools():
    assert get_tool_names() == ("browser", "web_search", "github", "email")


def test_every_tool_has_a_non_empty_description_and_a_schema_with_required_fields():
    for tool in TOOL_REGISTRY:
        assert tool.description.strip() != ""
        assert tool.schema["type"] == "object"
        assert "properties" in tool.schema
        assert set(tool.schema["required"]).issubset(tool.schema["properties"].keys())


def test_get_tool_registry_returns_the_same_tuple_every_call():
    assert get_tool_registry() is get_tool_registry()


def test_get_tool_by_name_finds_a_real_tool():
    tool = get_tool_by_name("browser")
    assert tool is not None
    assert tool.name == "browser"


def test_get_tool_by_name_returns_none_for_an_unknown_tool():
    assert get_tool_by_name("does_not_exist") is None


def test_registry_metadata_matches_the_underlying_tool_modules():
    from backend.tools import browser_tool, email_tool, github_tool, web_search_tool

    assert get_tool_by_name("browser").schema is browser_tool.SCHEMA
    assert get_tool_by_name("web_search").schema is web_search_tool.SCHEMA
    assert get_tool_by_name("github").schema is github_tool.SCHEMA
    assert get_tool_by_name("email").schema is email_tool.SCHEMA
