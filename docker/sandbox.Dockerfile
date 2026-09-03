FROM python:3.12-slim

RUN useradd --create-home --uid 10001 sandboxuser \
    && apt-get update \
    && apt-get install --no-install-recommends -y git \
    && pip install --no-cache-dir pytest ruff black \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/agentforge \
    && printf '%s\n' '#!/bin/sh' 'case "$1" in *Username*) echo x-access-token ;; *) echo "$GITHUB_PAT" ;; esac' > /opt/agentforge/git-askpass \
    && chmod 0555 /opt/agentforge/git-askpass

WORKDIR /sandbox
USER sandboxuser
