from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.repo as repo
from app.main import app


class FakeGraph:
    def __init__(self):
        self.invocations = []
        self.state_by_thread = {}
        self.interrupted_threads = set()

    def invoke(self, arg, config):
        thread_id = config["configurable"]["thread_id"]
        self.invocations.append((arg, thread_id))
        self.state_by_thread[thread_id] = {"ops_action": "send", "run_status": "done", "run_id": "run-1"}
        return self.state_by_thread[thread_id]

    def get_state(self, config):
        thread_id = config["configurable"]["thread_id"]
        if thread_id not in self.state_by_thread:
            return None

        values = self.state_by_thread[thread_id]
        interrupted = thread_id in self.interrupted_threads

        class _Task:
            interrupts = (True,) if interrupted else ()

        class _Snap:
            def __init__(self):
                self.values = values
                self.tasks = (_Task(),) if interrupted else ()

        return _Snap()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DESK_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("DESK_BASIC_AUTH_USER", "ops")
    monkeypatch.setenv("DESK_BASIC_AUTH_PASS", "ops-pass")
    monkeypatch.delenv("DESK_OPEN_ACCESS", raising=False)
    monkeypatch.delenv("DESK_RUN_INLINE", raising=False)
    main_module.set_graph(FakeGraph())
    monkeypatch.setattr(repo, "is_duplicate_external_ref", lambda **k: False)
    monkeypatch.setattr(repo, "create_message", lambda **k: "msg-1")
    monkeypatch.setattr(repo, "create_agent_run", lambda **k: "run-1")
    monkeypatch.setattr(repo, "record_rejected_event", lambda **k: None)
    return TestClient(app)


def test_webhook_rejects_missing_secret(client):
    rejected = []
    import app.repo as repo_mod
    orig = repo_mod.record_rejected_event
    repo_mod.record_rejected_event = lambda **k: rejected.append(k)
    try:
        resp = client.post("/webhook", json={"channel": "whatsapp", "body": "hi", "external_ref": "x1"})
    finally:
        repo_mod.record_rejected_event = orig
    assert resp.status_code == 401
    assert rejected and rejected[0]["reason"] == "bad_secret"


def test_webhook_rejects_malformed_body(client):
    resp = client.post(
        "/webhook", headers={"X-Desk-Secret": "test-secret"}, content=b"not json at all",
    )
    assert resp.status_code == 422


def test_webhook_misconfigured_secret_returns_500_not_401(client, monkeypatch):
    monkeypatch.delenv("DESK_WEBHOOK_SECRET", raising=False)
    rejected = []
    monkeypatch.setattr(repo, "record_rejected_event", lambda **k: rejected.append(k))
    resp = client.post(
        "/webhook", headers={"X-Desk-Secret": "anything"},
        json={"channel": "whatsapp", "body": "hi", "external_ref": "x1"},
    )
    assert resp.status_code == 500
    assert rejected == []


def test_webhook_accepts_valid(client):
    resp = client.post(
        "/webhook", headers={"X-Desk-Secret": "test-secret"},
        json={"channel": "whatsapp", "body": "hi", "external_ref": "x1"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-1"
    assert len(main_module._graph.invocations) == 1


def test_command_centre_requires_basic_auth(client):
    resp = client.get("/")
    assert resp.status_code == 401


def test_command_centre_with_basic_auth(client):
    resp = client.get("/", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    assert "Weekend Enquiry Desk" in resp.text
    assert "decision-panel" not in resp.text
    assert 'class="marquee"' in resp.text
    assert 'data-build="rest-v1"' in resp.text
    assert "/api/queue" in resp.text


def test_command_centre_shows_demo_ghl_button_when_configured(client, monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    resp = client.get("/", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    assert "Demo GoHighLevel" in resp.text
    assert "btn-demo-ghl" in resp.text
    assert "GHL ready" in resp.text


def test_command_centre_hides_demo_ghl_button_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("GHL_PIT_TOKEN", raising=False)
    resp = client.get("/", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    assert "Demo GoHighLevel" not in resp.text
    assert "GHL off" in resp.text


def test_decide_requires_basic_auth(client):
    resp = client.post("/api/decide", json={"thread_id": "t1", "action": "approve"})
    assert resp.status_code == 401


def test_decide_approve_invokes_resume(client):
    resp = client.post(
        "/api/decide", auth=("ops", "ops-pass"),
        json={"thread_id": "t1", "action": "approve"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["ops_action"] == "send"


def test_decide_rejects_invalid_action(client):
    resp = client.post(
        "/api/decide", auth=("ops", "ops-pass"),
        json={"thread_id": "t1", "action": "not-a-real-action"},
    )
    assert resp.status_code == 422


def test_decide_always_returns_json_even_with_htmx_header(client):
    """REST: /api/* is JSON-only; the shell fetches and renders."""
    resp = client.post(
        "/api/decide", auth=("ops", "ops-pass"), headers={"HX-Request": "true"},
        json={"thread_id": "t1", "action": "approve"},
    )
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert resp.json()["ops_action"] == "send"


def test_fixture_picker_submits_a_real_fixture(client):
    resp = client.post(
        "/api/fixture", auth=("ops", "ops-pass"), json={"fixture_id": "msg-01"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"
    assert len(main_module._graph.invocations) == 1


def test_fixture_picker_unknown_id(client):
    resp = client.post(
        "/api/fixture", auth=("ops", "ops-pass"), json={"fixture_id": "not-a-real-fixture"},
    )
    assert resp.status_code == 404


def test_rerun_requires_auth(client):
    resp = client.post("/api/rerun", json={"run_id": "run-1"})
    assert resp.status_code == 401


def test_rerun_missing_run_id(client):
    resp = client.post("/api/rerun", auth=("ops", "ops-pass"), json={})
    assert resp.status_code == 422


def test_rerun_unknown_run(client, monkeypatch):
    monkeypatch.setattr(repo, "get_message_for_run", lambda **k: None)
    resp = client.post("/api/rerun", auth=("ops", "ops-pass"), json={"run_id": "missing"})
    assert resp.status_code == 404


def test_rerun_clones_source_and_arrival_into_new_pipeline_run(client, monkeypatch):
    from datetime import datetime, timezone

    arrived = datetime(2026, 11, 13, 13, 38, 52, tzinfo=timezone.utc)
    created = {}
    superseded = []

    def _create_message(**k):
        created["message"] = k
        return "msg-rerun"

    def _create_agent_run(**k):
        created["run"] = k
        return "run-rerun"

    monkeypatch.setattr(repo, "get_message_for_run", lambda **k: {
        "message_id": "msg-orig",
        "run_id": "run-orig",
        "channel": "whatsapp",
        "body": "Hi, can I bring my dog this Saturday?",
        "sender_ref": "guest-42",
        "is_synthetic": True,
        "external_ref": "live-test-002",
        "received_at": arrived,
        "raw_payload": {
            "channel": "whatsapp",
            "body": "Hi, can I bring my dog this Saturday?",
            "external_ref": "live-test-002",
            "is_synthetic": True,
        },
    })
    monkeypatch.setattr(repo, "create_message", _create_message)
    monkeypatch.setattr(repo, "create_agent_run", _create_agent_run)
    monkeypatch.setattr(repo, "supersede_agent_run", lambda **k: superseded.append(k))

    before = len(main_module._graph.invocations)
    resp = client.post("/api/rerun", auth=("ops", "ops-pass"), json={"run_id": "run-orig"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-rerun"
    assert body["source_run_id"] == "run-orig"
    assert body["replaced_run_id"] == "run-orig"
    assert body["channel"] == "whatsapp"
    assert body["external_ref"].startswith("rerun-live-test-002-")

    assert created["message"]["channel"] == "whatsapp"
    assert created["message"]["body"] == "Hi, can I bring my dog this Saturday?"
    assert created["message"]["sender_ref"] == "guest-42"
    assert created["message"]["is_synthetic"] is True
    assert created["message"]["received_at"] == arrived
    assert created["message"]["raw_payload"]["rerun_of"]["run_id"] == "run-orig"
    assert created["message"]["raw_payload"]["rerun_of"]["external_ref"] == "live-test-002"
    assert created["run"]["message_id"] == "msg-rerun"
    assert superseded == [{"run_id": "run-orig", "replaced_by_run_id": "run-rerun"}]
    assert len(main_module._graph.invocations) == before + 1
    state, thread_id = main_module._graph.invocations[-1]
    assert state["body"] == "Hi, can I bring my dog this Saturday?"
    assert state["channel"] == "whatsapp"
    assert state["sender_ref"] == "guest-42"
    assert state["is_synthetic"] is True
    assert state["thread_id"] == thread_id


def test_retry_crm_not_found(client, monkeypatch):
    monkeypatch.setattr(repo, "get_lead", lambda **k: None)
    resp = client.post("/api/retry-crm", auth=("ops", "ops-pass"), json={"lead_id": "nope"})
    assert resp.status_code == 404


def test_retry_crm_retries_without_touching_graph(client, monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(repo, "get_lead", lambda **k: {
        "id": "lead-1", "intent": "faq", "party_size": None, "event_date": None,
        "status": "approved_logged", "crm_provider": "none",
    })
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))
    resp = client.post("/api/retry-crm", auth=("ops", "ops-pass"), json={"lead_id": "lead-1"})
    assert resp.status_code == 200
    assert resp.json()["crm_status"] == "synced"
    assert len(main_module._graph.invocations) == 0


def test_retry_crm_success_clears_partial_run_status(client, monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(repo, "get_lead", lambda **k: {
        "id": "lead-1", "intent": "faq", "party_size": None, "event_date": None,
        "status": "approved_logged", "crm_provider": "none", "run_id": "run-1",
    })
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: None)
    finished = []
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: finished.append(k))
    resp = client.post("/api/retry-crm", auth=("ops", "ops-pass"), json={"lead_id": "lead-1"})
    assert resp.status_code == 200
    assert finished == [{"run_id": "run-1", "status": "done"}]


def test_demo_ghl_sync_requires_token(client, monkeypatch):
    monkeypatch.delenv("GHL_PIT_TOKEN", raising=False)
    resp = client.post("/api/demo-ghl-sync", auth=("ops", "ops-pass"), json={"lead_id": "lead-1"})
    assert resp.status_code == 400


def test_demo_ghl_sync_requires_auth(client, monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    resp = client.post("/api/demo-ghl-sync", json={"lead_id": "lead-1"})
    assert resp.status_code == 401


def test_demo_ghl_sync_forces_gohighlevel_without_mutating_env(client, monkeypatch):
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setenv("GHL_LOCATION_ID", "loc-demo")
    monkeypatch.setattr(repo, "get_lead", lambda **k: {
        "id": "lead-1", "intent": "faq", "party_size": 2, "event_date": None,
        "status": "approved_logged", "crm_provider": "supabase", "run_id": "run-1",
        "sender_ref": None,
    })
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: None)

    import httpx

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-demo-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    resp = client.post("/api/demo-ghl-sync", auth=("ops", "ops-pass"), json={"lead_id": "lead-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["crm_status"] == "synced"
    assert body["crm_provider"] == "gohighlevel"
    assert body["crm_external_id"] == "ghl-demo-1"
    assert "ghl-demo-1" in body["crm_url"]
    assert synced[0]["provider"] == "gohighlevel"
    import os
    assert os.environ.get("CRM_PROVIDER") == "supabase"
    assert len(main_module._graph.invocations) == 0


def test_demo_ghl_sync_falls_back_to_latest_lead(client, monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.setattr(repo, "latest_lead_id", lambda: "lead-latest")
    monkeypatch.setattr(repo, "get_lead", lambda **k: {
        "id": k["lead_id"], "intent": "faq", "party_size": None, "event_date": None,
        "status": "approved_logged", "crm_provider": "none", "run_id": "run-9",
        "sender_ref": None,
    })
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: None)

    import httpx

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-latest"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    resp = client.post("/api/demo-ghl-sync", auth=("ops", "ops-pass"), json={})
    assert resp.status_code == 200
    assert resp.json()["lead_id"] == "lead-latest"
    assert synced[0]["lead_id"] == "lead-latest"


def test_demo_ghl_sync_no_leads_returns_404(client, monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setattr(repo, "latest_lead_id", lambda: None)
    resp = client.post("/api/demo-ghl-sync", auth=("ops", "ops-pass"), json={})
    assert resp.status_code == 404


def test_debug_run_not_found(client):
    resp = client.get("/debug/run", params={"thread_id": "does-not-exist"}, auth=("ops", "ops-pass"))
    assert resp.status_code == 404


def test_api_draft_requires_basic_auth(client):
    resp = client.get("/api/drafts/does-not-exist")
    assert resp.status_code == 401


def test_api_draft_no_draft_yet(client):
    resp = client.get("/api/drafts/does-not-exist", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["draft"] is None
    assert body["interrupted"] is False


def test_api_draft_returns_json_with_interrupt_flag(client):
    main_module._graph.state_by_thread["th-draft"] = {
        "draft": {
            "sentences": [{"text": "Yes, dogs are welcome on the beach.", "cites": ["pets"], "non_factual": False}],
            "urls": ["https://sevn.ly/xvKcpSJX"],
        },
        "lead_status": "awaiting_human",
        "run_status": "running",
        "policy_result": {"passed": False, "violations": [{"rule_id": "PR-08", "detail": "group_quote"}]},
    }
    main_module._graph.interrupted_threads.add("th-draft")
    resp = client.get("/api/drafts/th-draft", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["interrupted"] is True
    assert body["draft"]["sentences"][0]["text"] == "Yes, dogs are welcome on the beach."
    assert body["lead_status"] == "awaiting_human"
    assert body["policy_result"]["passed"] is False
    assert body["policy_result"]["violations"][0]["rule_id"] == "PR-08"


def test_api_queue_requires_auth(client):
    resp = client.get("/api/queue")
    assert resp.status_code == 401


def test_api_queue_returns_empty_json(client, monkeypatch):
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["stats"]["awaiting"] == 0


def test_api_queue_returns_rows_json(client, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "running",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "awaiting_human",
        "pet_flag": True, "kid_flag": False, "party_size": None,
        "crm_contact_id": None, "crm_provider": "none",
        "crm_status": None, "crm_error": None, "crm_external_id": None, "crm_attempts": 0,
        "body": "can I bring my dog?", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["is_synthetic"] is True
    assert body["rows"][0]["show_draft"] is True
    assert body["stats"]["awaiting"] == 1


def test_api_queue_excludes_error_runs_from_awaiting(client, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "error",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "new",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": None, "crm_provider": "none",
        "crm_status": None, "crm_error": None, "crm_external_id": None, "crm_attempts": 0,
        "body": "timeout", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    body = resp.json()
    assert body["stats"]["awaiting"] == 0
    assert body["rows"][0]["show_draft"] is False


def test_api_queue_crm_badge_and_ghl_deep_link(client, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("CRM_PROVIDER", "gohighlevel")
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setenv("GHL_LOCATION_ID", "loc-abc123")
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "done",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "approved_logged",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": "contact-xyz", "crm_provider": "gohighlevel",
        "crm_status": "synced", "crm_error": None, "crm_external_id": "contact-xyz", "crm_attempts": 1,
        "body": "can I bring my dog?", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
        "sent_body": "Yes — dogs are welcome on a lead.",
        "sent_citation_block": {"urls": ["https://example.com/pets"], "cites": ["pets"]},
        "sent_would_send_to": "whatsapp:+15551212",
        "sent_logged_at": datetime.now(timezone.utc),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    body = resp.json()
    assert "app.gohighlevel.com/v2/location/loc-abc123/contacts/detail/contact-xyz" in body["rows"][0]["crm_url"]
    assert body["crm"]["ghl_configured"] is True
    assert body["rows"][0]["show_sent_reply"] is True
    assert body["rows"][0]["sent_body"] == "Yes — dogs are welcome on a lead."
    assert body["rows"][0]["show_draft"] is False


def test_api_queue_sent_reply_for_partial_approved(client, monkeypatch):
    """CRM/WhatsApp partial still has a sent_log - the card must expose it
    so the operator can expand and read the reply that was logged."""
    from datetime import datetime, timezone
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "partial",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "approved_logged",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": None, "crm_provider": "supabase",
        "crm_status": "failed", "crm_error": "401", "crm_external_id": None, "crm_attempts": 1,
        "body": "camping question", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
        "sent_body": "Day pass and camping links…",
        "sent_citation_block": {"urls": [], "cites": []},
        "sent_would_send_to": "whatsapp:+1",
        "sent_logged_at": datetime.now(timezone.utc),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    row = resp.json()["rows"][0]
    assert row["show_draft"] is False
    assert row["show_sent_reply"] is True
    assert row["sent_body"].startswith("Day pass")


def test_api_queue_no_sent_reply_while_awaiting(client, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "running",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "awaiting_human",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": None, "crm_provider": "none",
        "crm_status": None, "crm_error": None, "crm_external_id": None, "crm_attempts": 0,
        "body": "can I bring my dog?", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
        "sent_body": None, "sent_citation_block": None,
        "sent_would_send_to": None, "sent_logged_at": None,
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    row = resp.json()["rows"][0]
    assert row["show_draft"] is True
    assert row["show_sent_reply"] is False


def test_api_queue_supabase_provider_has_no_ghl_link(client, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("CRM_PROVIDER", "supabase")
    monkeypatch.delenv("GHL_PIT_TOKEN", raising=False)
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "done",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "approved_logged",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": "lead-1", "crm_provider": "supabase",
        "crm_status": "synced", "crm_error": None, "crm_external_id": "lead-1", "crm_attempts": 1,
        "body": "can I bring my dog?", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    body = resp.json()
    assert body["rows"][0]["crm_url"] is None
    assert body["crm"]["ghl_configured"] is False


def test_api_queue_handles_runs_older_than_an_hour(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "running",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "new",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": None, "crm_provider": "none",
        "crm_status": None, "crm_error": None, "crm_external_id": None, "crm_attempts": 0,
        "body": "old message", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc) - timedelta(hours=4, minutes=7),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["age"] == "4h 7m"


def test_api_timeline_returns_json(client, monkeypatch):
    monkeypatch.setattr(repo, "get_run_timeline", lambda run_id: {
        "steps": [{"node": "intake", "error": None}],
        "tool_calls": [{"tool_name": "search_knowledge_base", "ms": 3, "error": None}],
        "violations": [{"rule_id": "PR-08", "severity": "critical", "detail": "group_quote must escalate"}],
        "crm": None,
    })
    resp = client.get("/api/runs/run-1/timeline", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"][0]["node"] == "intake"
    assert body["violations"][0]["rule_id"] == "PR-08"


def test_decide_on_already_decided_thread_returns_409(client):
    """A thread whose checkpoint exists but is no longer interrupted (the
    graph already resumed past approval) must not be resumed a second time -
    a double-click or a stale tab replaying an old decide should get a clear
    409, not a second graph.invoke() on a finished thread."""
    main_module._graph.state_by_thread["t-done"] = {
        "ops_action": "send", "run_status": "done", "run_id": "run-1",
    }
    # Deliberately not added to interrupted_threads - simulates "already decided".
    resp = client.post(
        "/api/decide", auth=("ops", "ops-pass"),
        json={"thread_id": "t-done", "action": "approve"},
    )
    assert resp.status_code == 409
    assert len(main_module._graph.invocations) == 0


def test_decide_on_pending_interrupt_still_resumes(client):
    """Sanity check for the guard above: a thread genuinely parked at the
    approval interrupt must still resume normally."""
    main_module._graph.state_by_thread["t-pending"] = {
        "draft": {"sentences": [], "urls": []}, "run_status": "running", "run_id": "run-2",
    }
    main_module._graph.interrupted_threads.add("t-pending")
    resp = client.post(
        "/api/decide", auth=("ops", "ops-pass"),
        json={"thread_id": "t-pending", "action": "approve"},
    )
    assert resp.status_code == 200
    assert len(main_module._graph.invocations) == 1


def test_api_queue_shows_draft_for_interrupted_run_marked_done(client, monkeypatch):
    """Regression for the live bug: a CRM sync on an already-decided lead can
    leave an unrelated interrupted run's agent_runs.status stale at 'done' in
    old data. show_draft must come from lead_status, not run_status=='done',
    so the draft stays reachable from the queue."""
    from datetime import datetime, timezone
    monkeypatch.setattr(repo, "list_queue", lambda limit=50: [{
        "run_id": "run-1", "thread_id": "th-1", "run_status": "done",
        "lead_id": "lead-1", "intent": "faq", "lead_status": "new",
        "pet_flag": False, "kid_flag": False, "party_size": None,
        "crm_contact_id": None, "crm_provider": "none",
        "crm_status": None, "crm_error": None, "crm_external_id": None, "crm_attempts": 0,
        "body": "can I bring my dog?", "channel": "whatsapp", "is_synthetic": True,
        "started_at": datetime.now(timezone.utc),
    }])
    resp = client.get("/api/queue", auth=("ops", "ops-pass"))
    assert resp.json()["rows"][0]["show_draft"] is True


def test_retry_crm_rejects_lead_still_awaiting_decision(client, monkeypatch):
    monkeypatch.setattr(repo, "get_lead", lambda **k: {
        "id": "lead-1", "intent": "faq", "party_size": None, "event_date": None,
        "status": "awaiting_human", "crm_provider": "none", "run_id": "run-1",
    })
    synced = []
    monkeypatch.setattr(repo, "record_crm_sync", lambda **k: synced.append(k))
    finished = []
    monkeypatch.setattr(repo, "finish_agent_run", lambda **k: finished.append(k))
    resp = client.post("/api/retry-crm", auth=("ops", "ops-pass"), json={"lead_id": "lead-1"})
    assert resp.status_code == 409
    assert synced == []
    assert finished == []


def test_demo_ghl_sync_rejects_lead_still_awaiting_decision(client, monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setattr(repo, "get_lead", lambda **k: {
        "id": "lead-1", "intent": "faq", "party_size": None, "event_date": None,
        "status": "drafted", "crm_provider": "none", "run_id": "run-1",
    })
    resp = client.post(
        "/api/demo-ghl-sync", auth=("ops", "ops-pass"), json={"lead_id": "lead-1"},
    )
    assert resp.status_code == 409
    assert len(main_module._graph.invocations) == 0


def test_legacy_reports_route_returns_reload_banner_not_404(client):
    """A tab stuck on the old htmx build polls GET /reports forever since
    htmx never retargets on a 4xx - a 200 banner is what actually reaches it."""
    resp = client.get("/reports", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    assert "out of date" in resp.text.lower()


def test_legacy_draft_route_returns_reload_banner(client):
    resp = client.get("/draft/th-1", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    assert "out of date" in resp.text.lower()


def test_api_reports_returns_json(client, monkeypatch):
    monkeypatch.setattr(repo, "content_gap_report", lambda: [{"missing_fact": "alcohol policy", "occurrences": 3}])
    monkeypatch.setattr(repo, "demand_signal_report", lambda: {
        "total_leads": 5, "by_intent": {"faq": 3, "group_quote": 2},
        "pet_flag_count": 2, "kid_flag_count": 1,
        "by_party_size": {"2-5": 3, "unknown": 2},
        "unconverted_by_status": {"escalated": 2},
    })
    resp = client.get("/api/reports", auth=("ops", "ops-pass"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_gap"][0]["missing_fact"] == "alcohol policy"
    assert body["demand"]["by_intent"]["group_quote"] == 2


# --- public-demo switches (Vercel deployment) -------------------------------

def test_open_access_drops_login_on_command_centre(client, monkeypatch):
    monkeypatch.setenv("DESK_OPEN_ACCESS", "true")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Weekend Enquiry Desk" in resp.text


def test_open_access_drops_login_on_api_routes(client, monkeypatch):
    monkeypatch.setenv("DESK_OPEN_ACCESS", "true")
    resp = client.post("/api/fixture", json={"fixture_id": "msg-01"})
    assert resp.status_code == 202


def test_open_access_keeps_webhook_secret(client, monkeypatch):
    monkeypatch.setenv("DESK_OPEN_ACCESS", "true")
    resp = client.post("/webhook", json={"channel": "whatsapp", "body": "hi", "external_ref": "x1"})
    assert resp.status_code == 401


def test_open_access_is_off_unless_explicitly_true(client, monkeypatch):
    monkeypatch.setenv("DESK_OPEN_ACCESS", "false")
    assert client.get("/").status_code == 401


def _record_threadpool(monkeypatch):
    calls = []

    async def fake_threadpool(fn, *args, **kwargs):
        calls.append(fn.__name__)
        return fn(*args, **kwargs)

    monkeypatch.setattr(main_module, "run_in_threadpool", fake_threadpool)
    return calls


def test_run_inline_runs_graph_inside_the_request(client, monkeypatch):
    monkeypatch.setenv("DESK_RUN_INLINE", "true")
    calls = _record_threadpool(monkeypatch)
    resp = client.post("/api/fixture", json={"fixture_id": "msg-01"}, auth=("ops", "ops-pass"))
    assert resp.status_code == 202
    assert calls == ["_run_graph_background"]
    assert len(main_module._graph.invocations) == 1


def test_default_mode_still_uses_background_tasks(client, monkeypatch):
    calls = _record_threadpool(monkeypatch)
    resp = client.post("/api/fixture", json={"fixture_id": "msg-01"}, auth=("ops", "ops-pass"))
    assert resp.status_code == 202
    assert calls == []
    assert len(main_module._graph.invocations) == 1  # TestClient runs background tasks


def test_run_inline_graph_error_still_returns_202(client, monkeypatch):
    monkeypatch.setenv("DESK_RUN_INLINE", "true")

    class Boom(FakeGraph):
        def invoke(self, arg, config):
            raise RuntimeError("graph exploded")

    main_module.set_graph(Boom())
    resp = client.post("/api/fixture", json={"fixture_id": "msg-01"}, auth=("ops", "ops-pass"))
    assert resp.status_code == 202
