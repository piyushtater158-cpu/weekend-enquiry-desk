"""Shared LangGraph state. A TypedDict, not a class with methods - every node
reads and writes a subset of these keys; LangGraph merges partial updates.
"""
from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class GraphState(TypedDict):
    message_id: str
    run_id: str
    thread_id: str
    body: str
    channel: str
    is_synthetic: bool
    external_ref: str

    intent: NotRequired[str]
    event_date: NotRequired[str | None]  # ISO date string
    party_size: NotRequired[int | None]
    pet_flag: NotRequired[bool]
    kid_flag: NotRequired[bool]
    language: NotRequired[str]
    mentions_camping_bundle_question: NotRequired[bool]

    lead_id: NotRequired[str]
    lead_status: NotRequired[str]

    _chunks: NotRequired[list[dict[str, Any]]]  # research's output, consumed by content/policy
    draft: NotRequired[dict[str, Any]]  # {"sentences": [...], "urls": [...]}
    policy_result: NotRequired[dict[str, Any]]
    repair_attempted: NotRequired[bool]

    sender_ref: NotRequired[str | None]

    decision: NotRequired[str]  # approve | edit | dismiss
    is_recheck: NotRequired[bool]  # True once an edited draft is back in policy_node
    next_step: NotRequired[str]  # repair | approval | escalate | ops - set by policy_node
    ops_action: NotRequired[str]  # dismiss | send | escalate - set by prepare_ops

    run_status: NotRequired[str]  # running | error | partial | done
    error_kind: NotRequired[str]
    error_detail: NotRequired[str]

    crm_status: NotRequired[str]  # synced | failed - set by ops() on a send, surfaced in the UI
    crm_error: NotRequired[str | None]
