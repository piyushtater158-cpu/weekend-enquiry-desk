"""Unit tests for each node, mocked LLM and mocked repo - no live DB, no live
LLM. Each node's input/output contract is tested in isolation here; the full
wired graph (interrupt/resume, repair loop, edit-recheck) is tested separately
in tests/test_graph_integration.py.
"""
from __future__ import annotations

import app.llm as llm_module
import app.repo as repo
from app.nodes.content import content
from app.nodes.intake import intake
from app.nodes.ops import ops
from app.nodes.policy_node import policy_node
from app.nodes.reporting import reporting


# --- intake ---

def test_intake_happy_path(monkeypatch):
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: {
        "intent": "faq", "event_date": None, "party_size": None,
        "pet_flag": True, "kid_flag": False, "language": "en",
        "mentions_camping_bundle_question": False,
    })
    monkeypatch.setattr(repo, "create_lead", lambda **k: "lead-1")

    result = intake({"message_id": "msg-1", "body": "can I bring my dog?", "channel": "whatsapp"})
    assert result["intent"] == "faq"
    assert result["pet_flag"] is True
    assert result["lead_id"] == "lead-1"
    assert result["run_status"] == "running"


def test_intake_model_timeout_creates_escalated_lead(monkeypatch):
    def raise_timeout(*a, **k):
        from app.llm import LLMTimeout
        raise LLMTimeout("stub")

    monkeypatch.setattr(llm_module, "call_llm", raise_timeout)
    monkeypatch.setattr(repo, "create_lead", lambda **k: "lead-2")
    statuses = []
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: statuses.append(k["status"]))

    result = intake({"message_id": "msg-2", "body": "FIXTURE_MODEL_TIMEOUT hello", "channel": "whatsapp"})
    assert result["run_status"] == "error"
    assert result["error_kind"] == "model_timeout"
    assert statuses == ["escalated"]


def test_intake_rejects_invalid_intent_falls_back_to_other(monkeypatch):
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: {
        "intent": "not-a-real-intent", "pet_flag": False, "kid_flag": False,
    })
    monkeypatch.setattr(repo, "create_lead", lambda **k: "lead-3")

    result = intake({"message_id": "msg-3", "body": "whatever", "channel": "email"})
    assert result["intent"] == "other"


# --- content ---

def test_content_happy_path(monkeypatch):
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: {
        "sentences": [{"text": "Dogs welcome.", "cites": ["pet-friendly"], "non_factual": False}],
        "urls": [],
    })
    state = {"body": "can I bring my dog?", "_chunks": [
        {"section_id": "pet-friendly", "text": "Dogs welcome. Water bowls ready."},
    ]}
    result = content(state)
    assert result["draft"]["sentences"][0]["cites"] == ["pet-friendly"]
    assert result["run_status"] == "running"


def test_content_includes_prior_violations_in_repair_prompt(monkeypatch):
    captured = {}

    def fake_call_llm(system_prompt, user_message, **kwargs):
        captured["user_message"] = user_message
        return {"sentences": [], "urls": []}

    monkeypatch.setattr(llm_module, "call_llm", fake_call_llm)
    state = {
        "body": "hi", "_chunks": [], "repair_attempted": True,
        "policy_result": {"violations": [{"rule_id": "PR-01", "detail": "currency figure"}]},
    }
    content(state)
    assert "PR-01" in captured["user_message"]


def test_content_model_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        from app.llm import LLMTimeout
        raise LLMTimeout("stub")

    monkeypatch.setattr(llm_module, "call_llm", raise_timeout)
    result = content({"body": "FIXTURE_MODEL_TIMEOUT", "_chunks": []})
    assert result["run_status"] == "error"
    assert result["error_kind"] == "model_timeout"


# --- policy_node ---

def test_policy_node_passing_draft_routes_to_approval():
    state = {
        "draft": {"sentences": [{"text": "Dogs welcome.", "cites": ["pet-friendly"], "non_factual": False}], "urls": []},
        "_chunks": [{"section_id": "pet-friendly"}],
        "intent": "faq",
    }
    result = policy_node(state)
    assert result["policy_result"]["passed"] is True
    assert result["next_step"] == "approval"


def test_policy_node_first_failure_routes_to_repair():
    state = {
        "draft": {"sentences": [{"text": "We have space this Saturday.", "cites": [], "non_factual": False}], "urls": []},
        "_chunks": [], "intent": "faq",
    }
    result = policy_node(state)
    assert result["policy_result"]["passed"] is False
    assert result["next_step"] == "repair"
    assert result["repair_attempted"] is True


def test_policy_node_second_failure_routes_to_approval():
    """Repair exhausted: park at human approval so Edit stays available.
    Human Approve is the send override in ops.
    """
    state = {
        "draft": {"sentences": [{"text": "We have space this Saturday.", "cites": [], "non_factual": False}], "urls": []},
        "_chunks": [], "intent": "faq", "repair_attempted": True,
    }
    result = policy_node(state)
    assert result["next_step"] == "approval"
    assert result["policy_result"]["passed"] is False


def test_policy_node_recheck_pass_routes_to_ops():
    state = {
        "draft": {"sentences": [{"text": "Dogs welcome.", "cites": ["pet-friendly"], "non_factual": False}], "urls": []},
        "_chunks": [{"section_id": "pet-friendly"}], "intent": "faq", "is_recheck": True,
    }
    result = policy_node(state)
    assert result["next_step"] == "ops"


def test_policy_node_recheck_fail_routes_to_escalate_no_override():
    """The D3 fix: an edited draft that fails re-check must not be allowed to override."""
    state = {
        "draft": {"sentences": [{"text": "Yes, camping is included, AED 150.", "cites": [], "non_factual": False}], "urls": []},
        "_chunks": [], "intent": "faq", "is_recheck": True,
    }
    result = policy_node(state)
    assert result["next_step"] == "escalate"
    assert result["policy_result"]["passed"] is False


# --- ops ---

def test_ops_dismiss(monkeypatch):
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-1")
    statuses = []
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: statuses.append(k["status"]))

    result = ops({"lead_id": "lead-1", "run_id": "run-1", "decision": "dismiss"})
    assert result["ops_action"] == "dismiss"
    assert statuses == ["dismissed"]


def test_ops_escalate_when_policy_failed_without_approve(monkeypatch):
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-2")
    statuses = []
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: statuses.append(k["status"]))

    result = ops({
        "lead_id": "lead-2", "run_id": "run-2", "intent": "group_quote",
        "policy_result": {"passed": False, "violations": []}, "next_step": "escalate",
    })
    assert result["ops_action"] == "escalate"
    assert statuses == ["escalated"]


def test_ops_approve_overrides_failed_policy_and_sends(monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-2b")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    statuses = []
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: statuses.append(k["status"]))
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: None)

    result = ops({
        "lead_id": "lead-2b", "run_id": "run-2b", "decision": "approve", "intent": "faq",
        "policy_result": {"passed": False, "violations": [{"rule_id": "PR-11"}]},
        "draft": {"sentences": [{"text": "See our page.", "cites": [], "non_factual": True}], "urls": []},
        "channel": "whatsapp",
    })
    assert result["ops_action"] == "send"
    assert statuses == ["approved_logged"]


def test_ops_send_success_syncs_to_crm(monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-3")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    statuses = []
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: statuses.append(k["status"]))
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))

    result = ops({
        "lead_id": "lead-3", "run_id": "run-3", "decision": "approve", "intent": "faq",
        "policy_result": {"passed": True, "violations": []},
        "draft": {"sentences": [{"text": "Dogs welcome.", "cites": ["pet-friendly"]}], "urls": []},
        "channel": "whatsapp",
    })
    assert result["ops_action"] == "send"
    assert result["run_status"] == "done"
    assert statuses == ["approved_logged"]
    assert synced[0]["status"] == "synced"


def test_ops_send_crm_failure_marks_partial(monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "gohighlevel")
    monkeypatch.delenv("GHL_PIT_TOKEN", raising=False)  # forces GoHighLevelError on construction
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-4")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: None)
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))

    result = ops({
        "lead_id": "lead-4", "run_id": "run-4", "decision": "approve", "intent": "faq",
        "policy_result": {"passed": True, "violations": []},
        "draft": {"sentences": [], "urls": []}, "channel": "whatsapp",
    })
    assert result["run_status"] == "partial"
    assert synced[0]["status"] == "failed"


def test_sync_lead_to_crm_provider_override_ignores_env(monkeypatch):
    """Demo GoHighLevel button passes provider='gohighlevel' while CRM_PROVIDER
    stays supabase - the override must win without mutating os.environ."""
    from app.nodes.ops import sync_lead_to_crm
    import httpx

    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-override"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = sync_lead_to_crm(
        lead_id="lead-ov", intent="faq", party_size=None, event_date=None,
        provider="gohighlevel",
    )
    assert result["crm_status"] == "synced"
    assert result["crm_provider"] == "gohighlevel"
    assert synced[0]["provider"] == "gohighlevel"
    import os
    assert os.environ["CRM_PROVIDER"] == "supabase"


def test_ops_group_quote_lead_status_is_awaiting_ops_quote(monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-5")
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: None)
    statuses = []
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: statuses.append(k["status"]))

    ops({
        "lead_id": "lead-5", "run_id": "run-5", "decision": "approve", "intent": "group_quote",
        "policy_result": {"passed": True, "violations": []},
        "draft": {"sentences": [], "urls": []}, "channel": "email",
    })
    assert statuses == ["awaiting_ops_quote"]


# --- reporting ---

def test_reporting_tags_missing_fact_on_escalate(monkeypatch):
    tagged = []
    monkeypatch.setattr(repo, "set_lead_missing_fact", lambda **k: tagged.append(k))
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: None)

    reporting({
        "lead_id": "lead-1", "run_id": "run-1", "ops_action": "escalate",
        "policy_result": {"violations": [{"rule_id": "PR-08", "detail": "group_quote must escalate"}]},
        "run_status": "done",
    })
    assert tagged[0]["missing_fact"] == "PR-08: group_quote must escalate"


def test_reporting_skips_missing_fact_on_send(monkeypatch):
    tagged = []
    monkeypatch.setattr(repo, "set_lead_missing_fact", lambda **k: tagged.append(k))
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: None)

    reporting({"lead_id": "lead-1", "run_id": "run-1", "ops_action": "send", "run_status": "done"})
    assert tagged == []
