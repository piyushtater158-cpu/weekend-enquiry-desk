"""Supabase IS the lead system of record (see app/repo.py) - this implementation
exists only to satisfy the CRMPort interface uniformly. It has no external
dependency and cannot fail, which is exactly why it is the default provider.
"""
from __future__ import annotations


class SupabaseCRM:
    def create_or_update_lead(
        self, *, lead_id: str, intent: str, party_size: int | None, event_date: str | None,
        contact_ref: str | None = None,
    ) -> str:
        return lead_id
