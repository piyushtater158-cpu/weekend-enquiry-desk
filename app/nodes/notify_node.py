"""Deterministic, runs exactly once (unlike approval, this node is never
re-executed on resume - only the node that actually calls interrupt() is).
Fires the Telegram draft-waiting ping right before the human-approval pause.
Also marks the lead awaiting_human so the queue REST resource matches interrupt.
"""
from __future__ import annotations

from app import notify, repo
from app.state import GraphState


def notify_node(state: GraphState) -> dict:
    lead_id = state.get("lead_id")
    if lead_id:
        try:
            repo.update_lead_status(lead_id=lead_id, status="awaiting_human")
        except Exception:  # noqa: BLE001 - status write must not block the ping/interrupt
            pass

    notify.notify_draft_waiting(
        lead_id=state.get("lead_id", ""),
        intent=state.get("intent", "faq"),
        body_preview=state.get("body", ""),
    )
    return {"run_status": "running", "lead_status": "awaiting_human"}
