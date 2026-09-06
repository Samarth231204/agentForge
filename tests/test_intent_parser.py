import json
from types import SimpleNamespace

from backend.intent_parser import IntentParser


class FakeClient:
    """Mimics litellm.completion's call signature: a plain callable returning
    an object shaped like a ModelResponse (.choices[0].message.content)."""

    def __init__(self, replies):
        self.replies = iter(replies)

    def __call__(self, **_kwargs):
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


def test_advances_to_the_next_candidate_model_on_a_provider_error():
    import litellm

    models_tried = []

    def flaky_client(*, model, **_kwargs):
        models_tried.append(model)
        if len(models_tried) == 1:
            raise litellm.RateLimitError(message="rate_limit_exceeded", model=model, llm_provider="groq")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"intent": "research", "confidence": 0.8, "reason": "ok", "task_summary": "x", "constraints": []})))])

    result = IntentParser(client=flaky_client).parse("Do something unclear")
    assert result.intent == "research"
    assert len(models_tried) == 2
    assert models_tried[0] != models_tried[1]  # actually moved to a different candidate model


def test_routes_github_action_without_calling_model():
    result = IntentParser(client=FakeClient([])).parse("Open a pull request on GitHub")
    assert result.intent == "github"


def test_routes_booking_action_without_calling_model():
    result = IntentParser(client=FakeClient([])).parse("Book a table for two on Friday")
    assert result.intent == "booking"


def test_routes_url_navigation_requests_to_booking_even_when_phrased_as_a_question():
    # Regression: this phrasing was previously misclassified as research (it
    # reads like an information lookup) and never reached the booking crew.
    result = IntentParser(client=FakeClient([])).parse(
        "Go to https://en.wikipedia.org, search for 'Alan Turing' using the search box, "
        "and tell me the first sentence of the article you land on."
    )
    assert result.intent == "booking"


def test_routes_login_and_payment_requests_to_unsupported():
    result = IntentParser(client=FakeClient([])).parse("Log in to my account and pay for the order")
    assert result.intent == "unsupported"


def test_routes_named_site_navigation_to_booking_without_a_literal_url():
    # Regression: "Open YouTube..." has no URL and no booking keyword, so it
    # fell through to the LLM classifier, which failed to return valid JSON
    # and landed on the generic "could not classify" fallback in production.
    result = IntentParser(client=FakeClient([])).parse('Open YouTube and give the link of first three videos, when you search "space science"')
    assert result.intent == "booking"


def test_generic_open_phrasing_does_not_trigger_booking():
    client = FakeClient([json.dumps({"intent": "research", "confidence": 0.6, "reason": "generic", "task_summary": "x", "constraints": []})])
    result = IntentParser(client=client).parse("Open a new tab and tell me a joke")
    assert result.intent == "research"  # falls through to the (fake) model rather than being forced to booking


def test_routes_file_creation_to_github_when_repo_context_is_present_without_calling_model():
    # Regression: a sidebar-configured repo URL/token is a strong session
    # signal that never reached the classifier before, so an otherwise
    # keyword-free file request ("create welcome.html...") fell through to
    # the LLM and, if that call failed to return valid JSON (as happened in
    # production), landed on the generic "could not classify" fallback.
    result = IntentParser(client=FakeClient([])).parse(
        'create a new file called "welcome.html" and write an alternate welcome page in it',
        has_repo_context=True,
    )
    assert result.intent == "github"


def test_same_file_request_is_not_forced_to_github_without_repo_context():
    client = FakeClient([json.dumps({"intent": "unsupported", "confidence": 0.5, "reason": "no repo context", "task_summary": "x", "constraints": []})])
    result = IntentParser(client=client).parse('create a new file called "welcome.html"', has_repo_context=False)
    assert result.intent == "unsupported"  # falls through to the (fake) model rather than being forced
