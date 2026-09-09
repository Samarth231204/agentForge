"""Shared LLM-completion resilience, used by every workflow that calls an LLM.

Two independent layers of resilience, composed together:

1. Per-call: a single (model, api_key) candidate gets up to 3 attempts if
   Groq's gpt-oss models corrupt their own tool-call name via internal
   "harmony" formatting tokens (e.g. calling "browser<|channel|>commentary"
   instead of "browser") or produce entirely unparseable output. Groq's
   rejection still carries the model's fully-formed intended call in its
   `failed_generation` field (name corrupted, arguments valid JSON), so the
   first attempt at recovery is parsing that out and executing the intended
   call directly — not resending an unchanged request and hoping for
   different output, since in production this reproduced with
   byte-identical broken output on consecutive retries. Blind retry is a
   fallback only for when that parsing itself fails.

2. Across calls: a prioritized list of (model, api_key) candidates. On any
   transient provider error (rate limit, 503, unavailable — or a candidate
   exhausting its own retries above), advance to the next candidate for the
   *same* request rather than failing the whole task. Groq scopes its rate
   limits per model, not per account, so simply trying a different model on
   the same key often has an entirely untouched quota.

Every workflow (research, email, GitHub, intent classification, booking)
builds its candidate list via build_llm_candidates() and calls
complete_with_fallback() instead of litellm.completion() directly, so a
single exhausted model/key never fails a task outright while an untried
alternative is available.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import litellm

# LiteLLM prints a "Give Feedback / Get Help" banner to stderr on every
# exception it catches internally, including ones this module's own
# retry/fallback logic is designed to catch and recover from — that noise
# made it hard to tell an expected, handled candidate failure apart from an
# actual task failure in the logs. This module is imported by every
# LLM-calling workflow, so setting it once here applies everywhere.
litellm.suppress_debug_info = True

if TYPE_CHECKING:
    from backend.config import Settings

_CORRUPTED_TOOL_CALL_MARKERS = (
    "not in request.tools",
    "tool call validation failed",
    "tool_use_failed",
    "output_parse_failed",
)
# "resourceexhausted" (no separator) and "limit reached" were added after a
# real OpenRouter model (nvidia/nemotron-3-nano-omni via an upstream Google
# proxy) returned "Upstream error from Nvidia: ResourceExhausted: Worker
# local total request limit reached (16/16)" — a genuine rate limit that the
# original Groq-shaped markers didn't recognize, so complete_with_fallback()
# raised instead of advancing to the next candidate. Different providers
# format the same condition differently; this list is deliberately loose
# rather than assuming every provider matches Groq's exact wording.
_RATE_LIMIT_MARKERS = ("rate_limit_exceeded", "resource_exhausted", "resourceexhausted", "limit reached", "429", "503", "unavailable")

# Fixed Groq fallback chain, tried after whatever GROQ_MODEL is configured as
# primary. Rate limits are per-model on Groq, so these give fresh quota pools
# even when the primary model (and its own key) is fully exhausted.
_GROQ_FALLBACK_MODELS = ("openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b")


def is_rate_limited(exc: Exception) -> bool:
    """Recognize a transient, retry-worthy provider error. Deliberately
    excludes plain "400" — a malformed-request error is permanent and
    retrying it (on any candidate) would just fail identically."""
    text = str(exc).lower()
    return exc.__class__.__name__ == "RateLimitError" or any(marker in text for marker in _RATE_LIMIT_MARKERS)


def is_json_validation_failure(exc: Exception) -> bool:
    """Recognize Groq's own server-side JSON-schema rejection for a
    response_format={"type": "json_object"} request — raised as a
    BadRequestError ("Failed to validate JSON... json_validate_failed")
    *instead of* returning malformed content. This is the same underlying
    problem intent_parser.py's/pipeline_planner.py's malformed-JSON retry
    already handles (the model failed to produce a valid structured
    response) — but that retry only triggers on a successful response
    whose content fails json.loads()/Pydantic validation; it never sees
    this case, since here there's no content at all, only a raised
    exception. Found live: a pipeline_planner.py request (a more complex
    nested schema than intent_parser.py's) tripped this and propagated as
    an unhandled 500, uncaught by either the malformed-JSON retry or
    is_rate_limited(). Callers should retry the same candidate on this,
    exactly as they already do for a malformed (but present) JSON reply."""
    return "json_validate_failed" in str(exc).lower()


def build_llm_candidates(settings: "Settings") -> list[tuple[str, str]]:
    """The primary configured Groq model first, then the fixed Groq fallback
    chain, then every configured OpenRouter model last, in order, if (and
    only if) OPENROUTER_API_KEY is set — added automatically the moment it
    is, no code changes needed. Multiple OpenRouter models matter here: its
    free tier is itself rate-limited per model, and free models occasionally
    get discontinued outright (confirmed directly — the model this project
    originally shipped with, minimax/minimax-m3:free, stopped working
    entirely), so a single hardcoded OpenRouter model is a fragile last
    resort. Order preserved, duplicates (e.g. the primary model already
    being one of the fallbacks) collapsed."""
    candidates = [(f"groq/{settings.groq_model}", settings.groq_api_key)]
    candidates += [(f"groq/{model}", settings.groq_api_key) for model in _GROQ_FALLBACK_MODELS]
    if settings.openrouter_api_key:
        candidates += [(f"openrouter/{model}", settings.openrouter_api_key) for model in settings.openrouter_models]

    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for model, api_key in candidates:
        if model not in seen:
            seen.add(model)
            deduped.append((model, api_key))
    return deduped


def _recover_corrupted_tool_call(exc: litellm.BadRequestError) -> Any | None:
    """Parse the model's actually-intended tool call back out of Groq's
    rejection body. Returns None if the body doesn't contain a parseable
    failed_generation (e.g. the output_parse_failed variant, which comes
    back empty) — the caller falls back to blind retry in that case."""
    match = re.search(r"\{.*\}", str(exc), re.DOTALL)
    if not match:
        return None
    try:
        outer = json.loads(match.group(0))
        inner = json.loads(outer["error"]["failed_generation"])
        clean_name = str(inner.get("name", "")).split("<|")[0].strip()
        arguments = inner.get("arguments", {})
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError):
        return None
    if not clean_name:
        return None
    return SimpleNamespace(id="recovered-corrupted-call", function=SimpleNamespace(name=clean_name, arguments=json.dumps(arguments)))


def _complete_one_candidate(**kwargs: Any) -> Any:
    """litellm.completion against a single (model, api_key), with up to 3
    attempts if the model corrupts its own tool-call output (see module
    docstring). Any other exception (including a rate limit) propagates
    immediately for complete_with_fallback to act on."""
    last_exc: litellm.BadRequestError | None = None
    for _ in range(3):
        try:
            return litellm.completion(**kwargs)
        except litellm.BadRequestError as exc:
            if not any(marker in str(exc).lower() for marker in _CORRUPTED_TOOL_CALL_MARKERS):
                raise
            recovered = _recover_corrupted_tool_call(exc)
            if recovered is not None:
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[recovered]))])
            last_exc = exc
    raise last_exc


def complete_with_fallback(candidates: list[tuple[str, str]], **kwargs: Any) -> Any:
    """Try each (model, api_key) candidate in order for the same request,
    advancing on any transient provider error. Raises the last error if
    every candidate is exhausted; a permanent error (anything
    is_rate_limited doesn't recognize) propagates immediately without
    trying further candidates, since they'd fail identically."""
    if not candidates:
        raise ValueError("complete_with_fallback requires at least one candidate.")
    last_exc: Exception | None = None
    for model, api_key in candidates:
        try:
            return _complete_one_candidate(model=model, api_key=api_key, **kwargs)
        except Exception as exc:
            if not is_rate_limited(exc):
                raise
            last_exc = exc
    raise last_exc
