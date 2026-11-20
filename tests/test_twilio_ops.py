"""ops() WhatsApp delivery path after sent_log."""
from __future__ import annotations

import app.repo as repo
from app.nodes.ops import ops


def _base_state(**overrides):
    state = {
        "lead_id": "lead-1",
        "run_id": "run-1",
        "decision": "approve",
        "channel": "whatsapp",
        "sender_ref": "whatsapp:+15555550100",
        "intent": "faq",
        "party_size": None,
        "event_date": None,
        "body": "hi",
        "policy_result": {"passed": True, "violations": []},
        "draft": {
            "sentences": [{"text": "Dogs welcome.", "cites": ["pet-friendly"], "non_factual": False}],
            "urls": ["https://sevn.ly/xvKcpSJX"],
        },
    }
    state.update(overrides)
    return state


def test_ops_send_without_twilio_flag_stays_log_only(monkeypatch):
    monkeypatch.delenv("TWILIO_WHATSAPP_SEND", raising=False)
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-1")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: None)
    deliveries = []
    monkeypatch.setattr(repo, "record_channel_delivery", lambda **k: deliveries.append(k))
    monkeypatch.setattr(
        "app.nodes.ops.sync_lead_to_crm",
        lambda **k: {"run_status": "done", "crm_status": "synced", "crm_provider": "supabase"},
    )

    result = ops(_base_state())
    assert result["ops_action"] == "send"
    assert result["run_status"] == "done"
    assert result.get("wa_status") is None
    assert deliveries == []


def test_ops_send_records_channel_delivery_on_success(monkeypatch):
    monkeypatch.setenv("TWILIO_WHATSAPP_SEND", "true")
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-1")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: None)
    monkeypatch.setattr(repo, "record_tool_call", lambda **k: None)
    deliveries = []
    monkeypatch.setattr(repo, "record_channel_delivery", lambda **k: deliveries.append(k))
    monkeypatch.setattr(
        "app.nodes.ops.sync_lead_to_crm",
        lambda **k: {"run_status": "done", "crm_status": "synced", "crm_provider": "supabase"},
    )
    monkeypatch.setattr(
        "app.nodes.ops.send_whatsapp",
        lambda **k: {"status": "synced", "external_id": "SM9", "to_address": k["to"]},
    )

    result = ops(_base_state())
    assert result["wa_status"] == "synced"
    assert result["run_status"] == "done"
    assert deliveries[0]["provider"] == "twilio_whatsapp"
    assert deliveries[0]["status"] == "synced"
    assert deliveries[0]["external_id"] == "SM9"


def test_ops_whatsapp_failure_marks_partial_keeps_sent_log(monkeypatch):
    monkeypatch.setenv("TWILIO_WHATSAPP_SEND", "true")
    sent = []
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-1")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: sent.append(k))
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: None)
    monkeypatch.setattr(repo, "record_tool_call", lambda **k: None)
    monkeypatch.setattr(repo, "record_channel_delivery", lambda **k: None)
    monkeypatch.setattr(
        "app.nodes.ops.sync_lead_to_crm",
        lambda **k: {"run_status": "done", "crm_status": "synced", "crm_provider": "supabase"},
    )
    monkeypatch.setattr(
        "app.nodes.ops.send_whatsapp",
        lambda **k: {"status": "failed", "error": "21608", "to_address": k["to"]},
    )

    result = ops(_base_state())
    assert sent, "sent_log must still be written"
    assert result["wa_status"] == "failed"
    assert result["run_status"] == "partial"
