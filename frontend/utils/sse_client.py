"""HTTP/SSE adapter used by the Streamlit application."""

from __future__ import annotations

import json
from collections.abc import Generator, Iterable

import requests


def _parse_sse_lines(lines: Iterable[str | None]) -> Generator[dict, None, None]:
    """Yield JSON payloads as soon as each SSE event delimiter arrives."""
    data_lines: list[str] = []
    for line in lines:
        if line is None:
            continue
        if line == "":
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            data_lines = []
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())


def stream_task(
    backend_url: str,
    prompt: str,
    context: str,
    repo_url: str = "",
    github_token: str = "",
    session_id: str = "",
    gmail_session_id: str = "",
    history_session_id: str = "",
    approved_plan: dict | None = None,
) -> Generator[dict, None, None]:
    url = f"{backend_url.rstrip('/')}/tasks"
    # Streamlit uses the bounded JSON endpoint. The backend's /tasks/stream
    # endpoint remains available for API consumers that need raw SSE.
    # approved_plan (Phase 12): set only when the user reviewed and
    # approved a proposed multi-step plan — running one always goes through
    # this same endpoint/event contract, nothing new for the frontend to
    # render differently once execution actually starts.
    with requests.post(
        url,
        json={
            "prompt": prompt,
            "context": context,
            "repo_url": repo_url,
            "github_token": github_token,
            "session_id": session_id,
            "gmail_session_id": gmail_session_id,
            "history_session_id": history_session_id,
            "approved_plan": approved_plan,
        },
        timeout=(10, 600),
    ) as response:
        response.raise_for_status()
        yield from response.json()["events"]


def propose_pipeline_plan(
    backend_url: str,
    prompt: str,
    context: str = "",
    repo_url: str = "",
    github_token: str = "",
    gmail_session_id: str = "",
    revision_instruction: str = "",
    prior_plan: dict | None = None,
) -> dict:
    """Phase 12: asks the backend whether this request needs a multi-step
    plan, without running anything. Returns {"compound": False, "intent":
    ...} for an ordinary request (caller should just run it normally), or
    {"compound": True, "plan": {...} | None} for a multi-step request
    (None means the planner itself failed)."""
    response = requests.post(
        f"{backend_url.rstrip('/')}/pipeline/plan",
        json={
            "prompt": prompt,
            "context": context,
            "repo_url": repo_url,
            "github_token": github_token,
            "gmail_session_id": gmail_session_id,
            "revision_instruction": revision_instruction,
            "prior_plan": prior_plan,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def check_gmail_connected(backend_url: str, gmail_session_id: str) -> bool:
    if not gmail_session_id:
        return False
    try:
        response = requests.get(f"{backend_url.rstrip('/')}/auth/google/status", params={"state": gmail_session_id}, timeout=5)
        response.raise_for_status()
        return bool(response.json().get("connected"))
    except requests.RequestException:
        return False


def check_task_history(backend_url: str, history_session_id: str) -> list[dict]:
    if not history_session_id:
        return []
    try:
        response = requests.get(f"{backend_url.rstrip('/')}/history", params={"state": history_session_id}, timeout=5)
        response.raise_for_status()
        return response.json().get("tasks", [])
    except requests.RequestException:
        return []
