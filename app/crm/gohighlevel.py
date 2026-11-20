"""Real GoHighLevel integration. API v2, Private Integration Token auth.
POST /contacts/upsert dedupes on email/phone using the location's own duplicate
settings - exactly create_or_update_lead semantics. Opportunity creation (for
group_quote leads) is attempted only if GHL_PIPELINE_ID is configured; its id
has to be read from the GHL UI by hand (see Open Questions in the design doc).

timeout=10 on every call - an unbounded call here sits in the live request path
serving the demo (see the eng-review Performance finding).
"""
from __future__ import annotations

import os

import httpx

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"


class GoHighLevelError(RuntimeError):
    pass


class GoHighLevelCRM:
    def __init__(self) -> None:
        self._token = os.environ.get("GHL_PIT_TOKEN")
        self._location_id = os.environ.get("GHL_LOCATION_ID")
        self._pipeline_id = os.environ.get("GHL_PIPELINE_ID")
        if not self._token:
            raise GoHighLevelError("GHL_PIT_TOKEN is not set")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Version": API_VERSION,
            "Content-Type": "application/json",
        }

    def create_or_update_lead(
        self, *, lead_id: str, intent: str, party_size: int | None, event_date: str | None,
        contact_ref: str | None = None,
    ) -> str:
        payload: dict = {
            "firstName": f"WeekendDesk-{lead_id[:8]}",
            "tags": ["weekend-enquiry-desk", f"intent:{intent}"],
        }
        if self._location_id:
            payload["locationId"] = self._location_id

        # GHL's /contacts/upsert 400s with no email/phone to dedup on - live-
        # verified, not a guess. Every synthetic fixture lacks a real contact,
        # so fall back to a labelled synthetic email rather than failing every
        # demo run of the GHL path. A real contact_ref (whatsapp number, email
        # address) always wins when the channel actually carries one.
        #
        # Twilio WhatsApp inbound sets sender_ref to "whatsapp:+E164"; GHL's
        # phone field wants bare E.164, so strip the channel prefix first.
        if contact_ref and "@" in contact_ref:
            payload["email"] = contact_ref
        elif contact_ref:
            phone = contact_ref.strip()
            for prefix in ("whatsapp:", "sms:", "tel:"):
                if phone.lower().startswith(prefix):
                    phone = phone[len(prefix):].strip()
                    break
            payload["phone"] = phone
        else:
            payload["email"] = f"synthetic-{lead_id}@weekend-enquiry-desk.invalid"
            payload["tags"].append("synthetic-contact")

        try:
            resp = httpx.post(
                f"{BASE_URL}/contacts/upsert", headers=self._headers(), json=payload, timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GoHighLevelError(f"contacts/upsert failed: {exc} - {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise GoHighLevelError(f"contacts/upsert failed: {exc}") from exc

        contact = resp.json().get("contact", {})
        contact_id = contact.get("id")
        if not contact_id:
            raise GoHighLevelError(f"contacts/upsert returned no contact id: {resp.text[:300]}")

        if intent == "group_quote" and self._pipeline_id:
            self._create_opportunity(contact_id=contact_id, party_size=party_size, event_date=event_date)

        return contact_id

    def _create_opportunity(self, *, contact_id: str, party_size: int | None, event_date: str | None) -> None:
        payload = {
            "pipelineId": self._pipeline_id,
            "contactId": contact_id,
            "name": f"Group quote ({party_size or '?'} people, {event_date or 'date TBD'})",
        }
        if self._location_id:
            payload["locationId"] = self._location_id
        try:
            resp = httpx.post(
                f"{BASE_URL}/opportunities/", headers=self._headers(), json=payload, timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GoHighLevelError(f"opportunity creation failed: {exc}") from exc
