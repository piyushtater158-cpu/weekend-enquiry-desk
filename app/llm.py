"""The single LLM call site. intake and content are the only two nodes that use
this - everything else in the graph is deterministic. Model swaps via LLM_MODEL
with no code change (OpenRouter), matching the Post-Live Watch pattern.
"""
from __future__ import annotations

import json
import os

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMTimeout(RuntimeError):
    """Raised by the real call path on a timeout. Also raised deliberately by the
    FIXTURE_MODEL_TIMEOUT sentinel, so intake/content's timeout-handling code is
    exercised by the same exception type a real production timeout would raise.
    """


class LLMError(RuntimeError):
    pass


def call_llm(system_prompt: str, user_message: str, *, response_format: dict | None = None) -> dict:
    """Returns the parsed JSON content of the model's response.

    Raises LLMTimeout if the message body contains the FIXTURE_MODEL_TIMEOUT
    sentinel (see tests and data/synthetic_messages.json's fixture notes) or on a
    real network timeout; LLMError on any other failure.
    """
    if "FIXTURE_MODEL_TIMEOUT" in user_message:
        raise LLMTimeout("stubbed slow LLM call (FIXTURE_MODEL_TIMEOUT sentinel)")

    model = os.environ.get("LLM_MODEL")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not model or not api_key:
        raise LLMError("LLM_MODEL and OPENROUTER_API_KEY must both be set")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    if response_format:
        payload["response_format"] = response_format

    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            # 45s, not 10s: verified live against the free model this project
            # actually uses (nvidia/nemotron-3.5-lightning:free) - a 24-chunk
            # corpus in the content prompt took long enough on a free/shared
            # model to trip a 10s timeout on a real (non-fixture) call. The
            # httpx CRM/Telegram calls elsewhere correctly stay at timeout=10 -
            # this is specifically about LLM generation time, a different
            # concern from "is this simple API call hanging."
            timeout=45,
        )
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LLMTimeout(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise LLMError(str(exc)) from exc

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON content: {content[:200]!r}") from exc
