"""research: deterministic. Full corpus, no vector search - see app/tools.py's
search_knowledge_base docstring for why. 'Returned N of N sections' on the run
timeline is a deliberate design choice (P2'), not a degraded retrieval step.
"""
from __future__ import annotations

import time

from app import repo
from app.state import GraphState
from app.tools import search_knowledge_base


def research(state: GraphState) -> dict:
    started = time.monotonic()
    chunks = search_knowledge_base()
    ms = int((time.monotonic() - started) * 1000)
    run_id = state.get("run_id")
    if run_id:
        try:
            repo.record_tool_call(run_id=run_id, tool_name="search_knowledge_base",
                                   args_json={}, result_json={"chunk_count": len(chunks)}, ms=ms)
        except Exception:  # noqa: BLE001 - observability must not break the run
            pass
    return {"_chunks": chunks, "run_status": "running"}
