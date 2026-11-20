"""Live check: every table must have row-level security enabled. Deselected by
default (requires a real DATABASE_URL) - run explicitly with `pytest -m live`
after migrations 0001 and 0002 have been applied.
"""
import os

import pytest

pytestmark = pytest.mark.live

EXPECTED_TABLES = [
    "chunks", "official_links", "messages", "leads", "agent_runs", "run_steps",
    "tool_calls", "policy_violations", "decisions", "sent_log", "crm_sync",
    "rejected_events",
]


def test_every_table_has_rls_enabled():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select relname, relrowsecurity from pg_class "
            "where relname = any(%s) and relkind = 'r'",
            (EXPECTED_TABLES,),
        )
        rows = {name: enabled for name, enabled in cur.fetchall()}

    missing = set(EXPECTED_TABLES) - set(rows)
    assert not missing, f"tables not found (migration not applied?): {missing}"

    not_protected = [name for name, enabled in rows.items() if not enabled]
    assert not not_protected, f"RLS not enabled on: {not_protected}"
