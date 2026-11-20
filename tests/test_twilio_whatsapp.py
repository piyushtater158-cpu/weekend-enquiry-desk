"""Unit tests for the Twilio WhatsApp sandbox adapter and inbound webhook."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.repo as repo
from app.main import app
from app.twilio_whatsapp import normalize_wa_address, send_whatsapp, whatsapp_send_enabled


def test_normalize_wa_address_variants():
    assert normalize_wa_address("whatsapp:+916001507395") == "whatsapp:+916001507395"
    assert normalize_wa_address("+916001507395") == "whatsapp:+916001507395"
    assert normalize_wa_address("916001507395") == "whatsapp:+916001507395"
    assert normalize_wa_address("") is None
    assert normalize_wa_address(None) is None


def test_whatsapp_send_enabled_flag(monkeypatch):
    monkeypatch.delenv("TWILIO_WHATSAPP_SEND", raising=False)
    assert whatsapp_send_enabled() is False
    monkeypatch.setenv("TWILIO_WHATSAPP_SEND", "true")
    assert whatsapp_send_enabled() is True


def test_send_whatsapp_skipped_when_send_disabled(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_WHATSAPP_SEND", "false")
    result = send_whatsapp(to="whatsapp:+916001507395", body="hi")
    assert result["status"] == "skipped"
    assert "not enabled" in result["error"]


def test_send_whatsapp_success(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_WHATSAPP_SEND", "true")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    class _Msg:
        sid = "SM123"
        status = "queued"

    class _Messages:
        def create(self, **kwargs):
            assert kwargs["to"] == "whatsapp:+916001507395"
            assert kwargs["from_"] == "whatsapp:+14155238886"
            assert kwargs["body"] == "Dogs welcome."
            return _Msg()

    class _Client:
        def __init__(self, sid, token):
            self.messages = _Messages()

    import twilio.rest as twilio_rest
    monkeypatch.setattr(twilio_rest, "Client", _Client)

    result = send_whatsapp(to="+916001507395", body="Dogs welcome.")
    assert result["status"] == "synced"
    assert result["external_id"] == "SM123"


@pytest.fixture
def client(monkeypatch):
    from tests.test_main import FakeGraph

    monkeypatch.setenv("DESK_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("DESK_BASIC_AUTH_USER", "ops")
    monkeypatch.setenv("DESK_BASIC_AUTH_PASS", "ops-pass")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example")
    main_module.set_graph(FakeGraph())
    monkeypatch.setattr(repo, "is_duplicate_external_ref", lambda **k: False)
    monkeypatch.setattr(repo, "create_message", lambda **k: "msg-wa")
    monkeypatch.setattr(repo, "create_agent_run", lambda **k: "run-wa")
    monkeypatch.setattr(repo, "record_rejected_event", lambda **k: None)
    return TestClient(app)


def test_twilio_webhook_rejects_bad_signature(client, monkeypatch):
    rejected = []
    monkeypatch.setattr(repo, "record_rejected_event", lambda **k: rejected.append(k))
    monkeypatch.setattr(
        "app.main.twilio_validate_request", lambda **k: False,
    )
    resp = client.post(
        "/webhooks/twilio/whatsapp",
        data={"Body": "hi", "From": "whatsapp:+916001507395", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "bad"},
    )
    assert resp.status_code == 403
    assert rejected and rejected[0]["reason"] == "twilio_bad_signature"


def test_twilio_webhook_accepts_and_starts_graph(client, monkeypatch):
    created = {}
    monkeypatch.setattr("app.main.twilio_validate_request", lambda **k: True)
    monkeypatch.setattr(repo, "create_message", lambda **k: created.setdefault("msg", k) or "msg-wa")
    monkeypatch.setattr(repo, "create_agent_run", lambda **k: "run-wa")

    before = len(main_module._graph.invocations)
    resp = client.post(
        "/webhooks/twilio/whatsapp",
        data={
            "Body": "Can I bring my dog?",
            "From": "whatsapp:+916001507395",
            "To": "whatsapp:+14155238886",
            "MessageSid": "SMabc123",
        },
        headers={"X-Twilio-Signature": "ok"},
    )
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert "<Response></Response>" in resp.text
    assert created["msg"]["channel"] == "whatsapp"
    assert created["msg"]["body"] == "Can I bring my dog?"
    assert created["msg"]["sender_ref"] == "whatsapp:+916001507395"
    assert created["msg"]["external_ref"] == "SMabc123"
    assert created["msg"]["is_synthetic"] is False
    assert len(main_module._graph.invocations) == before + 1


def test_twilio_webhook_duplicate_message_sid_returns_empty_twiml(client, monkeypatch):
    monkeypatch.setattr("app.main.twilio_validate_request", lambda **k: True)
    monkeypatch.setattr(repo, "is_duplicate_external_ref", lambda **k: True)
    created = []
    monkeypatch.setattr(repo, "create_message", lambda **k: created.append(k) or "x")
    resp = client.post(
        "/webhooks/twilio/whatsapp",
        data={"Body": "hi", "From": "whatsapp:+916001507395", "MessageSid": "SMdup"},
        headers={"X-Twilio-Signature": "ok"},
    )
    assert resp.status_code == 200
    assert created == []
    assert "<Response></Response>" in resp.text
