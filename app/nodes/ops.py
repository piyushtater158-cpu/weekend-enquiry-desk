"""ops: CRM sync + optional Twilio WhatsApp delivery. leads is already written
(at intake) and already carries its status through intermediate transitions -
ops's job is to decide dismiss vs. send vs. escalate for THIS run, record the
human decision, write the non-strippable sent_log row, optionally deliver via
Twilio sandbox, and sync to the CRM port. would_send_to stays named that way
even when a live sandbox send happens - the audit row is always written first.
"""
from __future__ import annotations

import os
import time

from app import repo
from app.crm import get_crm
from app.state import GraphState
from app.tools import escalate_for_human
from app.twilio_whatsapp import normalize_wa_address, send_whatsapp, whatsapp_send_enabled


def _ops_action(state: GraphState) -> str:
    if state.get("decision") == "dismiss":
        return "dismiss"
    # Explicit human Approve is the override: send even when policy still fails
    # (repair-exhausted drafts parked at approval). Failed edit rechecks keep
    # decision="edit" + next_step="escalate" and still escalate below.
    if state.get("decision") == "approve":
        return "send"
    if state.get("next_step") == "escalate" or not state.get("policy_result", {}).get("passed", False):
        return "escalate"
    return "send"


def _render_sent_log(draft: dict) -> tuple[str, dict]:
    body = " ".join(s["text"] for s in draft.get("sentences", []))
    citation_block = {"urls": draft.get("urls", []), "cites": sorted({
        c for s in draft.get("sentences", []) for c in s.get("cites", [])
    })}
    return body, citation_block


def _maybe_deliver_whatsapp(*, decision_id: str, state: GraphState, rendered_body: str) -> dict:
    """Attempt Twilio WhatsApp send after sent_log. Always records channel_deliveries
    when send is enabled and the destination is a WA address; otherwise no-op.
    """
    if not whatsapp_send_enabled():
        return {"wa_status": None}
    if state.get("channel") != "whatsapp":
        return {"wa_status": None}

    to_addr = normalize_wa_address(state.get("sender_ref"))
    if not to_addr:
        return {"wa_status": None}

    # Append official URLs when present so the guest gets the same links as the draft.
    urls = (state.get("draft") or {}).get("urls") or []
    body = rendered_body
    if urls:
        body = body.rstrip() + "\n\n" + "\n".join(urls)

    result = send_whatsapp(to=to_addr, body=body)
    try:
        repo.record_channel_delivery(
            decision_id=decision_id,
            provider="twilio_whatsapp",
            status=result.get("status") or "failed",
            external_id=result.get("external_id"),
            error=result.get("error"),
            to_address=result.get("to_address") or to_addr,
        )
    except Exception:  # noqa: BLE001 - audit must not break the run
        pass

    if state.get("run_id"):
        try:
            repo.record_tool_call(
                run_id=state["run_id"],
                tool_name="twilio_whatsapp_send",
                args_json={"to": to_addr, "body_chars": len(body)},
                result_json=result,
                ms=0,
                error=result.get("error") if result.get("status") == "failed" else None,
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "wa_status": result.get("status"),
        "wa_error": result.get("error"),
        "wa_external_id": result.get("external_id"),
    }


def ops(state: GraphState) -> dict:
    action = _ops_action(state)
    lead_id = state["lead_id"]

    decided_action = state.get("decision") or "auto_escalate"
    decision_id = repo.record_decision(
        run_id=state["run_id"],
        lead_id=lead_id,
        action=decided_action,
        editor_note=None,
        edited_body=state.get("draft") if state.get("is_recheck") else None,
        decided_by=None,
    )

    if action == "dismiss":
        repo.update_lead_status(lead_id=lead_id, status="dismissed")
        return {"ops_action": "dismiss", "run_status": "done"}

    if action == "escalate":
        repo.update_lead_status(lead_id=lead_id, status="escalated")
        _t0 = time.monotonic()
        escalation = escalate_for_human(lead_id=lead_id, reason=state.get("intent", "unknown"))
        try:
            repo.record_tool_call(run_id=state["run_id"], tool_name="escalate_for_human",
                                   args_json={"lead_id": lead_id, "reason": state.get("intent", "unknown")},
                                   result_json=escalation, ms=int((time.monotonic() - _t0) * 1000))
        except Exception:  # noqa: BLE001
            pass
        return {"ops_action": "escalate", "run_status": "done"}

    # action == "send"
    draft = state.get("draft") or {"sentences": [], "urls": []}
    rendered_body, citation_block = _render_sent_log(draft)
    sender = state.get("sender_ref") or "unknown"
    channel = state.get("channel") or "unknown"
    # sender_ref is often already "whatsapp:+E.164" - don't double-prefix.
    would_send_to = sender if str(sender).lower().startswith(f"{channel}:") else f"{channel}:{sender}"
    repo.record_sent_log(
        decision_id=decision_id, rendered_body=rendered_body,
        citation_block=citation_block, would_send_to=would_send_to,
    )

    wa = _maybe_deliver_whatsapp(decision_id=decision_id, state=state, rendered_body=rendered_body)

    is_group_quote = state.get("intent") == "group_quote"
    lead_status = "awaiting_ops_quote" if is_group_quote else "approved_logged"
    repo.update_lead_status(lead_id=lead_id, status=lead_status)

    force_fail = "FIXTURE_CRM_FAIL" in state.get("body", "")
    sync_result = sync_lead_to_crm(
        lead_id=lead_id, intent=state.get("intent", "faq"),
        party_size=state.get("party_size"), event_date=state.get("event_date"),
        contact_ref=state.get("sender_ref"), force_fail=force_fail,
    )

    run_status = sync_result["run_status"]
    # WhatsApp delivery failure after a logged send is the same class of
    # partial as a CRM failure - the decision stands, the side-effect didn't.
    if wa.get("wa_status") == "failed":
        run_status = "partial"

    return {
        "ops_action": "send",
        "run_status": run_status,
        "crm_status": sync_result.get("crm_status"),
        "crm_error": sync_result.get("error"),
        "wa_status": wa.get("wa_status"),
        "wa_error": wa.get("wa_error"),
        "wa_external_id": wa.get("wa_external_id"),
    }


def sync_lead_to_crm(*, lead_id: str, intent: str, party_size: int | None,
                      event_date: str | None, contact_ref: str | None = None,
                      force_fail: bool = False, provider: str | None = None) -> dict:
    """Shared by ops(), /api/retry-crm, and /api/demo-ghl-sync. Retry re-runs
    exactly this function against the already-persisted lead - it never
    re-invokes the graph, never re-calls the LLM, and never regenerates the
    draft the human actually approved (the eng-review's P2 fix: a naive retry
    that restarted from intake could silently send a different message than
    the one approved).

    `provider` overrides CRM_PROVIDER for a single call without mutating the
    process environment (thread-safe; used by the Demo GoHighLevel button so
    the default can stay supabase while still exercising the live GHL path).
    """
    chosen = provider or os.environ.get("CRM_PROVIDER", "supabase")
    try:
        if force_fail:
            # Sentinel-driven, same pattern as FIXTURE_MODEL_TIMEOUT - forces the
            # CRM adapter to fail deterministically for the demo/tests, rather
            # than depending on a real 401/429 from an external service.
            raise RuntimeError("stubbed GHL 401 (FIXTURE_CRM_FAIL sentinel)")
        crm = get_crm(chosen)
        external_id = crm.create_or_update_lead(
            lead_id=lead_id, intent=intent, party_size=party_size, event_date=event_date,
            contact_ref=contact_ref,
        )
        repo.record_crm_sync(lead_id=lead_id, provider=chosen, status="synced", external_id=external_id)
        return {"run_status": "done", "crm_status": "synced", "crm_provider": chosen,
                "crm_external_id": external_id}
    except Exception as exc:  # noqa: BLE001 - CRM failures are expected and handled, not re-raised
        repo.record_crm_sync(lead_id=lead_id, provider=chosen, status="failed", error=str(exc))
        return {"run_status": "partial", "crm_status": "failed", "crm_provider": chosen,
                "error": str(exc)}
