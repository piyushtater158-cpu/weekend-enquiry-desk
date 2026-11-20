"""The three tools named in the brief: search_knowledge_base, get_official_links,
escalate_for_human. Every call is logged by the caller into tool_calls (see
app/graph.py), so these functions stay pure and side-effect-free - they do not
write to the database themselves.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def search_knowledge_base() -> list[dict]:
    """Returns the full labelled corpus with stable section ids. No vector search,
    no top-k - see the design doc's P2' (pgvector was dropped: at 12-24 chunks,
    full context beats similarity search, especially for multi-intent messages).
    'Empty retrieval' is not a reachable state because the corpus is always fully
    present; this is why it is not one of the three error fixtures.
    """
    chunks = json.loads((DATA_DIR / "corpus" / "chunks.json").read_text(encoding="utf-8"))
    return chunks


def published_weekends() -> list[dict]:
    """The schema.org/Event JSON-LD data scraped alongside the prose chunks - the
    authoritative source for PR-11's published-weekends check.
    """
    return json.loads((DATA_DIR / "corpus" / "events.json").read_text(encoding="utf-8"))


def get_official_links() -> list[dict]:
    return json.loads((DATA_DIR / "official_links.json").read_text(encoding="utf-8"))


def corpus_max_accessed_at() -> datetime:
    chunks = search_knowledge_base()
    stamps = [datetime.fromisoformat(c["accessed_at"]) for c in chunks]
    return max(stamps) if stamps else datetime.now(timezone.utc)


def escalate_for_human(*, lead_id: str, reason: str) -> dict:
    """Marks the escalation reason. The actual lead-status write happens in the
    ops node (which owns leads.status transitions) - this function's job is only
    to produce the structured escalation record the caller logs as a tool call.
    """
    return {"lead_id": lead_id, "reason": reason, "escalated_at": datetime.now(timezone.utc).isoformat()}
