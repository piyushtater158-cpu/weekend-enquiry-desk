"""approval: the human-in-the-loop interrupt. Pauses the graph, persists state via
the checkpointer, and resumes with Command(resume={"action": ..., ...}) on the
same thread_id. An edit replaces the draft body and routes back through
policy_node (is_recheck=True) rather than straight to ops - this is the
eng-review fix that makes "zero invented prices/availability" true for edited
drafts too, not just unedited ones.

Note: a node containing interrupt() re-executes from the top on every resume -
any side effect (like the Telegram ping) belongs in a separate node BEFORE this
one (see app/nodes/notify_node.py), never inside approval() itself, or it fires
once per resume instead of once per draft.
"""
from __future__ import annotations

from langgraph.types import interrupt

from app.state import GraphState


def approval(state: GraphState) -> dict:
    payload = interrupt({
        "draft": state.get("draft"),
        "policy_result": state.get("policy_result"),
        "lead_id": state.get("lead_id"),
    })

    action = payload["action"]
    updates: dict = {"decision": action, "run_status": "running"}

    if action == "edit":
        current_draft = state.get("draft") or {"sentences": [], "urls": []}
        # None = client omitted sentences → keep original.
        # [] = client sent an empty list → keep that empty list (do not collapse
        # via `or`, which treated [] as missing and silently dropped edits).
        if "edited_sentences" in payload and payload["edited_sentences"] is not None:
            edited_sentences = payload["edited_sentences"]
        else:
            edited_sentences = current_draft.get("sentences", [])
        updates["draft"] = {"sentences": edited_sentences, "urls": current_draft.get("urls", [])}
        updates["is_recheck"] = True

    return updates
