"""Postgres connection helper. Direct psycopg, never the Supabase Data API/anon key
(see migrations/0002_rls_lockdown.sql and the design doc's P7).

DATABASE_URL must point at the Supabase SESSION pooler (port 5432), not the
transaction pooler (6543) - the LangGraph PostgresSaver checkpointer uses psycopg
prepared statements, which the transaction pooler does not support.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg


class MissingDatabaseUrl(RuntimeError):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise MissingDatabaseUrl(
            "DATABASE_URL is not set. Get it from Supabase -> Project Settings -> "
            "Database -> Connection string, SESSION pooler (port 5432), not the "
            "6543 transaction pooler (the LangGraph checkpointer needs prepared "
            "statement support)."
        )
    if ":6543" in url:
        raise MissingDatabaseUrl(
            "DATABASE_URL uses port 6543 (the transaction pooler). The LangGraph "
            "PostgresSaver checkpointer needs the session pooler on port 5432."
        )
    return url


def get_connection() -> "psycopg.Connection":
    """A fresh connection, prepare_threshold disabled defensively even though the
    session pooler supports prepared statements - cheap insurance against a future
    misconfigured DATABASE_URL silently breaking in production instead of at startup.

    psycopg is imported here, not at module level: on this dev machine, a Windows
    Application Control policy blocks psycopg's native pq DLL entirely, which would
    otherwise break every module that merely imports app.db (including unit tests
    that never touch the database). The Linux Docker deployment is unaffected -
    this is a local-dev-only workaround, not a production code path change.
    """
    import psycopg

    return psycopg.connect(get_database_url(), prepare_threshold=None, autocommit=True)


@lru_cache(maxsize=1)
def checkpointer_connection_string() -> str:
    """Separate accessor (vs. reusing get_connection's DSN directly) so call sites
    that need the raw string for PostgresSaver.from_conn_string() don't have to
    import psycopg at all.
    """
    return get_database_url()
