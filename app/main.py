"""FastAPI app: webhook, /debug, command centre. The graph runs via
BackgroundTasks, not inline in the request - see app/graph.py and the
eng-review's async-execution fix. A sync psycopg checkpointer called from an
async def handler would otherwise block the event loop.

All /api/* routes are JSON REST resources. GET / is the HTML shell that fetches
those resources; APIs never return HTML partials.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import jinja2
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import repo
from app.graph import build_production_graph
from app.state import GraphState
from app.twilio_whatsapp import validate_request as twilio_validate_request, whatsapp_configured

_graph = None  # lazily built; tests call set_graph() to inject a test double
_checkpointer = None


def get_graph():
    """Security-review fix: this used to default to build_graph() (InMemorySaver),
    which meant every approved run and every pending human-approval interrupt was
    unrecoverable across a process restart. Production always uses PostgresSaver.
    Tests call set_graph() to inject a test double, or build_graph() directly to
    get an InMemorySaver graph without a database - neither goes through here.
    """
    global _graph, _checkpointer
    if _graph is None:
        _graph, _checkpointer = build_production_graph()
    return _graph


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if _checkpointer is not None:
        _checkpointer.conn.close()  # ConnectionPool.close() - security review: this was never closed


app = FastAPI(title="Weekend Enquiry Desk", lifespan=lifespan)
# autoescape=True is explicit, not relied-on-by-default: requirements.txt pins
# no versions, and the security review flagged that Jinja2 autoescaping
# behavior should never be left implicit when guest-controlled text (message
# bodies, policy violation detail strings) renders into these templates.
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader("templates"), autoescape=True,
))
_security = HTTPBasic(auto_error=False)


def set_graph(graph) -> None:
    """Test seam."""
    global _graph
    _graph = graph


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def require_basic_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    # Public-demo deployments set DESK_OPEN_ACCESS=true to drop the login on the
    # command centre and /api/*. Off by default: every other deployment keeps HTTP
    # Basic. The webhook's X-Desk-Secret and the Twilio signature check are
    # separate dependencies and stay on either way.
    if _env_flag("DESK_OPEN_ACCESS"):
        return
    user = os.environ.get("DESK_BASIC_AUTH_USER")
    password = os.environ.get("DESK_BASIC_AUTH_PASS")
    if not user or not password:
        raise HTTPException(500, "DESK_BASIC_AUTH_USER/PASS not configured")
    if credentials is None or not (
        secrets.compare_digest(credentials.username, user)
        and secrets.compare_digest(credentials.password, password)
    ):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})


def _check_webhook_secret(request: Request) -> None:
    expected = os.environ.get("DESK_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(500, "DESK_WEBHOOK_SECRET not configured")
    provided = request.headers.get("X-Desk-Secret")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "bad or missing X-Desk-Secret")


def _run_graph_background(initial_state: GraphState) -> None:
    graph = get_graph()
    config = {"configurable": {"thread_id": initial_state["thread_id"]}}
    graph.invoke(initial_state, config=config)


async def _start_run(background_tasks: BackgroundTasks, initial_state: GraphState) -> None:
    """Default: hand the graph to BackgroundTasks so the request returns 202 at once.

    DESK_RUN_INLINE=true is for serverless hosts (Vercel), which may freeze an
    instance once the response is sent: the graph runs in the threadpool before
    responding instead. Errors are logged, not raised, so the caller still gets the
    same 202 it would have got with a background task - the run's own row and the
    lead created at intake carry the failure, exactly as in background mode.
    """
    if _env_flag("DESK_RUN_INLINE"):
        try:
            await run_in_threadpool(_run_graph_background, initial_state)
        except Exception:  # noqa: BLE001 - mirror BackgroundTasks: never fail the request
            logging.getLogger(__name__).exception("inline graph run failed")
    else:
        background_tasks.add_task(_run_graph_background, initial_state)


def _crm_context() -> dict:
    provider = os.environ.get("CRM_PROVIDER", "supabase")
    location_id = os.environ.get("GHL_LOCATION_ID") or None
    return {
        "provider": provider,
        "ghl_configured": bool(os.environ.get("GHL_PIT_TOKEN")),
        "ghl_location_id": location_id,
        "ghl_pipeline": bool(os.environ.get("GHL_PIPELINE_ID")),
        "wa_sandbox_configured": whatsapp_configured(),
        "wa_sandbox_send": os.environ.get("TWILIO_WHATSAPP_SEND", "").strip().lower()
        in ("1", "true", "yes", "on"),
        "wa_sandbox_demo_to": os.environ.get("TWILIO_SANDBOX_DEMO_TO") or None,
    }


def _empty_twiml() -> Response:
    """Acknowledge Twilio without auto-replying - human Approve is the send gate."""
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


def _twilio_webhook_url(request: Request) -> str:
    """URL Twilio signed. Prefer PUBLIC_BASE_URL (ngrok) over the docker-internal host."""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if base:
        return f"{base}{request.url.path}"
    return str(request.url)


def _ghl_contact_url(*, provider: str | None, location_id: str | None, contact_id: str | None) -> str | None:
    if provider != "gohighlevel" or not location_id or not contact_id:
        return None
    return f"https://app.gohighlevel.com/v2/location/{location_id}/contacts/detail/{contact_id}"


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _snapshot_interrupted(snapshot) -> bool:
    if snapshot is None:
        return False
    tasks = list(getattr(snapshot, "tasks", ()) or ())
    for task in tasks:
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            return True
    return False


def _show_draft_slot(row: dict) -> bool:
    """Draft UI only for actionable, non-terminal leads awaiting a human path.

    run_status is advisory here, not authoritative: a thread can still be
    parked at the approval interrupt while agent_runs.status reads "done" -
    e.g. a demo-ghl-sync/retry-crm call that touched finish_agent_run on a
    lead that was (incorrectly) reachable before it had been decided. See
    _sync_existing_lead's lead-status guard, which stops that at the source
    going forward; this function stays lenient on "done" so an already-
    corrupted row (from before that guard existed) self-heals on the next
    read instead of needing a backfill. "error" (crashed run, no draft ever
    reached approval) and "partial" (already decided, only the CRM sync
    failed - the card shows a CRM retry button instead) are the only
    run_status values that are genuinely incompatible with an actionable
    draft, so those two stay excluded.
    """
    if not row.get("lead_id"):
        return False
    if row.get("run_status") in ("error", "partial"):
        return False
    return row.get("lead_status") in ("new", "drafted", "awaiting_human")


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()

    try:
        _check_webhook_secret(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            repo.record_rejected_event(reason="bad_secret", raw_body=raw_body.decode("utf-8", "replace")[:2000])
        raise exc

    try:
        payload = json.loads(raw_body)
        channel = payload["channel"]
        body = payload["body"]
        external_ref = payload["external_ref"]
    except (json.JSONDecodeError, KeyError) as exc:
        repo.record_rejected_event(reason="malformed_body", raw_body=raw_body.decode("utf-8", "replace")[:2000])
        raise HTTPException(422, f"malformed body: {exc}") from exc

    if repo.is_duplicate_external_ref(external_ref=external_ref):
        repo.record_rejected_event(reason="duplicate_external_ref", raw_body=raw_body.decode("utf-8", "replace")[:2000])
        raise HTTPException(409, "duplicate external_ref - this message was already received")

    message_id = repo.create_message(
        channel=channel, raw_payload=payload, body=body, external_ref=external_ref,
        is_synthetic=bool(payload.get("is_synthetic", False)),
        sender_ref=payload.get("sender_ref"),
    )
    thread_id = secrets.token_hex(16)
    run_id = repo.create_agent_run(message_id=message_id, thread_id=thread_id)

    initial_state: GraphState = {
        "message_id": message_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "body": body,
        "channel": channel,
        "is_synthetic": bool(payload.get("is_synthetic", False)),
        "external_ref": external_ref,
        "sender_ref": payload.get("sender_ref"),
    }
    await _start_run(background_tasks, initial_state)

    return JSONResponse({"run_id": run_id, "thread_id": thread_id, "status": "accepted"}, status_code=202)


@app.post("/webhooks/twilio/whatsapp")
async def twilio_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Inbound Twilio WhatsApp sandbox messages → same graph as POST /webhook.

    Returns empty TwiML (no auto-reply). The operator Approves in the desk; ops
    then optionally sends via Twilio when TWILIO_WHATSAPP_SEND is enabled.
    """
    form = await request.form()
    params = {k: ("" if v is None else str(v)) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    url = _twilio_webhook_url(request)

    if not twilio_validate_request(url=url, params=params, signature=signature):
        raw_snip = "&".join(f"{k}={v}" for k, v in list(params.items())[:12])[:2000]
        repo.record_rejected_event(reason="twilio_bad_signature", raw_body=raw_snip)
        raise HTTPException(403, "invalid Twilio signature")

    body = (params.get("Body") or "").strip()
    sender_ref = params.get("From") or ""
    external_ref = params.get("MessageSid") or params.get("SmsMessageSid") or ""
    if not body or not external_ref:
        repo.record_rejected_event(reason="twilio_malformed_body", raw_body=str(params)[:2000])
        raise HTTPException(422, "Twilio webhook missing Body or MessageSid")

    if repo.is_duplicate_external_ref(external_ref=external_ref):
        # Twilio retries - acknowledge without creating a second run.
        return _empty_twiml()

    message_id = repo.create_message(
        channel="whatsapp",
        raw_payload=params,
        body=body,
        external_ref=external_ref,
        is_synthetic=False,
        sender_ref=sender_ref,
    )
    thread_id = secrets.token_hex(16)
    run_id = repo.create_agent_run(message_id=message_id, thread_id=thread_id)
    initial_state: GraphState = {
        "message_id": message_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "body": body,
        "channel": "whatsapp",
        "is_synthetic": False,
        "external_ref": external_ref,
        "sender_ref": sender_ref,
    }
    await _start_run(background_tasks, initial_state)
    return _empty_twiml()


@app.get("/debug/run")
def debug_run(thread_id: str, _auth: None = Depends(require_basic_auth)):
    graph = get_graph()
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    if snapshot is None:
        raise HTTPException(404, "no state for this thread_id")
    return JSONResponse(_jsonable(dict(snapshot.values)))


@app.post("/api/decide")
async def decide(request: Request, _auth: None = Depends(require_basic_auth)):
    payload = await request.json()
    thread_id = payload["thread_id"]
    action = payload["action"]
    if action not in ("approve", "edit", "dismiss"):
        raise HTTPException(422, f"invalid action: {action!r}")

    from langgraph.types import Command

    resume_payload = {"action": action}
    if action == "edit":
        # Preserve explicit null vs missing vs list for approval.py
        if "edited_sentences" in payload:
            resume_payload["edited_sentences"] = payload["edited_sentences"]

    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    # graph.invoke() is a sync psycopg call via the checkpointer - run it off
    # the event loop (see this module's docstring on why the graph never runs
    # inline in a request). Guard against resuming a thread with no pending
    # interrupt first: a double-click or a stale tab replaying an old decide
    # otherwise re-invokes the graph from whatever state it last stopped at,
    # which for a terminal thread does nothing useful and for a race with
    # another operator's click could re-run ops() a second time. A brand new
    # thread_id (no snapshot at all) is left alone here - LangGraph itself
    # rejects a resume with nothing to resume from.
    snapshot = await run_in_threadpool(graph.get_state, config)
    if snapshot is not None and not _snapshot_interrupted(snapshot):
        raise HTTPException(409, "this draft was already decided - refresh the queue")

    result = await run_in_threadpool(graph.invoke, Command(resume=resume_payload), config=config)
    return JSONResponse(_jsonable(result))


_SYNCABLE_LEAD_STATUSES = ("approved_logged", "awaiting_ops_quote")


def _sync_existing_lead(*, lead_id: str, provider: str | None = None) -> tuple[dict, str | None]:
    """Shared, blocking body for /api/retry-crm and /api/demo-ghl-sync - callers
    must run this via run_in_threadpool, never awaited inline (see those
    handlers' docstrings).
    """
    from app.nodes.ops import sync_lead_to_crm

    lead = repo.get_lead(lead_id=lead_id)
    if lead is None:
        raise HTTPException(404, f"no such lead: {lead_id!r}")

    # A lead still sitting at the human-approval interrupt (status 'new',
    # 'drafted', 'awaiting_human') has no sent_log row yet and no decision to
    # retry - syncing it to the CRM out of band here was the root cause of a
    # live bug where the demo button's no-args fallback (repo.latest_lead_id,
    # "most recently updated") grabbed exactly the lead an operator was still
    # deciding, synced it, and finish_agent_run(status="done") below then
    # made that lead's draft permanently unreachable from the queue even
    # though its thread was still parked at the interrupt. 'escalated' and
    # 'dismissed' have no sent_log either (ops() never calls sync for those
    # actions) - nothing to retry or demo there either.
    if lead["status"] not in _SYNCABLE_LEAD_STATUSES:
        raise HTTPException(
            409,
            f"lead {lead_id!r} is {lead['status']!r} - approve, edit, or dismiss it "
            "before syncing it to the CRM",
        )

    result = sync_lead_to_crm(
        lead_id=lead_id, intent=lead["intent"], party_size=lead["party_size"],
        event_date=str(lead["event_date"]) if lead["event_date"] else None,
        contact_ref=lead.get("sender_ref"),
        provider=provider,
    )

    run_id = lead.get("run_id")
    run_id = str(run_id) if run_id else None
    if run_id and result.get("crm_status") == "synced":
        repo.finish_agent_run(run_id=run_id, status="done")
    result["lead_id"] = lead_id
    result["run_id"] = run_id
    return result, run_id


@app.post("/api/retry-crm")
async def retry_crm(request: Request, _auth: None = Depends(require_basic_auth)):
    payload = await request.json()
    lead_id = payload["lead_id"]
    # _sync_existing_lead makes a blocking httpx.post (GHL) or DB call - never
    # await it inline, or one slow/hung CRM call freezes every other request
    # this worker is serving (queue, drafts, reports) until it times out.
    result, _run_id = await run_in_threadpool(_sync_existing_lead, lead_id=lead_id)
    return JSONResponse(_jsonable(result))


@app.post("/api/demo-ghl-sync")
async def demo_ghl_sync(request: Request, _auth: None = Depends(require_basic_auth)):
    if not os.environ.get("GHL_PIT_TOKEN"):
        raise HTTPException(400, "GHL_PIT_TOKEN not configured - set it in .env to demo GoHighLevel")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine for the header button
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    lead_id = payload.get("lead_id") or repo.latest_lead_id()
    if not lead_id:
        raise HTTPException(404, "no leads in the queue yet - run a fixture and approve it first")

    result, _run_id = await run_in_threadpool(
        _sync_existing_lead, lead_id=lead_id, provider="gohighlevel",
    )
    if result.get("crm_status") == "synced" and result.get("crm_external_id"):
        result["crm_url"] = _ghl_contact_url(
            provider="gohighlevel",
            location_id=os.environ.get("GHL_LOCATION_ID") or None,
            contact_id=result["crm_external_id"],
        )
    return JSONResponse(_jsonable(result))


@app.post("/api/fixture")
async def submit_fixture(request: Request, background_tasks: BackgroundTasks,
                          _auth: None = Depends(require_basic_auth)):
    payload = await request.json()
    fixtures = _all_fixtures()
    fixture = next((f for f in fixtures if f["id"] == payload["fixture_id"]), None)
    if fixture is None:
        raise HTTPException(404, f"no such fixture: {payload.get('fixture_id')!r}")

    external_ref = f"fixture-{fixture['id']}-{secrets.token_hex(4)}"
    message_id = repo.create_message(
        channel=fixture["channel"], raw_payload=fixture, body=fixture["body"],
        external_ref=external_ref, is_synthetic=True,
    )
    thread_id = secrets.token_hex(16)
    run_id = repo.create_agent_run(message_id=message_id, thread_id=thread_id)
    initial_state: GraphState = {
        "message_id": message_id, "run_id": run_id, "thread_id": thread_id,
        "body": fixture["body"], "channel": fixture["channel"], "is_synthetic": True,
        "external_ref": external_ref, "sender_ref": None,
    }
    await _start_run(background_tasks, initial_state)
    return JSONResponse(
        {"run_id": run_id, "thread_id": thread_id, "status": "accepted"},
        status_code=202,
    )


@app.post("/api/rerun")
async def rerun_queue_item(request: Request, background_tasks: BackgroundTasks,
                            _auth: None = Depends(require_basic_auth)):
    """Re-shoot a queue card into the orchestration pipeline.

    Clones the underlying message so the new run keeps the original channel,
    sender, synthetic flag, body, and received_at (arrival). A new external_ref
    is minted (messages.external_ref is unique) and the raw_payload records the
    lineage (original message/run/ref). The source run is marked superseded so
    the old card (including error cards) disappears from the queue and is
    replaced by this new run.
    """
    payload = await request.json()
    source_run_id = payload.get("run_id")
    if not source_run_id:
        raise HTTPException(422, "run_id is required")

    source = repo.get_message_for_run(run_id=str(source_run_id))
    if source is None:
        raise HTTPException(404, f"no such run: {source_run_id!r}")

    original_payload = source.get("raw_payload") or {}
    if not isinstance(original_payload, dict):
        original_payload = {}
    rerun_payload = {
        **original_payload,
        "channel": source["channel"],
        "body": source["body"],
        "is_synthetic": bool(source["is_synthetic"]),
        "sender_ref": source.get("sender_ref"),
        "rerun_of": {
            "message_id": str(source["message_id"]),
            "run_id": str(source["run_id"]),
            "external_ref": source["external_ref"],
            "received_at": source["received_at"].isoformat()
            if hasattr(source["received_at"], "isoformat") else str(source["received_at"]),
        },
    }
    # Keep a stable source identity in the payload; DB uniqueness still needs a
    # fresh external_ref so this is a new intake event, not a duplicate webhook.
    external_ref = f"rerun-{source['external_ref']}-{secrets.token_hex(4)}"
    rerun_payload["external_ref"] = external_ref

    message_id = repo.create_message(
        channel=source["channel"],
        raw_payload=rerun_payload,
        body=source["body"],
        external_ref=external_ref,
        is_synthetic=bool(source["is_synthetic"]),
        sender_ref=source.get("sender_ref"),
        received_at=source["received_at"],
    )
    thread_id = secrets.token_hex(16)
    run_id = repo.create_agent_run(message_id=message_id, thread_id=thread_id)
    # Drop the old card from the queue now that its replacement exists.
    repo.supersede_agent_run(run_id=str(source["run_id"]), replaced_by_run_id=run_id)
    initial_state: GraphState = {
        "message_id": message_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "body": source["body"],
        "channel": source["channel"],
        "is_synthetic": bool(source["is_synthetic"]),
        "external_ref": external_ref,
        "sender_ref": source.get("sender_ref"),
    }
    await _start_run(background_tasks, initial_state)
    return JSONResponse(
        _jsonable({
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "accepted",
            "replaced_run_id": str(source["run_id"]),
            "source_run_id": str(source["run_id"]),
            "source_message_id": str(source["message_id"]),
            "received_at": source["received_at"],
            "channel": source["channel"],
            "external_ref": external_ref,
        }),
        status_code=202,
    )


def _data_dir():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "data"


def _all_fixtures() -> list[dict]:
    synthetic = json.loads((_data_dir() / "synthetic_messages.json").read_text(encoding="utf-8"))
    errors = json.loads((_data_dir() / "error_fixtures.json").read_text(encoding="utf-8"))
    return synthetic + errors


@app.get("/", response_class=HTMLResponse)
def command_centre(request: Request, _auth: None = Depends(require_basic_auth)):
    response = templates.TemplateResponse(request, "index.html", {
        "fixtures": _all_fixtures(), "crm": _crm_context(),
    })
    # The shell was rewritten from htmx partials to a JSON-REST client
    # (data-build="rest-v1") without a cache-busted asset path - a tab that
    # cached the old shell keeps calling endpoints (e.g. the old build's
    # GET /reports) that no longer exist. no-store means a plain reload
    # always re-fetches the current shell instead of serving a stale one
    # from the browser's disk cache.
    response.headers["Cache-Control"] = "no-store"
    return response


_LEGACY_RELOAD_BANNER = (
    "<!doctype html><html><body style='font-family: sans-serif; padding: 3rem; "
    "max-width: 32rem;'><h1>This page is out of date</h1>"
    "<p>The command centre moved to a new build. Press Ctrl+Shift+R (or close "
    "this tab) and reopen the desk to pick it up.</p></body></html>"
)


def _legacy_reload_banner() -> HTMLResponse:
    """A handful of routes existed only on the pre-rest-v1 htmx build
    (/reports, /queue, /timeline/{run_id}, /draft/{thread_id} returned
    rendered partials; see templates/_queue.html et al., deleted alongside
    this). A tab still open on that build keeps polling them forever - htmx
    does not retarget or stop an hx-trigger on a 4xx, so a 404/410 here is
    invisible to the user and just repeats in the container logs. A 200
    HTML banner lands exactly where that stale tab's hx-swap is already
    looking and tells the operator to reload, instead of silently churning.
    """
    response = HTMLResponse(_LEGACY_RELOAD_BANNER)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/reports")
@app.get("/queue")
@app.get("/timeline/{run_id}")
@app.get("/draft/{thread_id}")
def legacy_build_reload_banner(run_id: str | None = None, thread_id: str | None = None):
    return _legacy_reload_banner()


def _age_minutes(started_at) -> int | None:
    if started_at is None:
        return None
    delta = datetime.now(timezone.utc) - (
        started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    )
    return int(delta.total_seconds() // 60)


def _age_label(minutes: int | None) -> str:
    if minutes is None:
        return "-"
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m"


@app.get("/api/queue")
def api_queue(_auth: None = Depends(require_basic_auth)):
    crm = _crm_context()
    rows = repo.list_queue()
    stats = {"awaiting": 0, "partial": 0, "logged": 0}
    out_rows = []
    for row in rows:
        minutes = _age_minutes(row.get("started_at"))
        item = dict(row)
        item["age"] = _age_label(minutes)
        item["stale"] = minutes is not None and minutes > 15
        item["crm_url"] = _ghl_contact_url(
            provider=row.get("crm_provider"), location_id=crm["ghl_location_id"],
            contact_id=row.get("crm_contact_id"),
        )
        item["show_draft"] = _show_draft_slot(item)
        # Approved/send path writes sent_log; surface it so the queue can expand
        # the card and show the (read-only) reply that was logged for this run.
        item["show_sent_reply"] = bool(item.get("sent_body")) and (
            item.get("lead_status") in _SYNCABLE_LEAD_STATUSES
        )
        # Serialize dates/UUIDs
        item = _jsonable(item)
        out_rows.append(item)

        lead_status = row.get("lead_status")
        run_status = row.get("run_status")
        if lead_status in ("new", "drafted", "awaiting_human") and run_status not in ("error", "done", "partial"):
            stats["awaiting"] += 1
        if run_status == "partial":
            stats["partial"] += 1
        if lead_status in ("approved_logged", "awaiting_ops_quote"):
            stats["logged"] += 1

    return JSONResponse({"stats": stats, "rows": out_rows, "crm": crm})


@app.get("/api/runs/{run_id}/timeline")
def api_timeline(run_id: str, _auth: None = Depends(require_basic_auth)):
    timeline = repo.get_run_timeline(run_id)
    return JSONResponse(_jsonable({"run_id": run_id, **timeline}))


@app.get("/api/drafts/{thread_id}")
def api_draft(thread_id: str, _auth: None = Depends(require_basic_auth)):
    """JSON draft from the checkpointer, plus whether the graph is interrupted."""
    graph = get_graph()
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values if snapshot is not None else None
    draft = (values or {}).get("draft")
    return JSONResponse(_jsonable({
        "thread_id": thread_id,
        "draft": draft,
        "lead_status": (values or {}).get("lead_status"),
        "run_status": (values or {}).get("run_status"),
        "policy_result": (values or {}).get("policy_result"),
        "interrupted": _snapshot_interrupted(snapshot),
    }))


@app.get("/api/reports")
def api_reports(_auth: None = Depends(require_basic_auth)):
    return JSONResponse(_jsonable({
        "content_gap": repo.content_gap_report(),
        "demand": repo.demand_signal_report(),
    }))
