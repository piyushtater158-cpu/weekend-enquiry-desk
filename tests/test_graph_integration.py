"""Full compiled graph, InMemorySaver (no live DB needed - LangGraph's own
checkpointer, not app/db.py), mocked LLM, mocked repo. Exercises interrupt() /
Command(resume=...) for approve, edit (pass and fail), dismiss, and the policy
repair loop - the real control-flow regression tests the eng-review's fixes
need and that unit-testing each node in isolation cannot prove on its own.
"""
from __future__ import annotations

import uuid

import app.llm as llm_module
import app.repo as repo
from app.graph import build_graph
from langgraph.types import Command

GOOD_DRAFT = {
    "sentences": [{"text": "Dogs welcome.", "cites": ["pet-friendly"], "non_factual": False}],
    "urls": [],
}
BAD_DRAFT = {
    "sentences": [{"text": "We have space this Saturday.", "cites": [], "non_factual": False}],
    "urls": [],
}
INJECTED_PRICE_DRAFT = {
    "sentences": [{"text": "Yes, camping is included, AED 150.", "cites": [], "non_factual": False}],
    "urls": [],
}
CAMPING_BUNDLE_OK_DRAFT = {
    "sentences": [{
        "text": "Day passes and camping are separate tickets - here are both links, "
                "and we can confirm with the team if you want to combine them.",
        "cites": ["camper-s-paradise"], "non_factual": False,
    }],
    "urls": ["https://sevn.ly/xvKcpSJX", "https://sevn.ly/xshc0Y2s"],
}


def _stub_repo(monkeypatch):
    monkeypatch.setattr(repo, "create_lead", lambda **k: "lead-" + uuid.uuid4().hex[:8])
    monkeypatch.setattr(repo, "update_lead_status", lambda **k: None)
    monkeypatch.setattr(repo, "record_decision", lambda **k: "dec-" + uuid.uuid4().hex[:8])
    monkeypatch.setattr(repo, "record_sent_log", lambda **k: None)
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: None)
    monkeypatch.setattr(repo, "set_lead_missing_fact", lambda **k: None)
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: None)


def _initial_state(body="can I bring my dog?"):
    return {
        "message_id": "msg-" + uuid.uuid4().hex[:8],
        "run_id": "run-" + uuid.uuid4().hex[:8],
        "thread_id": "thread-" + uuid.uuid4().hex[:8],
        "body": body,
        "channel": "whatsapp",
        "is_synthetic": True,
        "external_ref": "ext-" + uuid.uuid4().hex[:8],
    }


def test_approve_flow_sends_and_syncs(monkeypatch):
    _stub_repo(monkeypatch)
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "faq", "pet_flag": True, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": None, "mentions_camping_bundle_question": False}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else GOOD_DRAFT
    ))

    graph = build_graph()
    state = _initial_state()
    config = {"configurable": {"thread_id": state["thread_id"]}}
    result = graph.invoke(state, config=config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["draft"]["sentences"][0]["cites"] == ["pet-friendly"]

    final = graph.invoke(Command(resume={"action": "approve"}), config=config)
    assert final["ops_action"] == "send"
    assert final["run_status"] == "done"


def test_dismiss_flow_never_sends(monkeypatch):
    _stub_repo(monkeypatch)
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "faq", "pet_flag": False, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": None, "mentions_camping_bundle_question": False}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else GOOD_DRAFT
    ))

    graph = build_graph()
    state = _initial_state()
    config = {"configurable": {"thread_id": state["thread_id"]}}
    graph.invoke(state, config=config)
    final = graph.invoke(Command(resume={"action": "dismiss"}), config=config)

    assert final["ops_action"] == "dismiss"


def test_edit_that_passes_recheck_sends_edited_body(monkeypatch):
    _stub_repo(monkeypatch)
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "faq", "pet_flag": True, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": None, "mentions_camping_bundle_question": False}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else GOOD_DRAFT
    ))

    graph = build_graph()
    state = _initial_state()
    config = {"configurable": {"thread_id": state["thread_id"]}}
    graph.invoke(state, config=config)

    edited_sentences = [{"text": "Kids welcome too.", "cites": ["family-kids"], "non_factual": False}]
    final = graph.invoke(
        Command(resume={"action": "edit", "edited_sentences": edited_sentences}), config=config,
    )
    assert final["ops_action"] == "send"
    assert final["draft"]["sentences"] == edited_sentences


def test_edit_that_fails_recheck_escalates_with_no_override(monkeypatch):
    """The D3 regression test: an operator editing in an invented price must not
    be able to override the policy gate."""
    _stub_repo(monkeypatch)
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "faq", "pet_flag": True, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": None, "mentions_camping_bundle_question": True}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else CAMPING_BUNDLE_OK_DRAFT
    ))

    graph = build_graph()
    state = _initial_state("is camping included in the pass?")
    config = {"configurable": {"thread_id": state["thread_id"]}}
    graph.invoke(state, config=config)

    final = graph.invoke(
        Command(resume={"action": "edit", "edited_sentences": INJECTED_PRICE_DRAFT["sentences"]}),
        config=config,
    )
    assert final["ops_action"] == "escalate"
    assert final["policy_result"]["passed"] is False
    assert any(v["rule_id"] == "PR-01" for v in final["policy_result"]["violations"])


def test_repair_loop_then_parks_at_approval_when_content_never_fixes_it(monkeypatch):
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "faq", "pet_flag": False, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": None, "mentions_camping_bundle_question": False}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else BAD_DRAFT  # content never fixes the availability claim, even on retry
    ))
    _stub_repo(monkeypatch)

    graph = build_graph()
    state = _initial_state("what time are you open")
    config = {"configurable": {"thread_id": state["thread_id"]}}
    paused = graph.invoke(state, config=config)

    # Policy failed twice, but we still interrupt so the operator can edit /
    # dismiss / approve (human override sends). Nothing auto-concludes.
    assert "__interrupt__" in paused
    final = graph.invoke(Command(resume={"action": "approve"}), config=config)
    assert final["ops_action"] == "send"
    assert final["run_status"] == "done"


def test_fixture_crm_fail_marks_partial_after_approve(monkeypatch):
    """FIXTURE_CRM_FAIL: the draft itself is fine and gets approved normally;
    the CRM sync step fails deterministically on the sentinel."""
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "faq", "pet_flag": True, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": None, "mentions_camping_bundle_question": False}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else GOOD_DRAFT
    ))
    _stub_repo(monkeypatch)

    graph = build_graph()
    state = _initial_state("FIXTURE_CRM_FAIL can I bring my dog this Saturday?")
    config = {"configurable": {"thread_id": state["thread_id"]}}
    graph.invoke(state, config=config)
    final = graph.invoke(Command(resume={"action": "approve"}), config=config)

    assert final["ops_action"] == "send"
    assert final["run_status"] == "partial"


def test_group_quote_parks_at_approval_then_sends_on_approve(monkeypatch):
    monkeypatch.setattr(llm_module, "call_llm", lambda *a, **k: (
        {"intent": "group_quote", "pet_flag": False, "kid_flag": False, "language": "en",
         "event_date": None, "party_size": 20, "mentions_camping_bundle_question": False}
        if "today" in a[1].lower() or "today" in a[0].lower()
        else GOOD_DRAFT
    ))
    _stub_repo(monkeypatch)

    graph = build_graph()
    state = _initial_state("we are a group of 20, quote us a package")
    config = {"configurable": {"thread_id": state["thread_id"]}}
    paused = graph.invoke(state, config=config)

    # PR-08 parks at approval (no auto-send). Human Approve is the override.
    assert "__interrupt__" in paused
    final = graph.invoke(Command(resume={"action": "approve"}), config=config)
    assert final["ops_action"] == "send"
