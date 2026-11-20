# Weekend Enquiry Desk — Deep System Explainer

This document is written to be fed into a teaching LLM so a human (the builder) can be walked
through the system deeply enough to **present it live to SUNKSSD** (the beach-club brand this was
built as a portfolio piece for) and answer follow-up questions confidently. It is exhaustive on
purpose: every architectural decision below is traceable to a specific file and a specific reason,
not a guess.

If you (the teaching LLM) are asked to prep the human for a presentation, use this document as
ground truth, then help them rehearse: ask them to explain each section back in their own words,
and flag where their explanation drifts from what's written here.

---

## 1. What this system is, in one paragraph

A guest sends a message ("can I bring my dog, how do I get tickets, is camping included?") through
a channel (WhatsApp/email/Instagram, simulated via webhook for the demo). An AI agent — a LangGraph
state machine, not a chatbot loop — classifies the message, retrieves the brand's real public FAQ
content, drafts a reply with inline citations, and then runs that draft through an **11-rule
deterministic policy gate** that blocks anything resembling an invented price, a false availability
claim, an uncited factual claim, or a policy claim the website doesn't actually support. If the
draft passes, a human operator is notified (Telegram) and must explicitly Approve, Edit, or Dismiss
it before anything is logged as "sent" — and even an edited draft is re-checked by the same policy
gate before it can be approved. **Nothing is ever actually sent to a real guest** — this is a
decision-support and lead-capture tool, not an autoresponder, and the send-log table is deliberately
named `would_send_to`.

The system also quietly produces two pieces of business value SUNKSSD doesn't have today: a
**content-gap report** (what questions the public website fails to answer, ranked by frequency) and
a **demand-signal report** (people who asked but never booked — demand the booking platform,
SevenRooms, cannot see because it only sees completed bookings).

## 2. Why this exists — the brief behind it

This was built against a real job posting (SUNKSSD's "AI Automation & Agentic Systems Engineer"
role) as a portfolio proof-of-work. The posting asked for judgment about orchestration tooling
("n8n, Make, LangGraph or similar — **and judgment about when plain code is better**") and named
specific observability concepts (run timeline, tool calls, policy violations) that this system
implements literally, not metaphorically — there are real `run_steps`, `tool_calls`, and
`policy_violations` database tables.

Three self-imposed constraints shape everything else, and they are the first thing to say out loud
in a presentation because they pre-empt the obvious objections:

1. **Public** — the knowledge base is scraped only from `sunkssd.io`. Never Instagram DMs, never the
   SevenRooms/IPMS247 booking widgets, never WhatsApp content. Every chunk of knowledge carries the
   URL it came from and an `accessed_at` timestamp, so provenance is always inspectable.
2. **Synthetic** — all 16 demo guest messages and both error-fixture messages are synthetic and
   labelled `SYNTHETIC` in their data. None are real guest traffic; nothing here was trained or
   tested on SUNKSSD's actual customers.
3. **Sandboxed** — the GoHighLevel CRM integration (see §9) runs against the builder's own GHL
   account. SUNKSSD's actual use of GoHighLevel is **unconfirmed and not claimed anywhere**. The
   default CRM provider is Supabase itself, specifically so the whole demo keeps working forever
   even if the GHL trial expires — GHL is an opt-in extra, never load-bearing.

## 3. Two judgment calls worth defending explicitly

A reviewer (or an interviewer) will probe these two choices. Both have a specific, falsifiable
answer, not just "it seemed simpler":

### Why no vector database (pgvector)?

sunkssd.io's real public copy is roughly 1,200–1,400 words — 12 chunks after the corpus was trimmed
(originally 24; 8 team-bio/brand-history chunks were cut because they roughly doubled every prompt
for zero guest-FAQ value). At that size, **stuffing the entire corpus into the prompt beats top-k
similarity search** for three concrete reasons:
- It's simpler — one JSON file, no embeddings pipeline, no index to keep in sync.
- It cannot produce an "empty retrieval" failure mode at all, which is precisely why that isn't one
  of the system's three named error fixtures — the failure mode doesn't exist.
- It can't silently drop a clause of a multi-intent message. The flagship demo message asks about
  dogs, tickets, *and* camping bundling in one sentence — a similarity search tuned to match one of
  those three intents could easily miss another. Full-context retrieval cannot drop a clause.

The honest threshold stated for when pgvector *would* become the right call: roughly 500+ FAQ or
past-reply pairs — i.e., once there's enough real inbox history to make retrieval meaningfully
selective, not before. This is a scaling judgment, not a rejection of vector search as a concept.

### Why no n8n / Make / workflow-builder?

The orchestration is a LangGraph `StateGraph` with conditional edges and a policy gate — real code,
with real unit and integration tests (100 tests total), not a drag-and-drop flowchart. The posting's
own phrasing ("judgment about when plain code is better") is read as a direct invitation to make
this call rather than default to a visual tool. There's also a portfolio-specific reason: the
builder's other portfolio projects already demonstrate n8n, so building a third n8n project here
would be the *opposite* of the judgment the posting is asking for.

## 4. Architecture at a glance

```
POST /webhook (X-Desk-Secret header, idempotent on external_ref)
        |
        v   [FastAPI BackgroundTasks — NEVER inline in the request handler;
        |    two LLM calls plus a LangGraph interrupt() would otherwise block
        |    the request, and the Postgres checkpointer driving all this is
        |    synchronous psycopg, which would block the async event loop too]
        v
   intake (LLM call #1) --writes a `leads` row as 'new'--> research (no LLM; loads the full corpus)
        |
        v
   content (LLM call #2 — emits {sentence, cites[]} pairs, not a plain string)
        |
        v
   policy (11 deterministic rules, zero LLM calls)
        |   \--fails, 1st time-------> content   (one repair attempt, told exactly which rules failed)
        |   \--fails, 2nd time
        |    or fails a recheck-----> ops         (escalate; no override possible)
        |   passes
        v
   notify (Telegram ping to the operator — runs exactly ONCE, before the pause,
        |  never inside the paused node itself)
        v
   approval  [LangGraph interrupt() — the graph is durably paused here]
        |   --resume with Command(resume={"action": "approve"|"edit"|"dismiss"})--
        |
        |   action == "edit"  --> back to policy (the edited draft is re-checked
        |                          from scratch — this is the one fix that makes
        |                          "zero invented prices" true for edits too)
        v
   ops (CRM sync only — the lead already exists; Supabase is the system of
        |  record, GoHighLevel is an opt-in sync target, never load-bearing)
        v
   reporting (runs on EVERY outcome, success or failure — tags `missing_fact`
        |      on every escalated/dismissed lead; this tag is the entire input
        |      to the content-gap report, there is no separate aggregation job)
        v
       END
```

Two things about this diagram that aren't obvious from a glance:

- **`research` never calls an LLM.** It is pure retrieval of a static JSON file. Only `intake` and
  `content` touch the model — everything else in the graph, including every policy check, is
  deterministic Python with no model call and therefore no hallucination risk of its own.
- **The graph has exactly one place a human can intervene**, and it's a real pause, not a webhook
  poll: `approval` calls LangGraph's `interrupt()`, which suspends execution and persists the entire
  state to Postgres via the checkpointer. The process can restart, the operator can take hours, and
  `Command(resume=...)` on the same `thread_id` picks the graph back up exactly where it left off.

## 5. The shared contract: `GraphState`

Every node reads a subset of one `TypedDict` (`app/state.py`) and returns a partial update; LangGraph
merges updates into the running state. This is deliberately *not* a class with methods — it's the
simplest possible shared contract between 8 independently unit-testable node functions. Notable
fields and what they're actually for:

- `run_id` / `thread_id` — `run_id` is the Postgres row this run is logged under; `thread_id` is the
  LangGraph checkpointer's key for durable pause/resume. They are *different ids for different
  systems* on purpose.
- `_chunks` — prefixed with an underscore to mark it as `research`'s internal output, consumed by
  `content` and `policy_node`, not surfaced as a "real" field of the business state.
- `draft` — always `{"sentences": [...], "urls": [...]}`, never a plain string. This structure is
  the single most important design decision in the `content`/`policy` boundary (see §6).
- `repair_attempted` — a one-shot flag. The system allows exactly one repair loop back through
  `content`; a second policy failure always escalates. This bounds the LLM-cost and latency of a bad
  draft deterministically — there is no retry loop that could spin.
- `is_recheck` — set by `approval` when a human edits a draft. It changes `policy_node`'s routing:
  a recheck that passes goes straight to `ops` (not back through a second human approval — the human
  already approved intent), but a recheck that *fails* escalates with **no override available**,
  identical to what would happen to a bad model draft. An operator cannot type a fake price into the
  edit box and have it slip through.
- `run_status` — one of `running | error | partial | done`. `partial` specifically means "the draft
  was approved and logged, but the CRM sync afterward failed" — a distinct, recoverable state from
  `error` (which means the run itself broke, e.g. an LLM timeout).

## 6. The policy gate — this is the actual product

Eleven rules live in `app/policy.py` as small pure functions, each with a stable id (`PR-01`…`PR-11`),
each evaluated on *every* draft, each visible on the run timeline whether it fires or not (so an
operator can see "10 rules passed silently, 1 fired" — the absence of a violation is itself logged,
not just the presence of one). `config/policy_rules.yaml` holds the human-readable metadata (name,
severity, description) the UI and README reference; the actual boolean logic lives only in
`policy.py`.

| Rule | Severity | What it blocks | Why it matters |
|---|---|---|---|
| PR-01 | critical | Currency figures (`AED 150`, `$50`, `150 dirhams`) | Pricing changes; a hallucinated price is the single most reputationally expensive mistake an autoresponder can make. |
| PR-02 | critical | Availability claims ("we have space", "sold out") | The corpus has no live inventory data at all — any such claim is pure invention. |
| PR-03 | high | An affirmative yes/no with no citation | Forces every "yes" to be traceable to a real sentence of the website. |
| PR-04 | high | A citation pointing at a chunk id that doesn't exist | Catches the model inventing a plausible-looking but fake citation. |
| PR-05 | high | Any factual sentence with no citation at all | The blanket rule beneath PR-03/PR-06 — everything factual must cite something. |
| PR-06 | critical | Policy claims (refund, cancel, re-entry, age limit, alcohol) without a citation | These are exactly the claims a guest might later hold the brand to; they get the highest bar. |
| PR-07 | high | Any URL not on the official-links whitelist | Blocks the model from inventing or mis-typing a link — it can only ever hand out URLs a human pre-approved. |
| PR-08 | critical | A `group_quote` intent answered with numbers instead of escalating | Group pricing is never in the public corpus; this intent always goes to a human, unconditionally. |
| PR-09 | low (non-blocking) | A stale corpus (default 45-day threshold) | A *note*, not a violation — it doesn't block the draft, it just flags that the underlying scrape may be out of date. |
| PR-10 | high | The camping/day-pass bundling question answered with a bare yes/no | The corpus genuinely has two *separate* tickets with no bundling statement; a bare answer would be a guess either way, so the rule forces both links plus a "please confirm with our team" line. |
| PR-11 | medium | A resolved event date outside the published weekends | Catches the model resolving "this Saturday" to a date the brand isn't even running that season. |

Two design choices inside this gate are worth explaining carefully, because they show the difference
between "looks AI-generated" and "was actually engineered":

**Why the draft is structured JSON (`{"sentences": [{"text", "cites", "non_factual"}], "urls"}`)
and not a plain string.** Classifying "is this sentence a factual claim or a pleasantry" and "is
this an affirmation or a deflection" is a language-understanding task, not a pattern-matching task —
trying to regex that out of free prose is exactly the kind of brittle approach that breaks on the
first paraphrase. Instead, the `content` model is asked to self-annotate its own sentences with
citations and a `non_factual` flag as it writes them, and PR-03/PR-05 validate that structure
directly. The model does the understanding; the policy gate does the (much simpler, fully auditable)
structural check.

**Why an edited draft re-runs the *entire* policy gate, not just a diff check.** This was an
eng-review fix (traceable in `app/nodes/approval.py` and `app/graph.py`'s conditional edges): without
it, an operator who edited in a fake price or a fabricated availability claim would have that edit
logged as sent with zero validation — the policy gate would have done its job on the model's draft
and then be completely bypassed by the human's own edit. The fix routes `edit` back through
`policy_node` with `is_recheck=True`; a failing recheck escalates with no override, exactly like a
bad model draft would. This is the single fix the README calls out as "the one that makes 'zero
invented prices/availability' true for edited drafts, not just unedited ones," and it has a named
regression test: `tests/test_graph_integration.py::test_edit_that_fails_recheck_escalates_with_no_override`.

## 7. Walking through a node, in detail

### `intake` (LLM call #1)
Classifies the raw guest message into `{intent, event_date, party_size, pet_flag, kid_flag,
language, mentions_camping_bundle_question}` via a single structured-output LLM call, resolving
relative dates ("this Saturday") against a `today` that can be overridden by `DEMO_NOW` so the
fixed demo stays reproducible indefinitely. **This node, uniquely, owns the creation of the `leads`
row** — not `ops` — specifically so that a run which later times out or errors still leaves behind a
lead record (status `escalated`) that the human queue and the demand-signal report can see. If the
enquiry had no lead row until the very end of a successful run, every failed run would vanish from
the business's view of demand — exactly the blind spot this tool exists to close.

### `research` (no LLM)
Pure, deterministic: loads `data/corpus/chunks.json` in full (see §3's pgvector discussion) and times
itself for the tool-call log. Nothing here can fail in an interesting way — there's no retrieval
step that can return zero results, because there's no retrieval at all, just a full load.

### `content` (LLM call #2)
The only other model call in the system. Given the guest message, the full corpus (prefixed with
`[section_id]` tags the model must cite by), and the official links list, it drafts the structured
`{sentences, urls}` shape described in §6. On a repair pass (`repair_attempted` already set), the
prompt is extended with the exact prior violations ("Your previous draft failed these policy checks
— fix them: PR-10: ...") so the single retry is targeted, not a blind re-roll.

### `policy_node` (no LLM)
Wraps `app/policy.py`'s pure `evaluate()` with real context built from the corpus, the official
links, and the published-weekends data. Routes to `content` (repair), `approval` (via `notify`), or
`ops` (escalate) depending on pass/fail and whether this is a first attempt, a repair, or a
human-edit recheck (§6 covers the recheck logic in detail).

### `notify_node` (no LLM, external call)
Sends a Telegram message to the operator: `"Draft waiting — {intent} — lead {id} — {redacted
preview}"`. This is a separate node *specifically* because the node containing `interrupt()`
re-executes from the top on every resume — if the Telegram ping lived inside `approval()` itself, it
would fire again every time the graph resumes, not just once when the draft first became ready. This
is a non-obvious LangGraph gotcha worth stating explicitly in a presentation, because it is exactly
the kind of bug that doesn't show up in tests unless you specifically test resume behavior.

The ping redacts guest PII — this was a **security-review finding**: the operator ping used to
include the guest's raw message verbatim (names, phone numbers, emails) in a Telegram chat with no
redaction and no retention control. `app/notify.py` now regexes out emails and phone numbers and
caps the preview to 40 characters before sending.

### `approval` (the human-in-the-loop pause)
Calls LangGraph's `interrupt()`, which suspends the graph and persists its entire state via the
checkpointer. Resumes via `Command(resume={"action": "approve"|"edit"|"dismiss", ...})` on the same
`thread_id`. An edit replaces the draft body in state and sets `is_recheck=True`, routing back
through `policy_node` rather than straight to `ops` (§6).

### `ops` (CRM sync only — not lead ownership)
By this point the lead already exists (written at `intake`) and has moved through intermediate
status values. `ops`'s only job is: record the human's decision (`decisions` table), and for an
`approve`/edit-that-passed outcome, write the **non-strippable** `sent_log` row and attempt a CRM
sync. "Non-strippable" matters: `sent_log` is the audit trail proving exactly what would have been
sent and under what citations — it is never deleted or mutated after the fact. No code path in this
repository can actually message a real guest; this is why that table is named `would_send_to` rather
than `sent_to`.

CRM sync is delegated to a small `CRMPort` interface (`app/crm/base.py`) with two implementations —
see §9 — and failures are caught and recorded as a `crm_sync` row with `status='failed'` rather than
raising, which is what produces the `partial` run status and lets `/api/retry-crm` re-attempt later
without re-running the graph, the LLM, or regenerating the draft the human already approved (this
matters: a naive retry that restarted from `intake` could silently send a *different* message than
the one actually approved — an eng-review-caught risk).

### `reporting` (runs on every outcome, success or failure)
Tags every escalated or dismissed lead with `missing_fact` — either the first policy rule that
fired, the error kind if the run errored, or (for intents with no self-serve answer at all, like
`group_quote`/`partnership`/`other`) a generic "no self-serve answer in corpus" tag. This single
write is the entire input to the content-gap report (§11) — there is no separate batch job computing
it after the fact.

## 8. The two LLM calls, concretely

`app/llm.py` is the single call site for both `intake` and `content` — every other node is
deterministic. It calls OpenRouter (model configurable via `LLM_MODEL`, no code change needed to
swap models) with a 45-second timeout, chosen **after live-testing against a real free model** — a
10-second timeout, reasonable for a typical API call, was too aggressive for `content`'s larger
structured-drafting prompt on a free/shared model. This 45s figure is specifically scoped to LLM
generation calls; the CRM and Telegram HTTP calls elsewhere correctly stay at a tighter 10s, because
those are "is this simple API call hanging" concerns, not "is a large model finishing a completion"
concerns — conflating the two would have made the CRM/Telegram calls needlessly slow to fail.

Model choice was also live-tested, not assumed: `nvidia/nemotron-3.5-lightning:free` handled
`intake`'s simple classification fine but degenerated into 100+ seconds of whitespace-token
generation on `content`'s larger prompt — reproduced twice, not a fluke. The system switched to
`nvidia/nemotron-3.5-super-vl:free`, which returns clean JSON with no markdown fences in 5–20
seconds and — notably — correctly refused to act on a prompt-injection test message during live
testing.

`FIXTURE_MODEL_TIMEOUT` is a sentinel string: if it appears anywhere in the message being sent to
the model, `call_llm` raises `LLMTimeout` deterministically, without ever making a network call. This
means the exact same exception type and the exact same downstream handling code path that a *real*
production timeout would trigger is exercised by a fixture — the error-handling code is proven to
work, not merely asserted to exist.

## 9. The CRM integration — and why it's explicitly not load-bearing

`app/crm/base.py` defines a tiny `CRMPort` protocol: `create_or_update_lead(lead_id, intent,
party_size, event_date, contact_ref) -> external_id`, raises on failure. Two implementations:

- **`SupabaseCRM`** (the default, `CRM_PROVIDER=supabase`) — a no-op that just returns the lead_id.
  Supabase/Postgres is already the system of record for leads (§10); this implementation exists only
  to satisfy the interface uniformly. It has no external dependency and cannot fail, which is
  exactly why it's the safe default — the demo keeps working indefinitely, including after a GHL
  trial expires.
- **`GoHighLevelCRM`** (opt-in, `CRM_PROVIDER=gohighlevel`) — real API v2 calls against
  `/contacts/upsert` (dedup on email/phone) and, for `group_quote` leads with a configured pipeline,
  `/opportunities/`. Both calls time out at 10 seconds since they sit in the live request path.

A real, live-traffic bug surfaced and fixed here is a good concrete story for a presentation: GHL's
`/contacts/upsert` 400s if given no email or phone to dedup on — true of **every one of the 16
synthetic fixtures**, since none carry a real guest contact identifier. The fix is a labelled
synthetic fallback email (`synthetic-{lead_id}@weekend-enquiry-desk.invalid`, tagged
`synthetic-contact`) used only when the channel genuinely carries no identifier; a real `contact_ref`
(an actual WhatsApp number or email) always wins when the channel provides one. This is the kind of
bug that is invisible to any mocked unit test and only showed up once the system was run against a
real external API with real synthetic data — which is exactly why the README distinguishes
"unit-tested" from "live-validated" as two different claims (§13).

## 10. Data model

Twelve tables (`migrations/0001_schema.sql`), no ORM, direct psycopg SQL in `app/repo.py` — every
function there is a thin, independently mockable seam so node unit tests monkeypatch `repo.*`
rather than hitting a real database.

- **`messages`** — the raw inbound webhook payload, unique on `external_ref` (this is what makes the
  webhook idempotent — resubmitting the same `external_ref` is rejected, not re-processed).
- **`leads`** — the business record: intent, resolved date/party/pet/kid flags, `status`, and
  `missing_fact` (written only by `reporting`). Owned by `intake`, not `ops` (§7).
- **`agent_runs`** — one row per graph execution, keyed by `thread_id` (the LangGraph checkpointer's
  key) as well as its own `id`. Final `status` is one of `running|error|partial|done`.
- **`run_steps`** — one row per node execution, with a JSON snapshot of the state going in and the
  update coming out. This is the "run timeline" visible in the command centre, and it's populated
  automatically by a wrapper (`_instrumented` in `app/graph.py`) around every node — no node had to
  be individually modified to log itself.
- **`tool_calls`** — timed, logged calls to the three named tools (`search_knowledge_base`,
  `get_official_links`, `escalate_for_human`) — the tools themselves (`app/tools.py`) stay pure and
  side-effect-free; logging is the *caller's* responsibility, which keeps the tools trivially
  unit-testable.
- **`policy_violations`** — every rule that fired, on every run, tied to `run_id`.
- **`decisions`** — the human's approve/edit/dismiss action, with the edited body if any.
- **`sent_log`** — the non-strippable audit row for an approved send. `would_send_to`, not
  `sent_to`, by design (§1, §7).
- **`crm_sync`** — one row per CRM sync attempt, `status` of `pending|synced|failed`.
- **`rejected_events`** — webhook requests rejected before a run was even created (bad secret,
  malformed body, duplicate ref) — useful for seeing attack/misconfiguration traffic separately from
  real runs.

A second migration (`0002_rls_lockdown.sql`) enables deny-all Row-Level Security on every table and
revokes all grants from `anon`/`authenticated`. This matters specifically because Supabase
auto-publishes the public schema over PostgREST, reachable with the publishable ("anon") key — a
table without RLS is readable/writable by any client holding that key, even though the app itself
never uses that key at all (it connects directly via `DATABASE_URL`, bypassing PostgREST entirely).
This migration is a defense-in-depth structural guarantee, not a functional requirement for the app
to work — it costs the running app nothing and closes an entire class of future misconfiguration risk
(e.g., someone re-enabling the Data API by mistake later).

One sharp technical detail worth being able to explain: `DATABASE_URL` must point at Supabase's
**session pooler (port 5432)**, not the transaction pooler (6543) — the LangGraph `PostgresSaver`
checkpointer uses psycopg prepared statements, which the transaction pooler does not support.
`app/db.py` actively checks for `:6543` in the URL and raises a clear error rather than failing
mysteriously deep inside LangGraph.

## 11. Reporting — the part that's actually new business value

Both reports are **thin read-queries over data the system was already going to collect for
observability** — neither is a separate aggregation job:

- **Content gap** (`/reports`, `content_gap_report()`): groups and counts `leads.missing_fact`,
  descending. Directly actionable for SUNKSSD's marketing/content team, since — notably —
  sunkssd.io has no FAQ page at all today. This report is literally "here is the FAQ page you should
  write, ranked by how often guests actually ask."
- **Demand signal** (`demand_signal_report()`): leads broken down by intent, pet/kid flags, and
  unconverted status (`dismissed`, `escalated`, `awaiting_ops_quote`). This is demand SevenRooms
  (the booking platform) structurally cannot see, because it only records completed bookings — a
  guest who asked three questions and never booked is invisible to the booking system but fully
  visible here.

Both are explicitly framed as **measured outputs, not targets** — the real escalation rate across
the 16 synthetic + 2 error fixtures is whatever a live run actually produces, not a number asserted
in advance. This framing matters in a presentation: it's an honest "here's what the instrumentation
shows," not a cherry-picked success metric.

## 12. Error handling — three named, deterministic fixtures

| Fixture | Trigger | Behavior |
|---|---|---|
| `FIXTURE_BAD_WEBHOOK` | Missing/wrong `X-Desk-Secret` header, or a malformed JSON body | 401 or 422, a `rejected_events` row is written, **no run is ever created** — the system doesn't even start a graph execution for input it can't trust. |
| `FIXTURE_MODEL_TIMEOUT` | Sentinel string in the message body | The run is marked `error`, but a lead is still created and marked `escalated` — the enquiry is never silently lost just because the model call failed. |
| `FIXTURE_CRM_FAIL` | Sentinel string in the message body | The draft approves completely normally; only the CRM sync step fails, deterministically. Run is marked `partial`. `/api/retry-crm` re-syncs the already-approved lead without touching the graph, the LLM, or the draft. |

The deliberate asymmetry across these three is itself a teaching point: a failure *before* a lead
exists (bad webhook) is a clean reject with no lead record at all; a failure *after intake but
before approval* (model timeout) still produces an escalated lead so no enquiry vanishes; a failure
*after approval* (CRM) doesn't even touch the already-approved content — it's purely a sync-layer
retry concern.

## 13. Testing — and the honest distinction between "unit-tested" and "live-validated"

100 tests total (`pytest`), TDD throughout — every rule, node, and adapter has a test file, with
`pytest -m live` excluded by default because that marker requires a real `DATABASE_URL` and GHL
sandbox credentials.

The system was also **run for real** — a real free OpenRouter model, a real Supabase database, a
real GHL sandbox — via a Linux Docker container (this specific dev machine has a Windows Application
Control policy that blocks the native `psycopg`/`uuid_utils` DLLs, so local unit tests here run
against mocks; the Linux deployment is unaffected). This live run caught four bugs **no mocked test
could have caught**, each now covered by a regression test:

1. **psycopg3 needs `Jsonb(...)` around every dict bound to a `jsonb` column.** The very first real
   webhook POST 500'd on `cannot adapt type 'dict'`. Fixed with a `_jsonb()` helper wrapping every
   jsonb-bound parameter in `app/repo.py`.
2. **`PostgresSaver.from_conn_string()` is a context manager, not a constructor.** Calling it
   without `with` returns the generator wrapper itself, which LangGraph's `compile()` rejects.
3. **A single shared `psycopg.Connection` is not thread-safe across FastAPI's threadpool workers.**
   A background-task thread and a request thread both touching the checkpointer produced "the
   connection is closed." Fixed with a `psycopg_pool.ConnectionPool` (explicitly supported by
   `PostgresSaver`), giving each caller its own connection.
4. **GoHighLevel's `/contacts/upsert` 400s with no email/phone to dedup on** — covered in §9.

Per-node run-timeline/tool-call logging (`run_steps`, `tool_calls`, `policy_violations`) also *did
not exist* until this live pass exposed that no node actually called the already-written
`repo.record_*` functions — `app/graph.py`'s `_instrumented()` wrapper (§10) is the fix, applied
once at the graph-assembly level rather than inside each of the 8 node functions individually.

A real end-to-end run of the flagship demo message, against the real chosen model, reached the
*correct* outcome through the *correct* path: the first draft failed PR-10 (no bundling statement
for the camping clause), triggered exactly one real repair attempt via `content`, and the second
draft passed with both ticket links and a confirm-with-the-team line — then genuinely paused at the
human-approval interrupt, resumed on `approve`, and wrote a real `sent_log` row and a real `crm_sync`
row. Nothing about that sequence was staged by a test fixture; it is what the system actually did
when run for real.

## 14. Security review — findings and fixes

A security review (run as a Claude subagent) found two Critical and two High findings, all fixed
within the project's fix budget:

- **Critical — production used `InMemorySaver`, not `PostgresSaver`.** Every pending human approval
  would have been lost on any process restart — the one state standing between a draft and nothing
  happening to it at all. Fixed: `app/main.py`'s `get_graph()` now always builds the
  Postgres-backed graph in production; only tests inject an in-memory one explicitly.
- **Critical (checked, verified false positive) — Jinja2 autoescaping.** The installed Starlette
  version already autoescapes `.html` templates by default, confirmed directly by testing that
  `<script>` renders escaped. `autoescape=True` was made explicit anyway in `app/main.py`, because
  `requirements.txt` pins no dependency versions and this behavior should never silently depend on a
  transitive default.
- **High — Docker port bound to all interfaces.** `docker-compose.yml`'s `"8000:8000"` would have
  let traffic reach the app directly, bypassing the reverse proxy's TLS termination. Fixed: bound to
  `127.0.0.1:8000:8000` only.
- **High — raw guest PII sent to Telegram.** Covered in §7 (`notify_node`) — fixed with redaction in
  `app/notify.py`.

Two Warn-level findings were also fixed: a misconfigured `DESK_WEBHOOK_SECRET` used to return the
identical 401 an attacker would see, giving the operator no way to distinguish "I misconfigured this"
from "someone's probing me" — fixed by raising a distinct 500 when the secret isn't configured at
all. And dead `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` env vars were removed/documented as
deliberately unused, since the service-role key bypasses RLS entirely and leaving unused vars around
invites someone to "helpfully" wire them back in later.

Remaining lower-severity findings (webhook rate limiting, `rejected_events` retention, unpinned
dependency versions, a verbose 422 error exposing the webhook's required field names) are real but
don't create an exploit path today at demo scale — they're recorded as `TODOS.md` items 5–8 rather
than silently dropped, each with an explicit cost/benefit and a trigger condition for revisiting.

## 15. Deployment shape

Single Docker container (`Dockerfile`: `python:3.12-slim`, `uvicorn app.main:app`), deployed via
`docker compose up -d --build` onto an existing Oracle Cloud VM, behind that VM's existing reverse
proxy, in its **own** Docker network — explicitly not joined to the VM's existing n8n stack network.
The container binds only to `127.0.0.1:8000` (§14); the reverse proxy is what's actually
internet-facing.

## 16. Explicitly not built — and why that's a feature of the explanation, not a gap

A follow-up agent that would re-engage a lead stuck in `awaiting_ops_quote` (a group-quote lead
waiting on a human to actually quote it) is mentioned as a natural next step in the demo narrative
but was deliberately not built — it's a second agent with its own trigger/cadence design, out of
scope for this build's time budget. `TODOS.md` lists eight further deferred items (production
retry/backoff policy for a *real* LLM transient failure as opposed to the fixture; a defined
Supabase-write-failure path, which is currently the one `ops`-node failure with no fixture at all;
dismiss-flow test coverage; double-approve idempotency; webhook rate limiting; `rejected_events`
retention; dependency pinning; and the verbose-422 information disclosure) — each with an explicit
why/pros/cons/depends-on, which is itself worth pointing to in a presentation as evidence of
deliberate scoping rather than incomplete work.

## 17. How a real channel would actually plug in (and why Telegram, not WhatsApp, for the operator ping)

Today only a simulator/form posts to `/webhook`. A real WhatsApp Cloud API integration would need to
respect Meta's messaging windows: free-form replies only work inside the open 24-hour
customer-service window that opens when a user messages first; anything business-initiated (like
the operator's "a draft is waiting for you" ping) needs a pre-approved utility template in a billable
category. That's the concrete, non-obvious reason the operator notification in this system is
Telegram rather than WhatsApp — it isn't a style preference, it's a messaging-policy constraint that
would otherwise block the exact feature the ping exists to provide.

## 18. Glossary / things a presentation audience might ask about

- **LangGraph `StateGraph`** — a directed graph of Python functions ("nodes") sharing one mutable
  state dict, with conditional edges chosen by router functions. Not an LLM agent loop; the model is
  only called from two specific nodes.
- **`interrupt()` / `Command(resume=...)`** — LangGraph's durable human-in-the-loop primitive. The
  graph's entire state is checkpointed (to Postgres here) at the pause point and can be resumed any
  time later, even after a process restart, as long as the same `thread_id` is used.
- **Checkpointer** — the pluggable persistence layer for LangGraph's state across pauses;
  `InMemorySaver` for tests, `PostgresSaver` (via a `ConnectionPool`) in production.
- **Policy gate** — this project's term for the 11-rule deterministic validator in `app/policy.py`;
  not an LLM, not a classifier — plain Python logic over a structured draft.
- **`would_send_to` / `sent_log`** — the deliberately-named audit table proving what *would* have
  been sent; there is no code path anywhere in this repository that can message a real person.
- **Content-gap report / demand-signal report** — the two business-value reports described in §11,
  built from data the system already collects for observability, not from a separate analytics
  pipeline.

---

### If asked to summarize this in 60 seconds

This is a decision-support system for guest enquiries, not an autoresponder. It reads a guest
message, drafts a cited reply from a brand's real public website content, and runs that draft
through eleven deterministic rules designed to catch the most reputationally expensive classes of
AI mistake — invented prices, false availability claims, uncited policy statements — before a human
ever sees it. A human must explicitly approve, edit, or dismiss every single reply, and even an
edited reply is re-validated by the same rules before anything is logged. Nothing is ever actually
sent. Along the way, the same instrumentation that makes the system auditable also produces two
reports the business doesn't have today: which questions its own website fails to answer, and how
much guest demand its booking platform can't see because those guests never completed a booking.
