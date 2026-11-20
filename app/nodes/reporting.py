"""reporting: runs on every run, success or failure - not a post-hoc aggregate.
Writes missing_fact onto escalated/dismissed leads (the tag the content-gap
report counts); the /api/content-gap and /api/demand-signal endpoints are a
thin read-side query over that tag and over leads generally, with no separate
aggregation job of their own.
"""
from __future__ import annotations

from app import repo
from app.state import GraphState


def _derive_missing_fact(state: GraphState) -> str | None:
    policy_result = state.get("policy_result") or {}
    violations = policy_result.get("violations") or []
    if violations:
        v = violations[0]
        return f"{v['rule_id']}: {v['detail']}"

    if state.get("error_kind"):
        return f"{state['error_kind']}: {state.get('error_detail', '')}"

    intent = state.get("intent")
    if intent in ("group_quote", "partnership", "other"):
        return f"intent={intent}, no self-serve answer in corpus"

    return None


def reporting(state: GraphState) -> dict:
    ops_action = state.get("ops_action")
    lead_id = state.get("lead_id")

    if lead_id and ops_action in ("escalate", "dismiss"):
        missing_fact = _derive_missing_fact(state)
        if missing_fact:
            repo.set_lead_missing_fact(lead_id=lead_id, missing_fact=missing_fact)

    final_status = "error" if state.get("run_status") == "error" else state.get("run_status", "done")
    repo.finish_agent_run(
        run_id=state["run_id"], status=final_status,
        error_kind=state.get("error_kind"), error_detail=state.get("error_detail"),
    )

    return {"run_status": final_status}
