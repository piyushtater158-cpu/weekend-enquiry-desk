"""create_or_update_lead port. Two implementations: Supabase (always works, no
external dependency) and GoHighLevel (real API, opt-in via CRM_PROVIDER). See
the design doc's D7 - CRM_PROVIDER defaults to supabase so Approve keeps
working indefinitely even after a GHL trial expires.
"""
from __future__ import annotations

from typing import Protocol


class CRMPort(Protocol):
    def create_or_update_lead(
        self, *, lead_id: str, intent: str, party_size: int | None, event_date: str | None,
        contact_ref: str | None = None,
    ) -> str:
        """Returns the external id this lead is synced to. Raises on failure -
        callers (app/nodes/ops.py) catch and record a crm_sync failure row rather
        than letting an exception abort the run.

        contact_ref: the channel's own identifier for the guest (a phone number
        for whatsapp, an address for email), if the channel carries one. Live-
        verified gap: GHL's /contacts/upsert requires at least one of
        email/phone to dedup on and 400s with no identifier at all - which is
        every one of this project's synthetic fixtures, since none carry a
        real contact. Implementations without a real contact_ref (or without
        any identifier policy at all, like SupabaseCRM) should not need one;
        GoHighLevelCRM falls back to a labelled synthetic placeholder.
        """
        ...
