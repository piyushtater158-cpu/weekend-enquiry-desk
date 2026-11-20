from __future__ import annotations

from app.crm.base import CRMPort
from app.crm.gohighlevel import GoHighLevelCRM
from app.crm.supabase_crm import SupabaseCRM


def get_crm(provider: str) -> CRMPort:
    if provider == "gohighlevel":
        return GoHighLevelCRM()
    if provider == "supabase":
        return SupabaseCRM()
    raise ValueError(f"unknown CRM_PROVIDER: {provider!r}")
