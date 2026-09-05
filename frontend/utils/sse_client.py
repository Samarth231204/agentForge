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


def stream_task(backend_url: str, prompt: str, context: str, repo_url: str = "", github_token: str = "", session_id: str = "") -> Generator[dict, None, None]:
    url = f"{backend_url.rstrip('/')}/tasks"
    # Streamlit uses the bounded JSON endpoint. The backend's /tasks/stream
    # endpoint remains available for API consumers that need raw SSE.
    with requests.post(url, json={"prompt": prompt, "context": context, "repo_url": repo_url, "github_token": github_token, "session_id": session_id}, timeout=(10, 600)) as response:
        response.raise_for_status()
        yield from response.json()["events"]
