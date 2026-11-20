"""Unit tests must never touch a real network or database regardless of what
.env happens to be loaded in the environment - caught live: on Linux (where
psycopg actually works, unlike this project's Windows dev machine), several
"unit" tests silently made real Supabase calls through the run_steps/
tool_calls/policy_violations instrumentation in app/graph.py and app/nodes/*,
because they only mocked the repo functions the test itself cared about, not
every repo write a node might make. DATABASE_URL is stripped for every test
EXCEPT ones marked `live`, so any unmocked DB call fails fast (MissingDatabaseUrl,
caught by the existing try/except-pass guards) instead of making a slow real
network round trip that still passes but silently takes minutes.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_real_database_by_default(request, monkeypatch):
    if "live" in request.keywords:
        return
    monkeypatch.delenv("DATABASE_URL", raising=False)
