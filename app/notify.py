"""Operator draft-waiting ping. Telegram, not WhatsApp - a business-initiated
WhatsApp message to the operator's own number would need a pre-approved
utility template in a billable category (no open service window), which this
design's scope excludes. timeout=10, matching the same Performance fix as the
CRM calls - this sits in the request path of every run that reaches approval.
"""
from __future__ import annotations

import os
import re

import httpx

TELEGRAM_API = "https://api.telegram.org"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{6,}\d)")


def _redact(text: str) -> str:
    """Security review: the raw guest message (names, phone numbers, emails)
    was being sent verbatim to Telegram and stored there permanently. The
    operator ping only needs enough text to recognize the enquiry, not the PII.
    """
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


def notify_draft_waiting(*, lead_id: str, intent: str, body_preview: str) -> bool:
    """Returns True if the ping was sent, False if notify is not configured or
    failed - never raises, because a failed notification must not fail the run.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    redacted_preview = _redact(body_preview)[:40]
    text = f"Draft waiting - {intent} - lead {lead_id[:8]} - {redacted_preview}"
    try:
        resp = httpx.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
