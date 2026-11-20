"""Twilio WhatsApp sandbox adapter: signature validation + free-form send.

Inbound webhooks and Approve-path sends both go through here. Failures never
raise into ops() - callers get a status dict, matching the CRM sync pattern.
"""
from __future__ import annotations

import os
import re

_E164_RE = re.compile(r"^\+?[1-9]\d{6,14}$")


def whatsapp_configured() -> bool:
    return bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"))


def whatsapp_send_enabled() -> bool:
    return os.environ.get("TWILIO_WHATSAPP_SEND", "").strip().lower() in ("1", "true", "yes", "on")


def normalize_wa_address(value: str | None) -> str | None:
    """Return whatsapp:+E.164 or None if the value cannot be normalized."""
    if not value:
        return None
    raw = value.strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw.startswith("+"):
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return None
        raw = f"+{digits}"
    else:
        raw = "+" + re.sub(r"\D", "", raw)
    if not _E164_RE.match(raw):
        return None
    return f"whatsapp:{raw}"


def validate_request(*, url: str, params: dict, signature: str | None) -> bool:
    """Validate X-Twilio-Signature. Returns False when misconfigured or invalid."""
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not auth_token or not signature:
        return False
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        return False
    validator = RequestValidator(auth_token)
    # Twilio signs the form as string values; coerce everything to str.
    str_params = {str(k): "" if v is None else str(v) for k, v in params.items()}
    return bool(validator.validate(url, str_params, signature))


def send_whatsapp(*, to: str, body: str) -> dict:
    """Send a free-form WhatsApp message via the sandbox/production From number.

    Returns {status: 'synced'|'failed'|'skipped', external_id?, error?, to_address}.
    Never raises.
    """
    to_addr = normalize_wa_address(to)
    if not to_addr:
        return {"status": "failed", "error": f"invalid WhatsApp destination: {to!r}", "to_address": to}

    if not whatsapp_configured():
        return {
            "status": "skipped",
            "error": "TWILIO_ACCOUNT_SID/AUTH_TOKEN not configured",
            "to_address": to_addr,
        }
    if not whatsapp_send_enabled():
        return {
            "status": "skipped",
            "error": "TWILIO_WHATSAPP_SEND is not enabled",
            "to_address": to_addr,
        }

    from_addr = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    from_norm = normalize_wa_address(from_addr) or from_addr
    try:
        from twilio.rest import Client

        client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        msg = client.messages.create(from_=from_norm, to=to_addr, body=body)
        return {
            "status": "synced",
            "external_id": getattr(msg, "sid", None),
            "provider_status": getattr(msg, "status", None),
            "to_address": to_addr,
        }
    except Exception as exc:  # noqa: BLE001 - delivery failures are handled, not re-raised
        return {"status": "failed", "error": str(exc), "to_address": to_addr}
