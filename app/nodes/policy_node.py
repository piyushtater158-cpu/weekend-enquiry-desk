"""policy: deterministic. Wraps app/policy.py's pure evaluate() with the real
corpus/links/events context. Pass -> approval. Fail -> one content repair
attempt; if that also fails, still park at human approval with the draft
(nothing is conclusive until a human approve/edit/dismiss). Human Approve
sends/logs even when policy still fails (operator override); an edited draft
that fails re-check escalates with no override. This node also re-runs on an
edited draft before ops (the eng-review fix) - the graph wiring for that edge
lives in app/graph.py, not here; this node is agnostic to why it was called.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from app import repo
from app.policy import Draft, PolicyContext, Sentence, evaluate
from app.state import GraphState
from app.tools import corpus_max_accessed_at, get_official_links, published_weekends


def _now() -> datetime:
    override = os.environ.get("DEMO_NOW")
    if override:
        return datetime.fromisoformat(override).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _build_context(state: GraphState) -> PolicyContext:
    known_chunk_ids = {c["section_id"] for c in state.get("_chunks", [])}
    links = get_official_links()
    official_link_urls = {l["url"] for l in links}
    link_by_key = {l["key"]: l["url"] for l in links}

    weekends = [
        (
            datetime.fromisoformat(e["start_date"]).date(),
            datetime.fromisoformat(e["end_date"]).date(),
        )
        for e in published_weekends()
    ]

    return PolicyContext(
        known_chunk_ids=known_chunk_ids,
        official_link_urls=official_link_urls,
        published_weekends=weekends,
        corpus_max_accessed_at=corpus_max_accessed_at(),
        corpus_stale_days=int(os.environ.get("CORPUS_STALE_DAYS", "45")),
        day_pass_url=link_by_key.get("day_pass"),
        camping_url=link_by_key.get("camping"),
        now=_now(),
    )


def _build_draft(state: GraphState) -> Draft:
    raw = state.get("draft") or {"sentences": [], "urls": []}
    sentences = [
        Sentence(text=s["text"], cites=s.get("cites", []), non_factual=s.get("non_factual", False))
        for s in raw.get("sentences", [])
    ]
    event_date = state.get("event_date")
    return Draft(
        sentences=sentences,
        urls=raw.get("urls", []),
        intent=state.get("intent", "faq"),
        event_date=date.fromisoformat(event_date) if event_date else None,
        mentions_camping_bundle_question=bool(state.get("mentions_camping_bundle_question")),
    )


def policy_node(state: GraphState) -> dict:
    draft = _build_draft(state)
    context = _build_context(state)
    result = evaluate(draft, context)

    policy_result = {
        "passed": result.passed,
        "violations": [
            {"rule_id": v.rule_id, "severity": v.severity, "detail": v.detail}
            for v in result.violations
        ],
        "notes": result.notes,
    }

    run_id = state.get("run_id")
    if run_id:
        for v in result.violations:
            try:
                repo.record_policy_violation(run_id=run_id, rule_id=v.rule_id,
                                              severity=v.severity, detail=v.detail)
            except Exception:  # noqa: BLE001 - observability must not break the run
                pass

    is_recheck = bool(state.get("is_recheck"))
    updates: dict = {"policy_result": policy_result, "run_status": "running"}

    if result.passed:
        # A recheck that passes goes straight to ops with the edited body - not
        # back through a second human approval.
        updates["next_step"] = "ops" if is_recheck else "approval"
    elif is_recheck:
        # Edited draft fails its re-check: re-escalate, no override available.
        updates["next_step"] = "escalate"
    elif not state.get("repair_attempted"):
        updates["next_step"] = "repair"
        updates["repair_attempted"] = True
    else:
        # Repair exhausted and the draft still fails - do NOT auto-escalate.
        # Park at approval so every card stays editable. Human Approve is the
        # override (ops sends/logs); Edit rechecks policy; Dismiss drops it.
        updates["next_step"] = "approval"

    return updates
