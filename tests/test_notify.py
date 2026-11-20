import httpx

from app.notify import notify_draft_waiting


def test_notify_not_configured_returns_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify_draft_waiting(lead_id="lead-1", intent="faq", body_preview="hi") is False


def test_notify_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert notify_draft_waiting(lead_id="lead-1", intent="faq", body_preview="hi") is True


def test_notify_redacts_email_and_phone(monkeypatch):
    """Security review: raw guest PII was being sent to Telegram verbatim."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["text"] = json["text"]
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    notify_draft_waiting(
        lead_id="lead-1", intent="faq",
        body_preview="I'm John, call me at +971 50 123 4567 or john@example.com",
    )
    assert "john@example.com" not in captured["text"]
    assert "971 50 123 4567" not in captured["text"]
    assert "[email]" in captured["text"] or "[phone]" in captured["text"]


def test_notify_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def raising_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("500", request=request, response=response)

    monkeypatch.setattr(httpx, "post", raising_post)
    assert notify_draft_waiting(lead_id="lead-1", intent="faq", body_preview="hi") is False
