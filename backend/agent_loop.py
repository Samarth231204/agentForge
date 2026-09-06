"""A minimal, hand-rolled ReAct-style tool-calling loop.

Replaces CrewAI's own agent-execution engine, whose internals were the direct
source of several real bugs found during development:

- Forcing a plain-text final answer by omitting `tools` from the request
  does NOT reliably stop the model from calling one anyway — Groq's server
  treats "no tools present" as an implicit tool_choice="none" and rejects
  the attempt just as hard as it would an explicit hint (CrewAI's own
  forced-final-answer mechanism hit exactly this). Fighting a model's
  tool-calling bias doesn't work; this loop works with it instead by giving
  the model a dedicated `final_answer` tool to call when it's done, so
  finishing the task is itself a tool call rather than something to detect
  by the *absence* of one.
- A tool call's result — success or error — is always fed back to the model
  as a normal tool message, so the agent can react to and recover from a
  failure instead of the whole task crashing (CrewAI let a raised tool
  exception propagate and kill the entire crew).

Per-call corruption recovery and cross-call model/provider fallback both
live in llm_fallback.py, shared with every other workflow — this module
just drives the tool-calling loop itself on top of that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from backend.llm_fallback import complete_with_fallback

_FINAL_ANSWER_TOOL = "final_answer"
_FINAL_ANSWER_SCHEMA = {
    "type": "function",
    "function": {
        "name": _FINAL_ANSWER_TOOL,
        "description": "Call this when you have finished the task (or determined it cannot be completed), with your complete answer.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string", "description": "Your complete final answer to the task."}},
            "required": ["answer"],
        },
    },
}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the tool's arguments
    execute: Callable[..., str]

    def to_function_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def run_agent(
    system_prompt: str,
    user_prompt: str,
    tools: list[ToolSpec],
    candidates: list[tuple[str, str]],
    temperature: float = 0.2,
    max_iterations: int = 25,
    label: str = "agent",
) -> str:
    """Run a tool-calling loop until the model calls final_answer (or gives a
    plain-text reply, accepted as a fallback). max_iterations is deliberately
    generous — a circuit breaker against a genuinely runaway loop (the model
    never calling final_answer at all), not a routine per-task budget to tune.
    Tightly-tuned small budgets (4-10) were tried and consistently proved
    wrong in both directions: too low cut off real multi-step tasks (a wrong
    selector guess, retried once, could exhaust a budget of 4 on its own),
    and there's no reliable way to predict how many turns a given page
    actually needs. Let the model's own final_answer call be the real
    stopping condition instead.

    `candidates` is a prioritized list of (model, api_key) pairs — see
    llm_fallback.build_llm_candidates(). If one is exhausted mid-task,
    complete_with_fallback advances to the next for the *next* call, on this
    same, unbroken conversation: nothing already accomplished (navigation,
    prior tool results) is lost, since that's all just conversation history
    at this point, not tied to whichever model is currently answering.

    `label` identifies this call in the iteration-exhaustion message only
    (e.g. "Booking Agent") — useful for tracing which stage produced a
    failure instead of surfacing one generic, unattributed message."""
    system_prompt = (
        f"{system_prompt}\n\nWhen you have completed the task (or determined it cannot be "
        f"completed), call the '{_FINAL_ANSWER_TOOL}' tool with your complete answer as its "
        "'answer' argument — that is how you finish, not by writing the answer as plain text."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_by_name = {tool.name: tool for tool in tools}
    schemas = [tool.to_function_schema() for tool in tools] + [_FINAL_ANSWER_SCHEMA]
    trace: list[str] = []

    for _ in range(max_iterations):
        response = complete_with_fallback(candidates, temperature=temperature, messages=messages, tools=schemas, tool_choice="auto")
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            # No tool call at all: accept plain text as a fallback rather
            # than forcing another round, since forcing one doesn't reliably
            # work (see module docstring).
            if message.content:
                return message.content
            trace.append("(empty response, no tool call)")
            continue

        for tc in tool_calls:
            if tc.function.name == _FINAL_ANSWER_TOOL:
                try:
                    return json.loads(tc.function.arguments or "{}").get("answer") or "The agent finished without providing an answer."
                except json.JSONDecodeError:
                    return tc.function.arguments or "The agent finished without providing an answer."

        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in tool_calls],
        })
        for tc in tool_calls:
            tool = tool_by_name.get(tc.function.name)
            if tool is None:
                result = f"Error: unknown tool '{tc.function.name}'."
            else:
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                    result = tool.execute(**arguments)
                except Exception as exc:
                    result = f"Error: {exc}"
            trace.append(f"{tc.function.name}({tc.function.arguments}) -> {str(result)[:150]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    trace_block = "\n".join(trace[-6:]) or "(no tool calls were made)"
    return (
        f"The {label} could not finish within its iteration budget. Last actions taken:\n{trace_block}"
    )
