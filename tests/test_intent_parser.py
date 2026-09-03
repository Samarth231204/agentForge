import json
from types import SimpleNamespace

from backend.intent_parser import IntentParser


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(self.replies)))])


def test_parses_research_intent_from_model_json():
    client = FakeClient([json.dumps({"intent": "research", "confidence": 0.9, "reason": "Research request", "task_summary": "Find patterns", "constraints": []})])
    result = IntentParser(client=client).parse("Find FastAPI SSE patterns")
    assert result.intent == "research"
    assert result.confidence == 0.9


def test_forces_email_requests_to_draft_only():
    result = IntentParser(client=FakeClient([])).parse("Send email to the launch team")
    assert result.intent == "draft_email"
    assert "cannot send" in result.reason


def test_retries_invalid_json_then_falls_back():
    client = FakeClient(["not json", "still not json"])
    result = IntentParser(client=client).parse("Do something unclear")
    assert result.intent == "unsupported"
    assert result.confidence == 0.0


def test_routes_github_action_without_calling_model():
    result = IntentParser(client=FakeClient([])).parse("Open a pull request on GitHub")
    assert result.intent == "github"
