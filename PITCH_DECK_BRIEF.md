# Weekend Enquiry Desk — Pitch Deck & Interview Brief

**Purpose:** One source of truth for building the SUNKSSD application pitch deck, written walkthrough, demo script, and live interview answers. Every claim below is grounded in this repo (`README.md`, `SYSTEM_EXPLAINER.md`, code under `app/`, `tests/`, `migrations/`).

**Role targeted:** [AI Automation & Agentic Systems Engineer — SUNKSSD](https://wellfound.com/jobs/5035084-ai-automation-agentic-systems-engineer)  
**Company:** SUNKSSD (music / wellness / pets / weekends at Lumi Beach, Umm Al Quwain; also building an AI-native agency stack)  
**Compensation band (posted):** USD 2,000–3,000 / month (USD 24k–36k annual), 100% remote  
**Apply to:** `info@sunkssd.io` — subject `AI Engineer Application — [Your Name]`  
**They also want:** CV, GitHub, 1–2 shipped projects + your contribution, short video **or** written walkthrough of an agent system, salary expectations, availability, timezone  
**Process note:** Selected candidates get a **paid** 3–5 hour scoped technical assessment (not unpaid commercial work)

---

## How to use this document

| Goal | Sections to mine |
|---|---|
| Build slides | §1 pitch narrative, §2 JD→proof matrix, §3 suggested slide outline, §9 demo script |
| **Explain orchestration in depth** | **§O (Orchestration deep dive) — primary** |
| Written / video walkthrough | §4 system story, §5 agents & tools, §O, §6 command centre, §9 demo script |
| Interview depth | §O, §7 judgment calls, §8 integrations, §10 failure modes, §11 Q&A bank, §12 honest limits |
| Application email copy | §13 application packet checklist + one-paragraph project blurb |
| Numbers & receipts | §14 metrics & inventory, §15 tech stack |

Companion deep-dives already in-repo (do not duplicate live; cite them):
- `SYSTEM_EXPLAINER.md` — teaching-grade architecture walkthrough
- `README.md` — constraints, policy table, live-validation stories
- `TODOS.md` — deliberate deferrals (shows scoping judgment)

---

## O. Orchestration deep dive (explain this in depth)

This is the section to rehearse until you can whiteboard it without notes. The whole product is an **orchestrated state machine**, not a chatbot loop. Source of truth: `app/graph.py`, `app/main.py`, `app/state.py`, nodes under `app/nodes/`.

### O.1 What “orchestration” means here

Three layers work together:

| Layer | What it does | Where |
|---|---|---|
| **Ingress / runtime** | Accepts the enquiry, creates DB rows, starts the graph **off the request thread**, resumes it later on human action | `app/main.py` |
| **Graph (LangGraph `StateGraph`)** | Orders specialized nodes, chooses the next node via **conditional edges**, pauses durably at human approval | `app/graph.py` |
| **Shared state (`GraphState`)** | The contract every node reads/writes; LangGraph merges partial dict updates | `app/state.py` |

**One sentence you can say:**  
“I orchestrate eight specialized nodes on a shared TypedDict state. Only two call an LLM. Routers decide the next step from state flags like `run_status`, `next_step`, and `decision`. Human approval is a real LangGraph `interrupt()` checkpointed in Postgres — not a polling hack.”

### O.2 Master flow diagram (memorize / put on a slide)

```
                    ┌─────────────────────────────────────────┐
  Channel / form /  │  POST /webhook  OR  Twilio WA webhook   │
  fixture button ──►│  OR  POST /api/fixture  (/api/rerun)    │
                    └──────────────────┬──────────────────────┘
                                       │ validate secret / signature
                                       │ create messages + agent_runs
                                       │ BackgroundTasks → graph.invoke()
                                       ▼
                              ┌────────────────┐
                              │    INTAKE      │  LLM #1 — classify
                              │  create lead   │  (lead owned HERE)
                              └───────┬────────┘
                     error?           │ ok
                 ┌────────────────────┤
                 ▼                    ▼
          REPORTING ◄───      ┌──────────────┐
             (END)            │   RESEARCH   │  no LLM — full corpus tool
                              └──────┬───────┘
                                     ▼
                              ┌──────────────┐
                    ┌────────►│   CONTENT    │  LLM #2 — structured draft
                    │         │ (+ repair)   │  may include prior PR-IDs
                    │         └──────┬───────┘
                    │  error?        │ ok
                    │     ┌──────────┤
                    │     ▼          ▼
                    │ REPORTING   ┌──────────────┐
                    │             │    POLICY    │  11 deterministic rules
                    │             └──────┬───────┘
                    │                    │ sets next_step =
                    │     repair ◄───────┤  repair | approval | escalate | ops
                    └────────────────────┘
                                         │
                    approval ────────────┤──► NOTIFY (Telegram once;
                                         │         lead → awaiting_human)
                                         │           │
                                         │           ▼
                                         │     ┌───────────┐
                                         │     │ APPROVAL  │  interrupt()
                                         │     │  HITL     │  waits for human
                                         │     └─────┬─────┘
                                         │           │ resume: approve|edit|dismiss
                                         │     edit ──► back to POLICY (is_recheck)
                                         │           │
                          escalate/ops ──┘           ▼
                                              ┌───────────┐
                                              │    OPS    │  decision, sent_log,
                                              │           │  CRM, optional WA send
                                              └─────┬─────┘
                                                    ▼
                                              ┌───────────┐
                                              │ REPORTING │  missing_fact + finish run
                                              └─────┬─────┘
                                                    ▼
                                                   END
```

### O.3 How a run is started (ingress orchestration)

A run never begins “inside the LLM.” It begins as an HTTP event:

1. **Auth / reject early**
   - Desk webhook: `X-Desk-Secret` via `hmac.compare_digest`. Bad secret → `rejected_events`, **401**, no run.
   - Twilio: validate `X-Twilio-Signature` against `PUBLIC_BASE_URL` + form params. Bad sig → **403**, no run.
2. **Parse + idempotency**
   - Require `channel`, `body`, `external_ref` (or Twilio `Body` + `MessageSid`).
   - Duplicate `external_ref` → **409** (desk) or empty TwiML ack (Twilio retries) — never a second graph.
3. **Persist before compute**
   - `messages` row + `agent_runs` row (`thread_id` = random hex; this is LangGraph’s durable key).
4. **Hand off to background**
   - Build initial `GraphState` (`message_id`, `run_id`, `thread_id`, `body`, `channel`, `is_synthetic`, `external_ref`, `sender_ref`).
   - `BackgroundTasks.add_task(_run_graph_background, initial_state)`.
   - HTTP returns **202 Accepted** with `{run_id, thread_id}` immediately.

**Why BackgroundTasks (say this):**  
The checkpointer is **sync psycopg**. Two LLM calls + a possible `interrupt()` would block FastAPI’s event loop if run inline. Orchestration of the *agents* happens in the graph; orchestration of the *HTTP lifecycle* keeps the request non-blocking.

**Production checkpointer:** `build_production_graph()` → `PostgresSaver` on a `psycopg_pool.ConnectionPool` (min 1 / max 5). Tests inject `InMemorySaver`. Security-critical: never default production to in-memory (pending approvals would vanish on restart).

### O.4 Graph assembly (`build_graph`)

In `app/graph.py`:

```
nodes:  intake, research, content, policy, notify, approval, ops, reporting
entry:  intake

edges:
  intake  --conditional--> research | reporting
  research --------------> content
  content --conditional--> policy | reporting
  policy  --conditional--> content | notify | ops
                              ^repair    ^approval path
  notify  ------------------> approval
  approval--conditional--> policy | ops
                              ^edit
  ops ---------------------> reporting
  reporting ---------------> END
```

Every node is wrapped with `_instrumented(name, fn)` so the command centre timeline is automatic:
- Snapshot input state (keys not starting with `_`)
- Call the node
- On success: `repo.record_run_step(...)` with output
- On real exception: record error step, re-raise
- On `GraphBubbleUp` (LangGraph control flow from `interrupt()`): **re-raise untouched** — never log a pause as a node failure

**Talking point:** Observability is part of orchestration design — logging is a cross-cutting wrapper at assembly time, not copy-pasted into eight nodes.

### O.5 The four routers (this is the “brain” of orchestration)

Routers are plain Python functions. They do **not** call an LLM. They read state and return the next node name.

#### After intake — `_route_after_intake`
| Condition | Next | Why |
|---|---|---|
| `run_status == "error"` | `reporting` | Intake already created an escalated lead on timeout; skip research/content |
| else | `research` | Continue happy path |

#### After content — `_route_after_content`
| Condition | Next | Why |
|---|---|---|
| `run_status == "error"` | `reporting` | Content timed out; no draft to police |
| else | `policy` | Draft ready for the gate |

#### After policy — `_route_after_policy` (most important)
Policy node does not jump itself; it sets `next_step`, then the router maps it:

| `next_step` | Router returns | Actual next node | Meaning |
|---|---|---|---|
| `"repair"` | `"content"` | `content` again | First failure → one targeted rewrite |
| `"approval"` | `"approval"` | **`notify`** (edge label maps approval→notify) | Pass, or repair exhausted → park for human |
| `"ops"` / `"escalate"` / other | `"ops"` | `ops` | Passing **edit recheck**, or **failed edit recheck** (skip second interrupt) |

**Critical nuance to explain correctly:**  
Graph edge config is `{"content": "content", "approval": "notify", "ops": "ops"}`. So when policy says “go to approval,” the graph first runs **`notify`**, then `approval`. That is deliberate orchestration: ping once, then pause.

#### After approval — `_route_after_approval`
| Condition | Next | Why |
|---|---|---|
| `decision == "edit"` | `policy` | Edited body must re-enter the full gate (`is_recheck=True`) |
| else (`approve` / `dismiss`) | `ops` | Execute the human decision |

### O.6 Policy node’s routing decisions (how `next_step` is chosen)

`policy_node` runs `evaluate()` (11 rules, zero LLM), writes `policy_violations` rows, then:

| Situation | `next_step` | What happens next |
|---|---|---|
| Rules **pass**, first time (not a recheck) | `approval` | → notify → interrupt |
| Rules **pass**, and `is_recheck` | `ops` | Human already approved intent by editing; skip second interrupt |
| Rules **fail**, and `is_recheck` | `escalate` | **No override** — fake price typed into edit box cannot slip through |
| Rules **fail**, first attempt (`repair_attempted` false) | `repair` (+ set `repair_attempted=True`) | → content with prior PR-IDs in the prompt |
| Rules **fail**, repair already used | `approval` | **Do not auto-escalate** — park at human queue; operator may Approve (override), Edit (recheck), or Dismiss |

**Say this carefully (it impresses):**  
“The model gets exactly one repair. If it still fails, I don’t silently kill the enquiry — I park it for a human. But if a *human* edits in something that fails policy, there is no override: it escalates. Trust the operator’s Approve on a parked draft; never trust an unvalidated edit.”

### O.7 Node-by-node orchestration contract

Each node returns a **partial** state update. LangGraph merges it. Nodes are independently unit-testable.

#### 1) `intake` — LLM #1, lead ownership
- **Reads:** `body`, `message_id`
- **Does:** Structured classify → intent, event_date, party_size, pet/kid, language, camping-bundle flag. Resolves “this Saturday” against `DEMO_NOW` or real today.
- **Writes:** lead row as `new` (or `escalated` on timeout), classification fields, `run_status`
- **Orchestration role:** Creates the business object *before* expensive drafting so failed runs still appear in demand reports and the queue.

#### 2) `research` — deterministic retrieval
- **Reads:** (implicitly just continues the run)
- **Does:** `search_knowledge_base()` — full corpus, timed `tool_calls` row
- **Writes:** `_chunks` (underscore = internal; timeline snapshots exclude `_` keys)
- **Orchestration role:** Separates retrieval from generation so content never “invents” its knowledge source, and research stays mockable/testable with no model.

#### 3) `content` — LLM #2, structured draft
- **Reads:** `body`, `_chunks`, optional `repair_attempted` + `policy_result.violations`
- **Does:** Calls `get_official_links` (logged tool), builds prompt, returns `{sentences[{text,cites,non_factual}], urls}`
- **On repair:** Appends exact failing `PR-xx: detail` lines so the retry is targeted, not a blind re-roll
- **Orchestration role:** Only generative writer in the system; bounded by one repair flag from policy/router.

#### 4) `policy` — deterministic gate + routing signal
- **Reads:** draft, chunks, intent, event_date, camping flag, corpus freshness
- **Does:** Pure `evaluate()`; records violations; sets `next_step`
- **Orchestration role:** The **control plane** between generation and humans — decides repair vs park vs escalate vs ops.

#### 5) `notify` — once-only side effect before pause
- **Reads:** lead_id, intent, body
- **Does:** Set lead `awaiting_human`; Telegram ping with PII redaction
- **Orchestration role:** Lives in its **own node** because `approval` re-executes from the top on every resume. Side effects must not sit inside the interrupt node or the operator gets duplicate pings.

#### 6) `approval` — durable human interrupt
- **Does:** `interrupt({draft, policy_result, lead_id})` → graph pauses; state checkpointed under `thread_id`
- **Resumes via:** `POST /api/decide` → `Command(resume={action, edited_sentences?})`
- **On edit:** Replace draft sentences; set `is_recheck=True`; router sends back to policy
- **Orchestration role:** The only place a human enters the control flow. Resume is guarded: if snapshot has no pending interrupt → **409** (double-click / stale tab cannot re-run ops).

#### 7) `ops` — internal operations / side effects after decision
- **Decides action:** dismiss | send | escalate (`_ops_action`)
  - `dismiss` → lead dismissed
  - `approve` → **send path even if policy still failed** (explicit human override on parked drafts)
  - failed recheck / escalate signal → escalate + `escalate_for_human` tool log
  - else send
- **Send path:** record decision → write non-strippable `sent_log` (`would_send_to`) → optional Twilio WA delivery + `channel_deliveries` → CRM sync via `CRMPort` → lead `approved_logged` or `awaiting_ops_quote` for group quotes
- **CRM failure / WA failure:** run can become `partial` without undoing the human decision
- **Orchestration role:** Side effects are **after** durable human intent. Retries (`/api/retry-crm`, `/api/demo-ghl-sync`) call `sync_lead_to_crm` **without** re-invoking the graph or LLM — so the approved text cannot silently change.

#### 8) `reporting` — always-on terminal analytics hook
- **Runs on every outcome** (success, escalate, dismiss, error)
- **Does:** On escalate/dismiss, derive `missing_fact` (first violation, error kind, or “no self-serve answer”); `finish_agent_run`
- **Orchestration role:** Reporting is a **node in the graph**, not a nightly batch — same run that handled the guest also tags the business insight.

### O.8 Collaboration pattern (how “several agents work together”)

This is **not** agents chatting in a loop. It is **role specialization + shared state + routers**:

```
intake (classify) ──writes──► lead fields in state + DB
research ──writes──► _chunks
content ──reads chunks/links──► draft
policy ──reads draft──► policy_result + next_step
notify / approval ──human──► decision (+ maybe new draft)
ops ──reads decision/draft──► sent_log + CRM + channel
reporting ──reads outcomes──► missing_fact + run status
```

**JD mapping while you explain:**
- Research agent = `research` + `search_knowledge_base`
- Content agent = `content` (+ intake as the classification specialist)
- Reporting agent = `reporting` + `/api/reports` reads
- Internal ops agent = `ops` + notify + CRM + optional Twilio

Tool calling is explicit and logged (`tool_calls`): `search_knowledge_base`, `get_official_links`, `escalate_for_human`, plus `twilio_whatsapp_send` when enabled.

### O.9 End-to-end path cards (use these as spoken examples)

#### Path A — Flagship happy path (msg-01, often with one repair)
1. Webhook/fixture → 202  
2. intake → faq, pet_flag, camping bundle true, lead `new`  
3. research → 12 chunks  
4. content → draft  
5. policy → maybe fail **PR-10** → `repair`  
6. content (repair prompt includes PR-10) → new draft  
7. policy → pass → `approval`  
8. notify → Telegram; lead `awaiting_human`  
9. interrupt… operator Approve  
10. ops → `sent_log` + CRM → reporting → END `done`

#### Path B — Repair exhausted, human still decides
1. … content → policy fail → repair → content → policy fail again  
2. `next_step=approval` (not auto-escalate)  
3. Human sees failing draft + violations in UI  
4. Approve → ops **sends/logs anyway** (override) **or** Edit → recheck **or** Dismiss  

#### Path C — Human edit introduces a fake price
1. At interrupt, Edit inserts `AED 150`  
2. Router → policy with `is_recheck=True`  
3. PR-01 fires → `next_step=escalate`  
4. Router → **ops directly** (no second interrupt)  
5. Lead escalated; no `sent_log` send path  

#### Path D — Model timeout at intake
1. intake raises/handles `LLMTimeout` → lead `escalated`, `run_status=error`  
2. Router → reporting (skip research/content)  
3. Enquiry still visible; never silently lost  

#### Path E — CRM fails after approve
1. Normal approve path writes `sent_log` first  
2. CRM throws / fixture → `crm_sync` failed, run `partial`  
3. `/api/retry-crm` re-syncs **same** lead — graph/LLM not re-run  

#### Path F — WhatsApp sandbox loop
1. Twilio webhook → same graph as desk webhook  
2. Empty TwiML (no auto-reply) — human remains the send gate  
3. On Approve + `TWILIO_WHATSAPP_SEND`, ops delivers and audits `channel_deliveries`  

### O.10 Resume orchestration (HITL) — sequence

```
Operator UI                FastAPI /api/decide              LangGraph + Postgres
    |                            |                                |
    |  POST {thread_id,          |                                |
    |        action, edits?}     |                                |
    |--------------------------->|  get_state(thread_id)          |
    |                            |------------------------------->|
    |                            |  interrupted?                  |
    |                            |<-------------------------------|
    |                            |  if no → 409                   |
    |                            |  else invoke(Command(resume))  |
    |                            |------------------------------->|
    |                            |     approval returns decision  |
    |                            |     → policy (if edit) or ops  |
    |                            |     → reporting → END          |
    |  JSON result               |<-------------------------------|
    |<---------------------------|                                |
```

Durability claim: process can restart while paused; `PostgresSaver` still holds the interrupt for that `thread_id`.

### O.11 What is *not* orchestrated by the LLM (judgment)

Make this explicit in the pitch — it shows “when plain code is better”:

| Concern | Handled by |
|---|---|
| Node order / branching | LangGraph edges + Python routers |
| Safety (prices, availability, cites) | `app/policy.py` |
| Exactly one repair | `repair_attempted` flag |
| Human pause / resume | `interrupt` + checkpointer |
| Idempotent ingest | `external_ref` uniqueness |
| CRM retry without rewriting the reply | `sync_lead_to_crm` outside the graph |
| Timeline / tool logs | `_instrumented` + explicit tool logging |
| Auth / rejects | FastAPI before graph starts |

The LLM is **invoked by** orchestration; it does not **own** orchestration.

### O.12 90-second spoken explanation (practice this)

> “An enquiry hits a webhook. I persist the message and an agent_run, then kick LangGraph in a background task so HTTP stays fast. The graph starts at intake — that’s an LLM that classifies the message and, importantly, creates the lead immediately so we never lose demand if something fails later. Research loads the full public corpus as a tool call — no vector DB at this size. Content drafts a structured, cited reply — second and last LLM call. Policy is pure Python: eleven rules set a next_step. First failure repairs content once with the exact rule IDs; if it still fails, we park for a human rather than auto-dropping it. Notify pings Telegram once, then approval calls interrupt and checkpoints to Postgres. The operator Approve, Edit, or Dismiss. Edit goes back through the entire policy gate — if they typed a fake price, we escalate with no override. Ops writes the audit log and syncs CRM; reporting always runs and tags content gaps. That’s the orchestration: specialized agents, shared state, conditional routers, durable human-in-the-loop, and side effects only after a decision.”

### O.13 Whiteboard checklist (if they ask you to draw it)

- [ ] Ingress → 202 + background invoke  
- [ ] 8 nodes named  
- [ ] Mark which 2 use LLM  
- [ ] Show repair loop content ⇄ policy  
- [ ] Show notify **before** interrupt  
- [ ] Show edit → policy recheck → ops (skip second interrupt)  
- [ ] Show error shortcuts intake/content → reporting  
- [ ] Say Postgres checkpointer + thread_id  
- [ ] Say CRM retry does not re-enter the graph  

---

## 1. The 60-second pitch (memorize this)

> Weekend Enquiry Desk is a **working MVP**, not a prompt playground. A guest enquiry hits a webhook, a LangGraph of specialized nodes classifies it, loads the real public sunkssd.io corpus, drafts a **cited** reply, and runs that draft through **11 deterministic policy rules** that block invented prices, availability, and uncited policy claims. A human must **Approve / Edit / Dismiss** in a command centre before anything is logged — and even an edited draft is re-checked by the same gate. Supabase is the system of record; GoHighLevel is a real opt-in CRM sync. Observability is first-class: `run_steps`, `tool_calls`, `policy_violations`. Two business reports fall out of the same data: **content gaps** on the website and **demand signals** booking systems never see. **Nothing is ever silently sent** — the audit table is named `would_send_to` on purpose. Built end-to-end in Python (FastAPI + LangGraph + Postgres), deployed on Docker behind a reverse proxy, with **139 automated tests** plus live validation against real OpenRouter, Supabase, and a GHL sandbox.

**One-line brand fit:** I built the exact first-90-days MVP their JD describes — research, content, reporting, ops agents; GHL wiring; command centre with approvals and logs — using their brand as the sample domain, under public/synthetic/sandboxed constraints so nothing touches real guests.

---

## 2. JD → proof matrix (slide backbone)

Map every JD phrase to something you can **show** or **name a file for**.

### 2.1 “Build real products. Not just prompts.” / “A working product matters more than an elaborate demo.”

| Proof | Where |
|---|---|
| Full request → durable graph → human decision → CRM + audit path | `app/graph.py`, `app/main.py` |
| Deployable container, own Docker network, bind to localhost only | `Dockerfile`, `docker-compose.yml` |
| Real DB schema + RLS lockdown | `migrations/0001_schema.sql`, `0002_rls_lockdown.sql`, `0003_channel_deliveries.sql` |
| Live-validated against real OpenRouter model + Supabase + GHL | README “Live-validated” section (4 bugs only production caught) |
| Explicitly **not** a chatbot UI with fake “agent” labels | LangGraph `StateGraph` with conditional edges; only 2 nodes call an LLM |

**Say this:** “The product is the policy gate + durable human interrupt + recoverable CRM sync — not the model prompt.”

### 2.2 “Specialized, collaborating AI agents for research, content, reporting and internal operations”

Their JD names four domains. This system maps **literally**:

| JD domain | Node(s) | LLM? | Job |
|---|---|---|---|
| **Research** | `research` (+ tool `search_knowledge_base`) | No | Load full labelled corpus from `data/corpus/chunks.json`; timed tool-call log |
| **Content** | `content` (+ intake classification) | Yes | Structured draft `{sentences[{text,cites,non_factual}], urls}` with one repair pass |
| **Reporting** | `reporting` + `/api/reports` | No | Tag `missing_fact`; content-gap + demand-signal reports |
| **Internal operations** | `ops`, `notify_node`, CRM adapters, optional Twilio send | No (HTTP) | Decisions, `sent_log`, CRM sync, operator Telegram ping, channel delivery audit |

**Orchestration layer:** `intake` → `research` → `content` → `policy` ⇄ repair → `notify` → `approval` [interrupt] → `ops` → `reporting` → END  
**File:** `app/graph.py` (8 instrumented nodes, conditional routers)  
**Explain in depth:** §O (full routers, path cards, resume sequence, spoken 90s script)

**Talking point — “several agents working together”:**  
Don’t oversell “multi-agent debate.” Sell **specialized collaborating roles on a shared state**, with tool calling and durable orchestration — which is what production systems need. Two LLM specialists (`intake`, `content`); six deterministic specialists for research, policy, notify, approval, ops, reporting. That mix is the judgment they asked for.

### 2.3 “Tool calling and agent orchestration”

| Named tool (brief) | Implementation | Logged in |
|---|---|---|
| `search_knowledge_base` | `app/tools.py` — full corpus load | `tool_calls` |
| `get_official_links` | `app/tools.py` — whitelist from `data/official_links.json` (6 links) | `tool_calls` |
| `escalate_for_human` | `app/tools.py` — structured escalation record | `tool_calls` |

Orchestration: LangGraph conditional edges (`_route_after_intake`, `_route_after_content`, `_route_after_policy`, `_route_after_approval`). Per-node timeline via `_instrumented()` wrapper — every node auto-writes `run_steps` without duplicating logging inside each function.

### 2.4 “Simple command center… tasks, agent activity and progress… logs, error handling and human approvals”

| Capability | Endpoint / UI | Notes |
|---|---|---|
| Queue of leads / tasks | `GET /api/queue`, shell `GET /` | Stats: awaiting / partial / logged; age + stale (>15m) |
| Agent activity / timeline | `GET /api/runs/{run_id}/timeline` | Per-node steps, tool calls, policy violations |
| Draft visibility | `GET /api/drafts/{thread_id}` | Lazy-loaded from LangGraph checkpoint (not redundantly stored) |
| Human approvals | `POST /api/decide` | `approve` \| `edit` \| `dismiss`; edit re-enters policy |
| Fixture launcher | `POST /api/fixture` | 16 synthetic + 2 error fixtures one-click |
| Rerun / supersede | `POST /api/rerun` | Re-shoot a card; old run marked superseded |
| Reports | `GET /api/reports` | Content gap + demand signal |
| CRM retry | `POST /api/retry-crm` | Re-sync without re-running LLM/graph |
| Demo GHL push | `POST /api/demo-ghl-sync` | Force GHL without mutating `CRM_PROVIDER` |
| Auth | HTTP Basic on command centre + APIs | `DESK_BASIC_AUTH_*` |
| Design | Brand tokens from sunkssd.io | Console/dark operator UI, not a landing page |

**Human-in-the-loop primitive:** LangGraph `interrupt()` in `approval`; resume via `Command(resume=...)`. State survives process restart because production uses **PostgresSaver** (security-critical fix vs earlier InMemorySaver).

**Double-decide guard:** `/api/decide` refuses resume if snapshot has no pending interrupt → HTTP 409 (“already decided”).

### 2.5 “Connect business tools and CRM systems, potentially GoHighLevel”

| Integration | Status | Proof |
|---|---|---|
| **Supabase / Postgres** | System of record (default CRM provider is a no-op adapter) | `DATABASE_URL` session pooler :5432; `app/db.py` rejects :6543 |
| **GoHighLevel API v2** | Real opt-in | `app/crm/gohighlevel.py` — `/contacts/upsert`, optional `/opportunities/` for `group_quote` |
| **CRMPort interface** | Pluggable | `app/crm/base.py` — swap providers without rewriting ops |
| **Telegram** | Operator ping | `app/notify.py` — PII redacted (security fix) |
| **Twilio WhatsApp sandbox** | Optional conversation loop | inbound webhook + optional send on Approve; `channel_deliveries` audit |
| **OpenRouter LLM API** | Live | `app/llm.py` — model via `LLM_MODEL` env |
| **Webhooks** | Core | `POST /webhook` (secret + idempotent `external_ref`); Twilio signed webhook |

**Honesty line (must say):** GHL runs against **the builder’s** sandbox location. SUNKSSD’s use of GHL is **unconfirmed and not claimed**. Default `CRM_PROVIDER=supabase` so the demo never depends on a GHL trial.

### 2.6 “Familiarity with n8n, Make, LangGraph… and judgment about when plain code is better”

| Choice | Decision | Why (falsifiable) |
|---|---|---|
| Orchestration | **LangGraph + plain Python**, not n8n/Make | Conditional repair loops, durable interrupt, unit/integration tests, policy as code — flowchart tools fight this |
| Retrieval | **Full corpus in prompt**, not pgvector | ~12 FAQ chunks (~1.2–1.4k words public site); multi-intent flagship message; empty-retrieval impossible |
| Policy | **Deterministic Python rules**, not another LLM judge | Auditable rule IDs PR-01…PR-11; zero hallucination risk in the gate itself |
| CRM default | **Supabase adapter**, GHL opt-in | Demo durability > vendor lock for portfolio |
| Operator notify | **Telegram**, not WhatsApp business-initiated | Meta 24h window / template constraints for business-initiated pings |
| Portfolio strategy | This project = LangGraph/code; **other portfolio = n8n** | Avoids third identical n8n demo; shows range + judgment |

### 2.7 “APIs, databases, webhooks, full-stack development and deployment”

| Layer | Stack |
|---|---|
| API | FastAPI — JSON REST for UI; BackgroundTasks for graph (never blocks request on sync checkpointer) |
| DB | Postgres (Supabase) — 12+ tables, direct psycopg, no ORM; RLS deny-all on public schema |
| Webhooks | Desk secret + Twilio signature validation; `rejected_events` for bad traffic |
| Frontend | Server-rendered shell + JSON client (`templates/index.html`) — queue cards, inline edit, timeline |
| Deploy | Docker Compose on Oracle Cloud VM, `127.0.0.1:8001:8000`, reverse proxy TLS, isolated network from n8n stack |

### 2.8 “One or two real projects… exactly what you built” + walkthrough

**This project contribution framing (solo build — say so):**
- Product framing against the JD (public / synthetic / sandboxed constraints)
- LangGraph orchestration, policy engine, command centre, CRM adapters
- Security review + fixes; live Docker validation; Twilio sandbox path
- Tests (TDD), migrations, deployment, docs (`README`, `SYSTEM_EXPLAINER`)

**Walkthrough artifact options:**
1. **Written** — this brief §4–§9 + `SYSTEM_EXPLAINER.md` (or a 2–3 page excerpt)
2. **Video (recommended 3–5 min)** — follow §9 demo script with screen share

### 2.9 Soft JD signals: independence, speed, thoughtful engineering

| Signal | Evidence |
|---|---|
| Independence | End-to-end ownership: scrape → schema → agents → UI → deploy → security |
| Speed | Scoped as a short demo build; deferred work recorded in `TODOS.md` with triggers |
| Thoughtful engineering | Edit recheck; lead owned at intake (failed runs still visible); `partial` vs `error`; Jsonb/pool/checkpointer live fixes |
| Security maturity | Critical/High findings fixed; PII redaction; RLS; localhost bind; distinct 500 vs 401 for misconfigured secret |
| Honesty | Synthetic labels; no claim of SUNKSSD GHL; measured reports not vanity metrics |

---

## 3. Suggested pitch deck outline (10–12 slides)

Use this as the slide structure; pull bullets from §2 and numbers from §14.

### Slide 1 — Title
- **Weekend Enquiry Desk**
- Working MVP for SUNKSSD-style guest ops + AI-native agency workflow
- Your name · role · “Built against your JD as proof-of-work”
- Optional: live URL / GitHub

### Slide 2 — The problem (their world)
- Weekend brand + agency ops: guest FAQs across WhatsApp/email/IG; no FAQ page on sunkssd.io today
- Risk of AI inventing **prices / availability / policy**
- Booking tools (e.g. SevenRooms) only see completed bookings — miss “asked but never booked”
- Agency need: agents for research / content / reporting / ops with a human command centre

### Slide 3 — Product in one picture
- Architecture diagram from README (webhook → intake → research → content → policy → notify → **human interrupt** → ops → reporting)
- Caption: “Decision support, not an unsupervised autoresponder”

### Slide 4 — Multi-agent collaboration map / orchestration
- Four JD domains → eight graph nodes table (§2.2)
- Call out: only **two** LLM calls per happy path; everything else deterministic
- Put the §O.2 master diagram on this slide (or a dedicated follow-on slide)
- One callout box: routers read `next_step` / `decision` — the LLM does not choose the graph path

### Slide 5 — The actual product: policy gate
- PR-01…PR-11 table (currency, availability, citations, whitelist links, group quote, camping bundle, published weekends…)
- Structured draft JSON (why not free prose)
- **Edit → full recheck** (the line that makes “zero invented prices” true for humans too)

### Slide 6 — Human command centre
- Screenshots: queue cards, draft + citations, Approve/Edit/Dismiss, timeline with steps/tools/violations
- Telegram ping (redacted)
- Durable pause: Postgres checkpointer

### Slide 7 — Wiring real tools
- Webhooks (idempotent), Supabase, GHL upsert + opportunities, Telegram, Twilio WhatsApp sandbox, OpenRouter
- CRMPort pattern; demo GHL without changing default provider
- Deployment: Docker on OCI VM

### Slide 8 — Judgment calls
- Why LangGraph/code over n8n here
- Why no pgvector yet (threshold: ~500+ FAQ/past-reply pairs)
- Why Telegram for ops ping
- Why GHL is opt-in

### Slide 9 — Proof it’s real (not a demo fake)
- 139 tests collected; TDD; live Docker run stories (4 production-only bugs)
- Flagship message path: fail PR-10 → repair → pass → interrupt → approve → `sent_log` + `crm_sync`
- Security review: Critical/High fixed
- Three named error fixtures

### Slide 10 — Business value beyond the reply
- Content-gap report → ranked FAQ the marketing site should write
- Demand-signal report → pet/kid/intent demand SevenRooms can’t see
- `missing_fact` written by reporting node — no separate analytics pipeline

### Slide 11 — Scope honesty + next 30–90 days if hired
- Not built: follow-up agent for `awaiting_ops_quote` (called out deliberately)
- Deferred: LLM retry/backoff, double-approve hardening depth, rate limits, pin deps (`TODOS.md`)
- If hired: production channel windows, real FAQ growth → revisit retrieval, agency multi-tenant command centre, more CRM objects

### Slide 12 — Close / ask
- “I already shipped a version of your first 90-day MVP against your brand.”
- Links: repo, live desk, walkthrough video
- Availability / timezone / salary expectation placeholder
- Invitation to paid assessment / live demo

---

## 4. System story (written walkthrough — long form)

### 4.1 What it is
A guest-facing enquiry assistant for a weekend beach/music brand, implemented as a **portfolio MVP** for SUNKSSD’s agentic-systems role. It turns a message into:
1. a classified **lead**
2. a **cited draft reply** grounded in public website copy
3. a **policy verdict**
4. a **human decision**
5. an optional **CRM sync**
6. **observability** + two **ops/marketing reports**

It is **not** an unsupervised bot that messages guests. Default portfolio posture: log-only (`would_send_to`). Optional Twilio sandbox can deliver after human Approve when explicitly enabled.

### 4.2 Hard constraints (say these first — they pre-empt objections)

1. **Public** — corpus scraped only from `sunkssd.io` (never Instagram DMs, SevenRooms/IPMS247 widgets, WhatsApp archives). Every chunk has `url` + `accessed_at`.
2. **Synthetic** — 16 guest messages + 2 error fixtures labelled `SYNTHETIC`. No real guest traffic.
3. **Sandboxed** — GHL = builder’s account. Default CRM = Supabase adapter.

### 4.3 Happy path (flagship message)

**Message (`msg-01`):**  
“Hi, can I bring my dog this Saturday? How do I get tickets, and is camping included in the pass?”

Why this message is the demo hero:
- Multi-intent (pet + tickets + camping) — the reason vector top-k was rejected
- Triggers **PR-10** if the model answers camping with a bare yes/no
- Live run (documented): first draft failed PR-10 → one repair → second draft with both ticket links + “confirm with team” → human interrupt → approve → real `sent_log` + `crm_sync`

### 4.4 What happens technically (narrate while demoing)

1. **Ingress** — `POST /webhook` with `X-Desk-Secret`, or Twilio WhatsApp webhook, or command-centre fixture button. Idempotent on `external_ref`. Returns **202** and runs graph in `BackgroundTasks`.
2. **intake (LLM #1)** — Intent, date, party size, pet/kid flags, language, camping-bundle flag. **Creates the lead row as `new` here** so timeouts never erase demand.
3. **research** — Full corpus load via `search_knowledge_base` (no LLM).
4. **content (LLM #2)** — Structured cited draft; on repair, prompt includes exact failing rule IDs.
5. **policy** — 11 rules. Fail once → repair. Repair exhausted → still park at human approval (Approve can override). Failed **edit** recheck → escalate with **no** override. Pass → notify path.
6. **notify** — Telegram once (kept outside `approval` so resume doesn’t double-ping); lead → `awaiting_human`.
7. **approval** — `interrupt()`; operator Approve / Edit / Dismiss.
8. **ops** — Write decision; on approve path write `sent_log`; CRM sync; optional Twilio send + `channel_deliveries`.
9. **reporting** — Always runs; tags `missing_fact` on escalate/dismiss; finishes `agent_runs` status.

**Full orchestration (routers, path cards, resume sequence):** see **§O**.

---

## 5. Agents, tools, and shared state (interview depth)

### 5.1 Shared contract — `GraphState` (`app/state.py`)
Notable fields to be able to explain:
- `run_id` vs `thread_id` — different IDs for Postgres run log vs LangGraph checkpointer
- `draft` always structured JSON, never a plain string
- `repair_attempted` — exactly one repair loop (bounds cost/latency); after that, policy parks at approval rather than auto-escalating
- `is_recheck` — human edit path; failed recheck escalates with no override
- `next_step` — policy’s routing signal: `repair | approval | escalate | ops`
- `run_status`: `running | error | partial | done` — `partial` = approved but CRM/channel sync failed
- Deep walkthrough of how these flags drive routers: **§O.5–O.7**

### 5.2 Why structured drafts matter
PR-03 / PR-05 need “is this factual?” and “is this an affirmation?” — language understanding. Forcing the content model to self-annotate `cites` + `non_factual` lets the policy gate stay simple, auditable Python instead of brittle regex over prose.

### 5.3 Instrumentation (JD literally asks for this)
Tables the posting’s observability language maps to:
- `run_steps` — per-node timeline
- `tool_calls` — timed tool invocations
- `policy_violations` — which rules fired

Implementation detail worth bragging about: logging was missing until a live run exposed that nodes never called `repo.record_*`; fixed once via `_instrumented()` at graph assembly — not by editing eight nodes.

---

## 6. Command centre — what to show on camera

**Show in this order:**
1. Header: active `CRM_PROVIDER`, GHL credentials present?, WhatsApp sandbox flags
2. Fixture list → fire `msg-01`
3. Queue card appears (awaiting) with age
4. Expand timeline: intake → research → content → policy (maybe violation + repair) → notify → approval interrupt
5. Open draft: sentences + citations + official links
6. Telegram notification (if configured) with redacted preview
7. **Edit** a sentence to insert `AED 150` → save → show re-escalation (no override) — *or* describe with the named test if you don’t want to burn the run
8. Approve a clean draft → `sent_log` / would_send_to
9. Demo GoHighLevel push → open contact URL in GHL
10. Reports tab: content gap + demand signal
11. Fire `err-crm-fail` → `partial` → Retry CRM
12. (Optional) WhatsApp sandbox inbound → approve → phone receives reply when `TWILIO_WHATSAPP_SEND=true`

**UI design note for slides:** Reuses sunkssd.io design tokens (oklch palette, Space Grotesk / Inter, marquee motif) but as a **console**, not a marketing page — proves brand fluency without confusing “pretty demo” with “operator tool.”

---

## 7. Judgment narratives (practice out loud)

### 7.1 “When is plain code better than n8n?”
Use this system as the answer:
- Need durable HITL with restart-safe state
- Need unit-tested policy rules with stable IDs
- Need conditional repair with a hard one-retry bound
- Need observability tables that match product language
- Visual builders excel at linear SaaS glue; this is a **state machine with safety properties**

Then: “I still use n8n where it’s the right tool — my other portfolio projects cover that. Here the JD asked for judgment, so I chose LangGraph.”

### 7.2 “When would you add pgvector?”
When corpus/inbox history reaches ~**500+** FAQ or past-reply pairs and multi-intent retrieval quality becomes a measured problem. Until then, full context is simpler, cannot empty-retrieve, and cannot drop a clause of a multi-intent message.

### 7.3 “Why not let the LLM be the safety layer?”
Because invented prices/availability are reputationally catastrophic. Deterministic gates over structured drafts give **rule IDs, tests, and timeline rows**. The LLM drafts; the gate audits.

### 7.4 “Why create the lead at intake, not at the end?”
A model timeout after intake still leaves an escalated lead. Demand and the human queue must never depend on a successful draft. This closes the blind spot the product exists to fix.

---

## 8. Integrations & deployment cheat sheet

### 8.1 Environment map (from `.env.example`)
| Variable | Role |
|---|---|
| `DATABASE_URL` | Supabase session pooler **:5432** (required for PostgresSaver prepared statements) |
| `CRM_PROVIDER` | `supabase` (default) or `gohighlevel` |
| `GHL_PIT_TOKEN` / `GHL_LOCATION_ID` / `GHL_PIPELINE_ID` | GHL auth + opportunity pipeline |
| `LLM_MODEL` / `OPENROUTER_API_KEY` | Live model (verified: `nvidia/nemotron-3.5-super-vl:free`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Operator ping |
| `TWILIO_*` / `PUBLIC_BASE_URL` | WhatsApp sandbox loop |
| `DESK_WEBHOOK_SECRET` | Inbound desk webhook |
| `DESK_BASIC_AUTH_USER/PASS` | Command centre |
| `CORPUS_STALE_DAYS` | PR-09 threshold (default 45) |
| `DEMO_NOW` | Freeze “today” for reproducible date resolution |

### 8.2 API surface (for walkthrough appendix)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/webhook` | `X-Desk-Secret` | Ingest enquiry |
| POST | `/webhooks/twilio/whatsapp` | Twilio signature | WA inbound |
| GET | `/` | Basic | Command centre HTML |
| GET | `/api/queue` | Basic | Tasks + stats |
| GET | `/api/runs/{id}/timeline` | Basic | Agent activity |
| GET | `/api/drafts/{thread_id}` | Basic | Draft + interrupt flag |
| POST | `/api/decide` | Basic | Human approval |
| POST | `/api/fixture` | Basic | Launch synthetic/error fixtures |
| POST | `/api/rerun` | Basic | Re-orchestrate a card |
| POST | `/api/retry-crm` | Basic | CRM recover without LLM |
| POST | `/api/demo-ghl-sync` | Basic | Force GHL sync |
| GET | `/api/reports` | Basic | Content gap + demand |
| GET | `/debug/run` | Basic | Raw checkpoint dump |

### 8.3 Deploy one-liner
```bash
docker compose up -d --build
```
Binds `127.0.0.1:8001` → container `8000`; reverse proxy terminates TLS; network isolated from co-hosted n8n.

---

## 9. Demo script (3–5 minute video / live)

**Prep:** Docker up, basic auth ready, Telegram optional, GHL token present, fixtures loaded, browser zoom 110%.

| Time | Action | Say |
|---|---|---|
| 0:00–0:20 | Title card / repo | “Working MVP for your AI Automation role — research, content, reporting, ops agents, GHL, command centre.” |
| 0:20–0:40 | Constraints | “Public corpus only, synthetic guests, sandboxed GHL — nothing touches real customers.” |
| 0:40–1:10 | Architecture glance | “LangGraph state machine; two LLM nodes; eleven deterministic policy rules; durable human interrupt.” |
| 1:10–2:10 | Fire msg-01 | Walk queue → timeline → draft citations. Mention PR-10 / repair if it fires. |
| 2:10–2:50 | Approve path | Approve → would_send_to → CRM status. Optional GHL open-in-app link. |
| 2:50–3:20 | Error path | err-crm-fail → partial → Retry CRM without regenerating draft. |
| 3:20–3:50 | Reports | Content gap + demand signal — “marketing FAQ + demand SevenRooms can’t see.” |
| 3:50–4:20 | Judgment | “Why code over n8n here; why no pgvector yet; tests + live validation.” |
| 4:20–4:45 | Close | “This is the shape of your first 90 days. Happy to do the paid assessment next.” |

**B-roll / cutaways:** policy_rules.yaml, graph.py routers, gohighlevel.py upsert, migration RLS, pytest collect count.

---

## 10. Error handling & reliability (they asked for this)

### Three named fixtures

| Fixture | Trigger | Outcome |
|---|---|---|
| Bad webhook | Wrong/missing secret or malformed body | 401/422; `rejected_events`; **no run** |
| Model timeout | `FIXTURE_MODEL_TIMEOUT` sentinel | Run `error`; lead still `escalated` |
| CRM fail | `FIXTURE_CRM_FAIL` sentinel | Draft approved; CRM fails; run `partial`; `/api/retry-crm` |

**Asymmetry teaching point:** Fail before trust → reject cleanly. Fail after intake → keep the lead. Fail after approval → never regenerate the human-approved message.

### Live bugs fixed (credibility stories)
1. psycopg3 needs `Jsonb(...)` for dict → jsonb  
2. `PostgresSaver.from_conn_string()` is a context manager  
3. Shared `Connection` unsafe across FastAPI threadpool → `ConnectionPool`  
4. GHL upsert 400 without email/phone → labelled synthetic fallback email  

### Security findings fixed
- Critical: production InMemorySaver → PostgresSaver  
- Critical (FP verified): Jinja2 autoescape made explicit  
- High: Docker bind all interfaces → `127.0.0.1`  
- High: raw PII in Telegram → redact + 40-char preview  
- Warn: misconfigured secret now distinct 500; dead service-role env vars removed/documented  

---

## 11. Interview Q&A bank

**Q: Is this multi-agent or a pipeline?**  
A: Specialized collaborating roles on shared LangGraph state. Two generative agents, six operational/deterministic agents. Production orchestration > chatty agent swarms.

**Q: What would you build in the first 30 days if hired?**  
A: Productionize channel ingress for their real CRM/comms stack; harden idempotency/rate limits from `TODOS.md`; grow corpus from real FAQ gaps the report already ranks; wire agency-internal ops workflows with the same command-centre pattern.

**Q: How do you prevent the model inventing prices?**  
A: PR-01 blocks currency patterns; content never gets live price data (none in public corpus); edits re-enter the full gate; adversarial fixture `msg-16` prompt-injection must still escalate.

**Q: Why FastAPI BackgroundTasks?**  
A: Graph uses sync psycopg checkpointer + two LLM calls + interrupt; inline would block the event loop and the HTTP request.

**Q: What’s your contribution vs libraries?**  
A: Product constraints, policy engine, graph routing, HITL UX, CRM port + GHL, observability schema, security fixes, deployment, fixtures/tests — LangGraph/FastAPI are scaffolding, not the product.

**Q: n8n vs this?**  
A: I use both. This problem needed tested state-machine semantics and durable interrupts; n8n is excellent for linear SaaS glue and I demonstrate that elsewhere.

**Q: How do you know it works?**  
A: 139 collected automated tests; integration tests including edit-recheck escalation; live Docker run against real model/DB/GHL with documented path for msg-01.

**Q: What didn’t you build?**  
A: Follow-up agent for `awaiting_ops_quote`; production LLM retry/backoff; some idempotency/rate-limit hardening — all listed with triggers in `TODOS.md`.

**Q: Salary / availability?**  
*(Fill your real numbers.)* Reference posted band USD 2k–3k/mo; state timezone and start date clearly.

---

## 12. Honest limits (say them; they signal maturity)

- Not production guest traffic; synthetic + optional personal WhatsApp sandbox  
- GHL is builder sandbox, not a claim about SUNKSSD’s stack  
- Corpus is small by design (12 chunks) — retrieval choice is temporary and thresholded  
- Default path does not message guests; Twilio send is opt-in  
- Windows local native DLL policy forces mocked unit DB; Linux Docker is the live path  
- Remaining deferred items in `TODOS.md` (rate limit, pin deps, dismiss coverage depth, etc.)

---

## 13. Application packet checklist

- [ ] Email subject: `AI Engineer Application — [Your Name]`  
- [ ] CV / resume  
- [ ] GitHub link (this repo + any n8n/other project)  
- [ ] Link to 1–2 shipped projects with **your contribution** spelled out  
- [ ] Short video **or** written walkthrough (use §9 / this brief)  
- [ ] Salary expectations  
- [ ] Availability + timezone  
- [ ] Optional live demo URL + basic-auth instructions (share privately)  
- [ ] Pitch deck PDF (slides from §3)

### One-paragraph project blurb (paste into email)

> **Weekend Enquiry Desk** is a working Python MVP I built against SUNKSSD’s AI Automation & Agentic Systems Engineer brief. It orchestrates specialized LangGraph agents for research, content, policy, human approval, CRM ops, and reporting: a guest webhook becomes a cited draft grounded in public sunkssd.io content, gated by eleven deterministic policy rules (no invented prices/availability), then paused for human Approve/Edit/Dismiss in a command centre with full run timelines, tool-call logs, and policy-violation history. Supabase/Postgres is the system of record; GoHighLevel is a real opt-in CRM sync; Telegram notifies operators; an optional Twilio WhatsApp sandbox closes the conversation loop after approval. Deployed with Docker, covered by 139 automated tests, and live-validated against real OpenRouter, Supabase, and a GHL sandbox. I deliberately chose LangGraph/plain code over n8n here — and full-corpus prompting over pgvector — as product judgment, not defaults.

---

## 14. Metrics & inventory (numbers for slides)

| Item | Count / value |
|---|---|
| Graph nodes | 8 (intake, research, content, policy, notify, approval, ops, reporting) |
| LLM calls on happy path | 2 (`intake`, `content`) |
| Named tools | 3 |
| Policy rules | 11 (PR-01…PR-11) |
| Synthetic guest fixtures | 16 |
| Error fixtures | 2 (+ bad-webhook path without creating a run) |
| Corpus chunks | 12 (trimmed from 24; bios/history removed) |
| Official whitelisted links | 6 |
| Pytest collected | **139** (1 deselected by default marker; live tests opt-in) |
| DB tables (core) | messages, leads, agent_runs, run_steps, tool_calls, policy_violations, decisions, sent_log, crm_sync, rejected_events, chunks, official_links, channel_deliveries, … |
| Migrations | 3 |
| Human actions | approve / edit / dismiss |
| Run statuses | running / error / partial / done |
| Default CRM | supabase |
| Opt-in CRM | gohighlevel (API v2) |
| Verified free model | `nvidia/nemotron-3.5-super-vl:free` (5–20s; lightning variant rejected after 100s+ whitespace) |
| LLM timeout | 45s (CRM/Telegram HTTP stay 10s) |
| Corpus stale note | 45 days (PR-09, non-blocking) |
| Queue “stale” UI flag | >15 minutes |
| Deploy host pattern | Oracle Cloud VM + Docker + reverse proxy |
| Host port | 127.0.0.1:8001 |

### Fixture mix (story for “coverage thinking”)
| Category | Examples | Intent |
|---|---|---|
| Answerable FAQ | dog+tickets+camping, kids, hours, weekends, location | Self-serve with cites |
| Link-only | tickets, camping vs room | Official links whitelist |
| Escalate | group of 20, alcohol, re-entry, rain, partnership, Hinglish price+refund, day-pass price, prompt injection | Human / policy |
| Errors | model timeout, CRM fail | Partial/error semantics |

---

## 15. Tech stack (skills checklist for CV / slide)

**Languages / runtime:** Python 3.12  
**API / web:** FastAPI, Jinja2, JSON REST command centre  
**Agents:** LangGraph `StateGraph`, `interrupt` / `Command(resume)`, Postgres checkpointer (`langgraph-checkpoint-postgres`)  
**LLM:** OpenRouter HTTP API (`app/llm.py`), structured JSON outputs  
**Data:** Postgres/Supabase, psycopg + ConnectionPool, raw SQL repo layer, RLS  
**CRM:** GoHighLevel API v2 (httpx), pluggable `CRMPort`  
**Comms:** Telegram Bot API, Twilio WhatsApp sandbox  
**Scraping / corpus:** BeautifulSoup script (`scripts/scrape_corpus.py`), provenance fields  
**Quality:** pytest (unit + integration + live marker), TDD  
**Security:** HMAC webhook secret, Twilio signatures, HTTP Basic, PII redaction, Jinja autoescape, localhost bind  
**Ops:** Docker, Docker Compose, reverse proxy, Oracle Cloud VM  
**Config:** env-based providers, YAML policy metadata (`config/policy_rules.yaml`)

---

## 16. Slide-ready soundbites (steal these lines)

1. “Build real products” → “There is no code path that messages a guest by accident — the table is called `would_send_to`.”  
2. “Not just prompts” → “Eleven deterministic rules with IDs on the timeline; the model drafts, the gate decides.”  
3. “Several agents” → “Research, content, reporting, and ops are separate nodes with a shared state and tool logs — not one mega-prompt.”  
4. “Human approvals” → “LangGraph interrupt checkpointed to Postgres; edits re-run the whole policy gate.”  
5. “Possibly GoHighLevel” → “Real upsert + opportunities, behind a port; Supabase default so the product isn’t hostage to a trial.”  
6. “When plain code is better” → “I reached for LangGraph and tests here; I use n8n elsewhere. That’s the judgment you asked for.”  
7. “Working MVP” → “Docker-deployed, 139 tests, live-validated against real model, DB, and CRM.”  
8. “Agency value” → “Same instrumentation that makes agents auditable also ranks the FAQ your site doesn’t have yet.”

---

## 17. Mapping to SUNKSSD as company (context, not claims)

From public JD / company blurbs (for tone, not for fabricating internal facts):
- Brand community: music, wellness, pets, shared passions; weekends / Lumi Beach positioning  
- Team spanning media, creative, social, strategy — hiring an engineer to **connect** marketing, creative, websites, CRM, and ops  
- Small team (posted ~1–10); founder Julian Trad listed on Wellfound  
- Remote-only role; apply `info@sunkssd.io`

**Do not claim:** that SUNKSSD currently uses GoHighLevel, WhatsApp Cloud, or SevenRooms as verified stack facts beyond what the JD says (“potentially GoHighLevel”) and what the public site exposes.

---

## 18. Next actions for you (builder)

1. Draft slides from §3 using screenshots from a live queue run of `msg-01`, GHL contact, and reports.  
2. Record the §9 walkthrough (or polish a written walkthrough from §4 + §6).  
3. Fill salary / availability / timezone into §13.  
4. Link second shipped project (ideally n8n) to prove tool-range + judgment.  
5. Rehearse §7 and §11 aloud once — especially policy-edit recheck and “why no n8n here.”  
6. Keep `SYSTEM_EXPLAINER.md` open during deep technical interviews as backup memory.

---

*Generated as a pitch-deck preparation brief from repository evidence. Prefer `README.md` / code if this brief and the running system ever diverge.*
