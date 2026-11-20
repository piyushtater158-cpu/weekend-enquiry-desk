# Weekend Enquiry Desk

A portfolio proof-of-work build for the SUNKSSD "AI Automation & Agentic Systems Engineer" role
([wellfound.com/jobs/5035084](https://wellfound.com/jobs/5035084-ai-automation-agentic-systems-engineer)).

Turns a guest message into a sourced draft reply, a lead record, and a human approve / edit / dismiss
decision. **Nothing is ever sent.** There is no code path that could message a real guest - the
`sent_log` table is named `would_send_to` on purpose.

## What this actually is

- **Public**: the knowledge base is scraped from `sunkssd.io` only (never Instagram, never the
  SevenRooms/IPMS247 booking widgets, never WhatsApp). Every chunk carries the URL it came from and
  an `accessed_at` timestamp.
- **Synthetic**: all 16 guest messages in `data/synthetic_messages.json`, plus the 2 error-fixture
  messages in `data/error_fixtures.json`, are labelled `SYNTHETIC`. None are real guest traffic.
- **Sandboxed**: the GoHighLevel integration is wired against the builder's own GHL account/location.
  **SUNKSSD's use of GoHighLevel is unconfirmed and is not claimed anywhere in this repo.**
  `CRM_PROVIDER` defaults to `supabase`, so the deployed demo works indefinitely without GHL at all -
  GHL is demonstrated as an opt-in, not load-bearing.

## Why there is no pgvector

sunkssd.io is roughly 1,200-1,400 words of real public copy - 24 chunks after scraping. At that size,
putting the whole corpus in the prompt beats top-k similarity search: it's simpler, it can't return
an "empty retrieval" (which is why that isn't one of the three error fixtures below), and it doesn't
risk missing one clause of a multi-intent message (the flagship demo message asks about dogs,
tickets, *and* camping in one sentence - a vector search tuned for one of those could easily drop
another). The threshold where pgvector would become the right call is roughly 500+ FAQ or
past-reply pairs - once real inbox history exists to retrieve over, not before.

## Why there is no n8n

The job posting names "n8n, Make, LangGraph or similar - **and judgment about when plain code is
better**." The orchestration here is a LangGraph `StateGraph` with conditional edges and a policy
gate - that's code, with real unit and integration tests, not a flowchart. (The builder's other
portfolio projects already demonstrate n8n; building a third one here would be the opposite of the
judgment the posting is actually asking for.)

## The policy gate (the actual product)

Eleven rules in `app/policy.py`, each with an id (`PR-01` ... `PR-11`), each visible on the run
timeline whether it fires or not:

| Rule | What it blocks |
|---|---|
| PR-01 | Currency figures (AED 150, $50, 150 dirhams) |
| PR-02 | Availability claims ("we have space", "sold out") |
| PR-03 | An affirmative yes/no answer with no citation |
| PR-04 | A citation that doesn't exist in the corpus |
| PR-05 | Any factual sentence with no citation |
| PR-06 | Policy claims (refund, cancel, re-entry, age limit, alcohol) with no citation |
| PR-07 | A URL not on the official-links whitelist |
| PR-08 | `group_quote` answered with numbers instead of escalating |
| PR-09 | A stale corpus (45-day threshold) - a note, not a block |
| PR-10 | The camping/day-pass bundle question answered yes/no instead of both links |
| PR-11 | A resolved date outside the published weekends |

**An edited draft re-runs the full policy gate before it can be logged as sent.** This is the one
fix that makes "zero invented prices/availability" true for edited drafts, not just unedited ones -
see `tests/test_graph_integration.py::test_edit_that_fails_recheck_escalates_with_no_override`. An
operator who edits in a fake price gets re-escalated with no override, same as the model would.

## Two reports, built from data the system already collects

`app/nodes/reporting.py` tags every escalated/dismissed lead with `missing_fact` - which rule fired,
or which intent had no self-serve answer. `/reports` queries that tag:

- **Content gap**: a ranked list of the questions sunkssd.io fails to answer, with how often guests
  asked - directly actionable for SUNKSSD's marketing team, since the site has no FAQ page at all.
- **Demand signal**: what SevenRooms can't see - people who asked and never booked, by intent, pet/kid
  flags, and party size.

Both are measured outputs, not targets. The actual escalation rate out of the 16 fixtures is whatever
`pytest` + the live run produce - see **Testing & Security** below, not a number asserted here.

## Design language

The command centre reuses sunkssd.io's own design tokens - the real `oklch()` palette from
`data/corpus/site-styles.css`, Space Grotesk for display type, Inter for body copy, and the site's
full-bleed ✦-separated uppercase marquee (primary band, ink text). It runs the site's `.dark` block
rather than its daytime `:root` palette: an operator stares at this for hours, so it reads as a
console, not a landing page - no photography, one marquee that respects `prefers-reduced-motion`,
section labels and live-badge motifs matching the marketing site, otherwise still. Console-only
status colours (`--card`, `--ok`, `--err`) are local additions on top of the lifted palette.

Every queued lead is a card, not a table row. The draft the model actually wrote is visible inline
(lazy-loaded from the LangGraph checkpoint, not stored redundantly) with its per-sentence citations,
and **Edit** opens it in place: each sentence becomes a textarea, citations travel with it, and
**save & re-run the policy gate** resumes the graph with `action: "edit"` - the edited body goes back
through all 11 policy rules before it can be logged as sent, exactly like `approve` does for the
model's own draft (see "An edited draft re-runs the full policy gate" above). A fake price typed into
the edit box gets the lead re-escalated with no override, same as the model's.

The header states which CRM is active - `CRM_PROVIDER`, read live, never cached - and whether
GoHighLevel credentials are present. A **Demo GoHighLevel** button (and a per-lead **Push to
GoHighLevel**) calls `/api/demo-ghl-sync`, which forces the GHL adapter for that one sync without
mutating `CRM_PROVIDER` - so the default stays `supabase` while the live CRM path is still
demonstrable. Per the sandboxing note above, this only ever describes the *builder's* own GHL
account; it is not a claim about SUNKSSD. Each queued lead carries its own CRM sync status
(synced / failed / not synced, with the error on a failure and a retry count), and a lead synced
through GoHighLevel gets a direct link to its contact in the GHL app - gated on that lead's own
`crm_provider`, since Supabase's `external_id` is just the lead's UUID and would build a dead link.

## Architecture

```
POST /webhook (X-Desk-Secret, idempotent on external_ref)
        |
        v  [BackgroundTasks - never inline; two LLM calls + interrupt() would
        |   otherwise block the request, and the checkpointer is sync psycopg]
        v
   intake (model) --writes leads as 'new'--> research (full corpus, no vector search)
        |
        v
   content (model, emits {sentence, cites[]} - not plain text)
        |
        v
   policy (11 rules) --fail, 1st time--> content (repair)
        |  \--fail, 2nd time / recheck fail--> ops (escalate, no override)
        |  pass
        v
   notify (Telegram ping - runs once, BEFORE the interrupt re-executes on resume)
        v
   approval [interrupt()] --resume(approve|edit|dismiss)--
        |  edit --> back to policy (re-check the edited body)
        v
   ops (CRM sync only - leads already exists; Supabase is source of truth, GHL is opt-in sync)
        v
   reporting (runs on every outcome - tags missing_fact)
        v
       END
```

## Three error fixtures

| Fixture | Trigger | Behavior |
|---|---|---|
| `FIXTURE_BAD_WEBHOOK` | Missing/wrong `X-Desk-Secret`, or malformed body | 401/422, `rejected_events` row, never creates a run |
| `FIXTURE_MODEL_TIMEOUT` | Sentinel in message body | Run marked `error`; lead still created as `escalated` so the enquiry is never silently lost |
| `FIXTURE_CRM_FAIL` | Sentinel in message body | Draft approves normally; the CRM sync step fails deterministically; run marked `partial`; `/api/retry-crm` re-syncs the already-approved lead without touching the graph or the LLM |

## Testing & Security

**TDD throughout.** Every rule, node, and adapter has a failing test written first - see
`tests/`. 100 tests, unit + integration, `pytest -m live` excluded by default (that one needs a real
`DATABASE_URL`). Run `pytest` to see the current count and the real escalation rate across the 16
synthetic + 2 error fixtures.

**Security review.** Ran as a Claude subagent (Codex was not installed locally). Two Critical and
two High findings, all fixed within the fix budget:

- **Critical - production used `InMemorySaver`, not `PostgresSaver`.** Every pending approval would
  have been lost on a process restart - the one thing standing between a draft and nothing happening
  to it. Fixed: `app/main.py` now always builds the Postgres-backed graph.
- **Critical (verified false positive) - Jinja2 autoescaping.** Checked before fixing: the installed
  Starlette version already autoescapes `.html` templates by default (`&lt;script&gt;` renders
  escaped, confirmed directly). Made `autoescape=True` explicit anyway in `app/main.py`, since
  `requirements.txt` pins no versions and this should never depend on a transitive default.
- **High - Docker port bound to all interfaces.** `docker-compose.yml`'s `"8000:8000"` would have let
  traffic reach the app directly, bypassing the reverse proxy's TLS. Fixed: bound to `127.0.0.1` only.
- **High - raw guest PII sent to Telegram.** The operator ping included the guest's message verbatim
  (names, phone numbers, emails) in a Telegram chat with no redaction. Fixed: `app/notify.py` now
  redacts emails/phone numbers and caps the preview to 40 chars.

Two Warn-level findings were also fixed (a misconfigured `DESK_WEBHOOK_SECRET` returned the same 401
as an attacker, with no way to tell the two apart; dead `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`
env vars invited a future RLS bypass). The remaining Warn/Suggest findings (webhook rate limiting,
`rejected_events` retention, unpinned dependencies, a verbose 422 error) are real but don't create an
exploit path today - recorded in `TODOS.md` items 5-8 rather than silently dropped.

**Known limitation, this dev machine only**: a local Windows Application Control policy blocks the
native `psycopg`/`uuid_utils` DLLs, so unit tests here run with mocks, never a live Postgres. The
Linux Docker deployment is unaffected - `pytest -m live` runs there or on a Linux dev machine.

## Live-validated, not just unit-tested

The whole system was run for real - real free OpenRouter model, real Supabase database, real GHL
sandbox - via a Linux Docker container (bypassing this dev machine's psycopg block). This caught four
bugs no mocked test could have, each fixed and covered by a regression test:

- **psycopg3 needs `Jsonb(...)` around every dict bound to a `jsonb` column.** The very first real
  webhook POST 500'd on `cannot adapt type 'dict'`. Every write in `app/repo.py` now wraps jsonb
  parameters explicitly (`_jsonb()` helper).
- **`PostgresSaver.from_conn_string()` is a context manager, not a constructor.** Calling it without
  `with` (needed to keep the connection alive for the app's lifetime) returned the generator wrapper
  itself, which LangGraph's `compile()` rejected outright.
- **A single shared `psycopg.Connection` is not safe across FastAPI's threadpool workers.** The
  background-task thread and a request thread both touching the checkpointer hit "the connection is
  closed." Fixed with a `psycopg_pool.ConnectionPool`, which `PostgresSaver` explicitly supports.
- **GoHighLevel's `/contacts/upsert` 400s with no email or phone to dedup on** - true of every one of
  the 16 synthetic fixtures, since none carries a real guest contact. `GoHighLevelCRM` now falls back
  to a labelled synthetic email (tagged `synthetic-contact`) when the channel carries no real
  identifier, and uses the real one when it does.

Per-node run timeline/tool-call logging (`run_steps`, `tool_calls`, `policy_violations` - named
explicitly in the job posting and this design) also did not exist until this pass: no node ever
called the `repo.record_*` functions that were already written for it. `app/graph.py` now wraps every
node to record it automatically; the three named tools (`search_knowledge_base`,
`get_official_links`, `escalate_for_human`) log their own timed calls.

A real end-to-end run of the flagship demo message, with `nvidia/nemotron-3.5-super-vl:free`, reached
the correct outcome through the correct path: the first draft failed `PR-10` (no bundling statement
for the camping question), got one real repair attempt via `content`, and the second draft passed
with both ticket links and a "please confirm with our team" line - then paused at the human-approval
interrupt for real, resumed on `approve`, and wrote a real `sent_log` row and a real `crm_sync` row.
No test fixture staged any of that; it is what the system actually did.

**Model choice, live-tested, not assumed.** `nvidia/nemotron-3.5-lightning:free` handles `intake`'s
simple classification fine but degenerates into 100+ seconds of whitespace-token generation on
`content`'s larger structured-drafting prompt - reproduced twice, not a fluke. Switched to
`nvidia/nemotron-3.5-super-vl:free`: 5-20s, clean JSON, no markdown fences, correctly refused to act on
a prompt-injection test message. The corpus itself was also trimmed from 24 to 12 chunks - 8 team-bio
sections and brand-history copy were real public text but irrelevant to guest FAQs, and were nearly
doubling every prompt for no benefit.

## Running it

```bash
cp .env.example .env   # fill in DATABASE_URL (session pooler, port 5432!), GHL/Supabase/Telegram
python -m venv .venv && .venv/bin/pip install -r requirements.txt  # or .venv\Scripts\pip on Windows
psql "$DATABASE_URL" -f migrations/0001_schema.sql
psql "$DATABASE_URL" -f migrations/0002_rls_lockdown.sql
psql "$DATABASE_URL" -f migrations/0003_channel_deliveries.sql
pytest                       # unit + non-live integration
pytest -m live               # needs a real DATABASE_URL and GHL sandbox credentials
uvicorn app.main:app --reload
```

## Deploying

```bash
git pull
docker compose up -d --build
```

Runs on the existing Oracle Cloud VM behind its existing reverse proxy, in its own Docker network -
not joined to the VM's existing n8n stack.

### Public demo on Vercel

A second, public copy runs at **https://weekend-enquiry-desk.vercel.app** (Vercel project
`weekend-enquiry-desk`, Python function `app/main.py`, region `syd1` next to the Supabase pooler).
Two opt-in env switches make that possible. Both default off, so the Docker deployment above and
the test suite behave exactly as before:

| Env var | Effect |
|---|---|
| `DESK_OPEN_ACCESS=true` | No HTTP Basic login on the command centre or `/api/*`. `/webhook`'s `X-Desk-Secret` and the Twilio signature check stay on. |
| `DESK_RUN_INLINE=true` | Runs the graph inside the request instead of `BackgroundTasks`, because a serverless instance may freeze once the response is sent. Still returns 202, and still logs (not raises) graph errors. |

Deploy from this folder with `vercel deploy --prod --yes`. Production env vars are set with
`vercel env add`; `.env` never uploads. Three things in `.vercelignore` matter:
- **`.env` and `.env.*`:** secrets, plus the OIDC token `vercel link` writes to `.env.local`.
- **`pyproject.toml`:** it only holds pytest config, and while it's present Vercel ignores
  `requirements.txt` and installs nothing.
- **`tests/`, `.venv`, `scripts/` and the docs:** not needed at runtime.

**Connection limit:** the Supabase session pooler caps clients (pool size 15), and every running
copy of the desk holds connections. If the public copy shows `EMAXCONNSESSION` / "max clients
reached in session mode", another copy (the VM, a local run) or another app on the same project is
holding the slots. Stop it, or raise the pool size in Supabase.

## Twilio WhatsApp sandbox (demo conversation loop)

Optional. When configured, a real WhatsApp sandbox message enters the same LangGraph pipeline as
fixtures; Approve still writes `sent_log`, and with `TWILIO_WHATSAPP_SEND=true` also delivers the
draft via Twilio and records `channel_deliveries` (same audit shape as `crm_sync`).

1. In Twilio Console → Messaging → Try it out → WhatsApp, join the sandbox from your own phone
   (`join <your-code>` to `+14155238886`).
2. Set in `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`,
   `TWILIO_SANDBOX_DEMO_TO=whatsapp:+<your-number>`, `TWILIO_WHATSAPP_SEND=true`,
   and `PUBLIC_BASE_URL=https://<your-ngrok-host>` (no trailing slash).
3. Expose the desk: `ngrok http 8001` (or your reverse proxy), then set the sandbox
   “When a message comes in” webhook to `https://<PUBLIC_BASE_URL>/webhooks/twilio/whatsapp`.
4. Text a guest FAQ from that phone → card appears in the queue → Approve → reply arrives on
   WhatsApp; check timeline for `twilio_whatsapp_send` / WhatsApp delivery.

With `TWILIO_WHATSAPP_SEND` unset/false, Approve stays log-only (portfolio default). Operator pings
remain Telegram — business-initiated WhatsApp outside the open window needs templates.

## Not built

A follow-up agent (re-engaging a lead stuck in `awaiting_ops_quote`) - mentioned in the demo, not
built. See `TODOS.md` for what else was deliberately deferred and why.
