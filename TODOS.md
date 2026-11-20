# TODOs

Deferred items from the /plan-eng-review on 2026-11-13. None block the 3-4 day demo build; each is
real deferred work worth a record, per D4.

## 1. Production LLM retry/backoff policy

**What:** Define retry/backoff behavior for the real `LLM_MODEL` call on a transient OpenRouter
failure, distinct from `FIXTURE_MODEL_TIMEOUT` (which only proves the error path is *visible*, not
what a real transient failure should do).

**Why:** Right now a real timeout and the test fixture get identical treatment (escalate, lead stays
`escalated`). That's a safe default, but a one-retry-then-escalate policy would reduce needless
escalations from a single network blip.

**Pros:** Fewer false escalations; still safe-by-default if the retry also fails.
**Cons:** Adds a retry/backoff parameter to tune and test; low payoff at demo traffic volumes.

**Context:** Noted in the design doc's README-must-state section as explicitly deferred. Revisit once
real (non-synthetic) traffic exists and timeout frequency is measured.

**Depends on / blocked by:** Nothing — can be picked up any time after the core graph exists.

## 2. Supabase-write-failure error path

**What:** Define and test what happens if the `ops` node's Supabase lead write itself fails (network
blip, connection pool exhaustion), as opposed to the GHL write, which has `FIXTURE_CRM_FAIL`.

**Why:** Supabase is the source of truth for `leads`. An unhandled Supabase failure is currently more
severe than an unhandled GHL failure, which already has a fixture and a defined `partial` state — this
asymmetry was flagged in the Test Review as a real gap.

**Pros:** Closes the one `ops`-node failure path with no defined behavior at all.
**Cons:** Requires a 4th error-state concept distinct from the three named fixtures; adds a test.

**Context:** Flagged in both the Test Review coverage diagram and the Failure modes table of the
2026-11-13 eng review as the one path with no fixture and no stated behavior.

**Depends on / blocked by:** The `ops` node existing (Day 2-3).

## 3. Dismiss-flow test coverage and demo walkthrough

**What:** The dismiss action (approve / edit / **dismiss**) is named generically in the Testing
Approach and has an integration test planned, but the demo script never walks it, and its interaction
with CRM/report counting ("dismiss does not sync to CRM") needs its own explicit test.

**Why:** Thin coverage on one of the three core approval actions is a gap a careful reviewer would
notice, even though it's covered "in principle."

**Pros:** Rounds out the approval loop's test coverage to match approve/edit.
**Cons:** No demo-script value — this is coverage hygiene, not a visible feature.

**Context:** Flagged in the Test Review's coverage diagram as a GAP with "thin coverage."

**Depends on / blocked by:** The command centre UI (Day 3).

## 4. Double-approve idempotency

**What:** Prove that clicking Approve twice on the same draft (double-click, or two browser tabs) does
not produce a second `sent_log` row or attempt to resume an already-resumed LangGraph `thread_id`
twice.

**Why:** LangGraph's `interrupt()`/`Command(resume=...)` behavior on a thread that's already past its
interrupt point isn't specified in this design, and a tired operator double-clicking under time
pressure is a realistic failure mode for exactly the system whose whole point is "nothing sends
twice."

**Pros:** Closes a real idempotency gap on the system's single most important action.
**Cons:** May require state-machine logic in the `approval` node (check current status before
accepting a resume) beyond what LangGraph gives for free — effort unknown until investigated.

**Context:** Flagged in the Test Review coverage diagram and the Failure modes table as the one
remaining gap with no fixture and no test, folded into this TODO rather than separately ticketed.

**Depends on / blocked by:** The `approval` node and command centre UI (Day 2-3); investigate
LangGraph's actual behavior on a double-resume before estimating effort.

## 5. Rate limiting on /webhook

**What:** No per-IP throttle or message-count cap on the webhook. Each accepted message triggers two
real LLM calls when `OPENROUTER_API_KEY` is live.

**Why:** Flagged by the mandated security review (2026-11-13). Anyone who obtains
`DESK_WEBHOOK_SECRET` can submit unlimited unique `external_ref`s (the dedup check only blocks
identical refs) and run up real LLM charges. Low likelihood at demo scale - the secret isn't
distributed anywhere - but real for a deployed, long-lived instance.

**Pros:** Caps worst-case cost exposure. **Cons:** Needs a rate-limit store (even an in-memory
token bucket) and a policy for what "too many" means for a single-operator demo.

**Context/Depends on:** Pick a library (slowapi, or a 10-line in-memory limiter) once real traffic
volume is known.

## 6. `rejected_events` has no retention policy

**What:** Every rejected webhook (bad secret, malformed body, duplicate ref) writes a row with no
TTL or size cap, and writing it requires no authentication at all.

**Why:** Flagged by the security review. At scale this is a storage-growth/DoS vector against the
database from unauthenticated requests.

**Pros/Cons:** A weekly prune job is cheap; the question is just whether it's worth building before
there's any real traffic to generate these rows.

**Depends on:** A scheduled job mechanism (cron on the VM, or a Supabase scheduled function).

## 7. Pin dependency versions in requirements.txt

**What:** `requirements.txt` currently has no version pins.

**Why:** Flagged by the security review - the explicit `autoescape=True` fix (`app/main.py`) exists
specifically because the project should not depend on a transitive dependency's default behavior.
Pinning (`pip freeze` after a known-good install) removes this whole class of version-drift risk,
not just for Jinja2.

**Depends on:** None - do this whenever convenient, ideally before the first real deploy.

## 8. 422 error on malformed webhook body reveals the expected schema

**What:** `app/main.py`'s webhook handler currently returns the raw `KeyError`/`JSONDecodeError`
message on a malformed body, which names the required fields (`channel`, `body`, `external_ref`) to
an unauthenticated prober.

**Why:** Minor information disclosure, flagged by the security review. Low severity since the
webhook secret check already runs first and rejects unauthenticated requests before this code path
is reached - this only matters once someone already has the secret.

**Depends on:** None - swap the exception message for a generic one, or move to a Pydantic request
model (which would also give free type validation).
