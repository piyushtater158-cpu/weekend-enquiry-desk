"""intake: the first model call. Classifies intent, resolves date/party/pet/kid
flags and language, and writes the leads row as 'new' - leads is owned here, not
by ops, so a run that fails later still has a lead the queue and the
demand-signal report can see.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from app import llm, repo
from app.state import GraphState

_SYSTEM_PROMPT = """You are the intake classifier for a weekend beach brand's guest-enquiry desk.
Classify the guest message into exactly this JSON shape, nothing else:
{"intent": "faq|tickets|camping|room|group_quote|partnership|other",
 "event_date": "YYYY-MM-DD or null",
 "party_size": integer or null,
 "pet_flag": true/false,
 "kid_flag": true/false,
 "language": "ISO 639-1 code, or 'mixed' for code-switched text",
 "mentions_camping_bundle_question": true if the guest is asking whether camping
   is included/bundled with the day pass, else false}
Resolve relative dates ("this Saturday") against today's date, given below.
Never guess a date for intents with no date reference - use null.
"""


def _today() -> date:
    override = os.environ.get("DEMO_NOW")
    if override:
        return date.fromisoformat(override)
    return datetime.now(timezone.utc).date()


def intake(state: GraphState) -> dict:
    today = _today()
    try:
        result = llm.call_llm(
            _SYSTEM_PROMPT,
            f"Today's date is {today.isoformat()}. Guest message: {state['body']}",
        )
    except llm.LLMTimeout:
        lead_id = repo.create_lead(
            message_id=state["message_id"], intent="other", event_date=None,
            party_size=None, pet_flag=False, kid_flag=False, language=None,
        )
        repo.update_lead_status(lead_id=lead_id, status="escalated")
        return {
            "run_status": "error",
            "error_kind": "model_timeout",
            "error_detail": "intake LLM call timed out",
            "lead_id": lead_id,
            "lead_status": "escalated",
        }

    intent = result.get("intent") or "other"
    valid_intents = {"faq", "tickets", "camping", "room", "group_quote", "partnership", "other"}
    if intent not in valid_intents:
        intent = "other"

    lead_id = repo.create_lead(
        message_id=state["message_id"],
        intent=intent,
        event_date=result.get("event_date"),
        party_size=result.get("party_size"),
        pet_flag=bool(result.get("pet_flag")),
        kid_flag=bool(result.get("kid_flag")),
        language=result.get("language"),
    )

    return {
        "intent": intent,
        "event_date": result.get("event_date"),
        "party_size": result.get("party_size"),
        "pet_flag": bool(result.get("pet_flag")),
        "kid_flag": bool(result.get("kid_flag")),
        "language": result.get("language"),
        "mentions_camping_bundle_question": bool(result.get("mentions_camping_bundle_question")),
        "lead_id": lead_id,
        "lead_status": "new",
        "run_status": "running",
    }
