"""The LangGraph wiring. Node functions live in app/nodes/ and are independently
unit-testable; this module only assembles them, instruments each node for the
run timeline, and defines the conditional edges.

Checkpointer: InMemorySaver for tests (no external dependency), PostgresSaver
for real runs (lazy-imported - see app/db.py's docstring on why, on this dev
machine specifically).
"""
from __future__ import annotations

import json
import time

from langgraph.graph import END, StateGraph

from app.nodes.approval import approval
from app.nodes.content import content
from app.nodes.intake import intake
from app.nodes.notify_node import notify_node
from app.nodes.ops import ops
from app.nodes.policy_node import policy_node
from app.nodes.reporting import reporting
from app.nodes.research import research
from app.state import GraphState


def _jsonable(value):
    """Best-effort: run_steps.input_json/output_json are for observability, not
    a contract - drop anything that doesn't round-trip through JSON rather than
    letting a timeline write ever break the actual run.
    """
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


def _instrumented(name, fn):
    """Wraps every node so the command centre's per-node run timeline (named
    explicitly in the design doc and the job posting) is actually populated -
    this did not exist until a live run against the real database exposed it.
    A failure recording the step never fails the run itself.
    """
    def wrapped(state: GraphState) -> dict:
        from langgraph.errors import GraphBubbleUp

        from app import repo

        run_id = state.get("run_id")
        input_snapshot = _jsonable({k: v for k, v in state.items() if not k.startswith("_")})
        try:
            result = fn(state)
        except GraphBubbleUp:
            # interrupt() and other LangGraph control-flow signals - not a node
            # error, must propagate untouched. approval() raises this on every
            # pause; recording it as an "error" step would be actively wrong.
            raise
        except Exception as exc:  # noqa: BLE001 - record then re-raise, never swallow
            error = str(exc)
            if run_id:
                try:
                    repo.record_run_step(run_id=run_id, node=name, input_json=input_snapshot,
                                          output_json=None, error=error)
                except Exception:  # noqa: BLE001 - observability must not break the run
                    pass
            raise
        if run_id:
            try:
                repo.record_run_step(run_id=run_id, node=name, input_json=input_snapshot,
                                      output_json=_jsonable(result))
            except Exception:  # noqa: BLE001
                pass
        return result
    wrapped.__name__ = name
    return wrapped


def _route_after_intake(state: GraphState) -> str:
    # intake's own LLMTimeout handling already wrote a lead as escalated; skip
    # straight to reporting rather than calling research/content on a run that
    # has no draft to make.
    return "reporting" if state.get("run_status") == "error" else "research"


def _route_after_content(state: GraphState) -> str:
    return "reporting" if state.get("run_status") == "error" else "policy"


def _route_after_policy(state: GraphState) -> str:
    next_step = state.get("next_step")
    if next_step == "repair":
        return "content"
    if next_step == "approval":
        return "approval"
    # "escalate" is only set on a failed edit recheck (no override) - or legacy
    # state. "ops" is a passing recheck. Both skip a second human interrupt.
    return "ops"


def _route_after_approval(state: GraphState) -> str:
    if state.get("decision") == "edit":
        return "policy"
    return "ops"


def build_graph(checkpointer=None):
    graph = StateGraph(GraphState)

    graph.add_node("intake", _instrumented("intake", intake))
    graph.add_node("research", _instrumented("research", research))
    graph.add_node("content", _instrumented("content", content))
    graph.add_node("policy", _instrumented("policy", policy_node))
    graph.add_node("notify", _instrumented("notify", notify_node))
    graph.add_node("approval", _instrumented("approval", approval))
    graph.add_node("ops", _instrumented("ops", ops))
    graph.add_node("reporting", _instrumented("reporting", reporting))

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", _route_after_intake, {"research": "research", "reporting": "reporting"})
    graph.add_edge("research", "content")
    graph.add_conditional_edges("content", _route_after_content, {"policy": "policy", "reporting": "reporting"})
    graph.add_conditional_edges("policy", _route_after_policy, {"content": "content", "approval": "notify", "ops": "ops"})
    graph.add_edge("notify", "approval")
    graph.add_conditional_edges("approval", _route_after_approval, {"policy": "policy", "ops": "ops"})
    graph.add_edge("ops", "reporting")
    graph.add_edge("reporting", END)

    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver
        checkpointer = InMemorySaver()

    return graph.compile(checkpointer=checkpointer)


def build_production_graph():
    """PostgresSaver is lazy-imported here, not at module level - see app/db.py.

    Two bugs fixed live against the real deployed shape, not caught by any
    mocked test:

    1. PostgresSaver.from_conn_string() is a @contextmanager generator, not a
       constructor - calling it without `with` returns the generator wrapper
       itself ("Received _GeneratorContextManager").
    2. A single plain psycopg.Connection is NOT safe to share across FastAPI's
       threadpool workers - a background-task thread and a request thread
       both touching the graph hit "the connection is closed". A
       ConnectionPool (which PostgresSaver explicitly supports) is the fix;
       each caller gets its own connection instead of fighting over one.

    Returns (graph, checkpointer).
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from app.db import checkpointer_connection_string

    pool = ConnectionPool(
        checkpointer_connection_string(),
        min_size=1, max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    return build_graph(checkpointer=checkpointer), checkpointer
