import httpx
import pytest

from app.crm import get_crm
from app.crm.gohighlevel import GoHighLevelCRM, GoHighLevelError
from app.crm.supabase_crm import SupabaseCRM


def test_get_crm_supabase_default():
    crm = get_crm("supabase")
    assert isinstance(crm, SupabaseCRM)


def test_supabase_crm_returns_lead_id_unchanged():
    crm = SupabaseCRM()
    result = crm.create_or_update_lead(lead_id="abc-123", intent="faq", party_size=None, event_date=None)
    assert result == "abc-123"


def test_get_crm_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_crm("not-a-real-provider")


def test_gohighlevel_requires_token(monkeypatch):
    monkeypatch.delenv("GHL_PIT_TOKEN", raising=False)
    with pytest.raises(GoHighLevelError):
        GoHighLevelCRM()


def test_gohighlevel_upsert_success(monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")

    def fake_post(url, headers=None, json=None, timeout=None):
        assert "contacts/upsert" in url
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-contact-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    crm = GoHighLevelCRM()
    result = crm.create_or_update_lead(lead_id="abc-123", intent="faq", party_size=None, event_date=None)
    assert result == "ghl-contact-1"


def test_gohighlevel_upsert_401_raises(monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            401, json={"message": "The token is not authorized for this scope."}, request=request,
        ).raise_for_status()

    def raising_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        response = httpx.Response(401, json={"message": "not authorized"}, request=request)
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(httpx, "post", raising_post)
    crm = GoHighLevelCRM()
    with pytest.raises(GoHighLevelError):
        crm.create_or_update_lead(lead_id="abc-123", intent="faq", party_size=None, event_date=None)


def test_gohighlevel_group_quote_creates_opportunity_when_pipeline_configured(monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.setenv("GHL_PIPELINE_ID", "pipeline-1")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        request = httpx.Request("POST", url)
        if "contacts/upsert" in url:
            return httpx.Response(200, json={"contact": {"id": "ghl-contact-1"}}, request=request)
        return httpx.Response(200, json={"opportunity": {"id": "opp-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    crm = GoHighLevelCRM()
    crm.create_or_update_lead(lead_id="abc-123", intent="group_quote", party_size=20, event_date="2026-11-21")
    assert any("opportunities" in c for c in calls)


def test_gohighlevel_falls_back_to_synthetic_email_with_no_contact_ref(monkeypatch):
    """Live-verified bug: GHL's /contacts/upsert 400s with no email/phone to
    dedup on - "Pass at least one of number, email query parameter". Every
    synthetic fixture lacks a real contact, so this fallback must exist."""
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-contact-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    crm = GoHighLevelCRM()
    crm.create_or_update_lead(lead_id="abc-123", intent="faq", party_size=None, event_date=None)
    assert "@" in captured["payload"]["email"]
    assert "synthetic-contact" in captured["payload"]["tags"]


def test_gohighlevel_uses_real_contact_ref_when_provided(monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-contact-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    crm = GoHighLevelCRM()
    crm.create_or_update_lead(lead_id="abc-123", intent="faq", party_size=None, event_date=None,
                               contact_ref="+971521141519")
    assert captured["payload"]["phone"] == "+971521141519"
    assert "email" not in captured["payload"]


def test_gohighlevel_strips_whatsapp_prefix_from_contact_ref(monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-contact-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    crm = GoHighLevelCRM()
    crm.create_or_update_lead(
        lead_id="abc-123", intent="faq", party_size=None, event_date=None,
        contact_ref="whatsapp:+15555550100",
    )
    assert captured["payload"]["phone"] == "+15555550100"
    assert "email" not in captured["payload"]


def test_gohighlevel_group_quote_skips_opportunity_without_pipeline(monkeypatch):
    monkeypatch.setenv("GHL_PIT_TOKEN", "fake-token")
    monkeypatch.delenv("GHL_PIPELINE_ID", raising=False)
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"contact": {"id": "ghl-contact-1"}}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    crm = GoHighLevelCRM()
    crm.create_or_update_lead(lead_id="abc-123", intent="group_quote", party_size=20, event_date="2026-11-21")
    assert not any("opportunities" in c for c in calls)
