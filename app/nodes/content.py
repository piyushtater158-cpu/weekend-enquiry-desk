"""content: the second and last model call. Drafts a structured reply -
{"sentences": [{"text", "cites", "non_factual"}], "urls": [...]} - not a plain
string, per the eng-review fix: classifying "factual vs. pleasantry" and
"affirmation vs. deflection" is language understanding, not pattern matching, so
PR-03/PR-05 validate this structure directly instead of parsing prose with regex.

On a policy repair (repair_attempted=True in state), the violations from the
previous attempt are included in the prompt so the model can address them -
one retry total, regardless of which rule failed.
"""
from __future__ import annotations

import time

from app import llm, repo
from app.state import GraphState
from app.tools import get_official_links

_SYSTEM_PROMPT = """You draft a reply to a beach-brand guest enquiry, using ONLY the
corpus sections and official links given to you. Output exactly this JSON shape:
{"sentences": [{"text": "...", "cites": ["section_id", ...], "non_factual": true/false}],
 "urls": ["https://...", ...]}
Rules:
- Every factual sentence must cite at least one section_id that actually supports it.
- A pleasantry ("looking forward to seeing you!") is non_factual: true and needs no citation.
- Never state a price or currency amount. Never claim availability ("we have space",
  "sold out"). Never state a policy (refund, cancel, re-entry, age limit, alcohol)
  unless a section explicitly supports it.
- Only use URLs from the official links list given to you.
- If asked whether camping is included/bundled with the day pass, answer with BOTH
  the day-pass and camping links and a line offering to confirm with the team -
  never a bare yes or no, because the corpus does not state a bundling policy.
"""


def content(state: GraphState) -> dict:
    chunks = state.get("_chunks", [])
    _t0 = time.monotonic()
    links = get_official_links()
    run_id = state.get("run_id")
    if run_id:
        try:
            repo.record_tool_call(run_id=run_id, tool_name="get_official_links", args_json={},
                                   result_json={"link_count": len(links)},
                                   ms=int((time.monotonic() - _t0) * 1000))
        except Exception:  # noqa: BLE001
            pass
    corpus_text = "\n".join(f"[{c['section_id']}] {c['text']}" for c in chunks)
    links_text = "\n".join(f"{l['key']}: {l['url']}" for l in links)

    prior_violations = ""
    if state.get("repair_attempted"):
        prior = state.get("policy_result", {}).get("violations", [])
        prior_violations = (
            "\nYour previous draft failed these policy checks - fix them: "
            + "; ".join(f"{v['rule_id']}: {v['detail']}" for v in prior)
        )

    user_message = (
        f"Guest message: {state['body']}\n\n"
        f"Corpus sections:\n{corpus_text}\n\n"
        f"Official links:\n{links_text}"
        f"{prior_violations}"
    )

    try:
        result = llm.call_llm(_SYSTEM_PROMPT, user_message)
    except llm.LLMTimeout:
        return {
            "run_status": "error",
            "error_kind": "model_timeout",
            "error_detail": "content LLM call timed out",
        }

    sentences = result.get("sentences", [])
    urls = result.get("urls", [])
    return {"draft": {"sentences": sentences, "urls": urls}, "run_status": "running"}
