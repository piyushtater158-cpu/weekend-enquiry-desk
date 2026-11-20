# Pitch script — no filler

**Runtime:** ~10–12 min including live demo  
**Prep:** Desk logged in, second tab. Warm one fixture before the call.

---

## 1 · Title — 20s

> Built your JD as a working MVP on your brand. Eight agents. Eleven hard rules. Nothing leaves until a human says so. Live-validated.

→

## 2 · Problem — 40s

> Guests ask everywhere. No FAQ on the site. An invented price or refund kills trust. Booking tools only see people who booked. Your JD asked for research, content, reporting, ops agents + a command centre with logs and approvals. I built that.

→

## 3 · Constraints — 25s

> Public site only. Synthetic guests. GHL is *my* sandbox — Supabase is default. I don’t claim you use GHL. Decision support, not an unsupervised bot.

→

## 4 · Agents — 35s

> Four JD domains, eight nodes. Only **two** call an LLM — intake and content. Research, policy, notify, approval, ops, reporting are deterministic. Shared state. Logged tool calls. Not a chat loop.

→

## 5 · Orchestration — 45s *(short path only)*

> Webhook → auth + idempotent `external_ref` → 202 → graph in background.  
> Policy sets `next_step`: fail once → repair; still fail → park for human; pass → notify → `interrupt()` in Postgres.  
> Edit → full policy recheck. Fake price → escalate, no override. CRM fail after approve → `partial`, retry without re-running the model.  
> Routers pick the path. The LLM doesn’t.

→

## 6 · Policy gate — 40s

> This is the product. Zero LLM in the gate.  
> Live msg-01: first draft failed **PR-10** (camping without both links + confirm). One repair. Passed.  
> Human types `AED 150` in edit → **PR-01** → escalate. Same rules for the model and the operator.

→

## 7 · Schemas — 30s

> Logs and approvals aren’t labels — they’re contracts.  
> `GraphState` · structured `Draft{cites, non_factual}` · edge map `approval → notify → interrupt` · tables: `run_steps`, `tool_calls`, `policy_violations`, `would_send_to`.

→

## 8 · Command centre → LIVE DEMO — 20s then switch

> Real UI. Human decides. Watch it run.

### ★ LIVE DESK — 3 min. Show, don’t narrate long.

| Do | Say (short) |
|---|---|
| Fire **msg-01** | Multi-intent: dog, tickets, camping. |
| Timeline | Repair loop = content/policy twice. PR-10. |
| Draft | Cites + both ticket links. Structured, not a blob. |
| **Approve** | `would_send_to`. Log-only by default. |
| Optional one: **GHL demo** *or* edit `AED 150` | Real CRM upsert *or* edit recheck kills the fake price. |
| Reports 10s | Content gap + demand SevenRooms can’t see. |
| Back to deck | — |

→

## 9 · Wiring — 30s

> Default: log-only. WhatsApp send = opt-in flag, only after Approve.  
> Webhooks, Supabase + RLS + checkpoints, GHL v2 opt-in, Telegram redacted, Twilio signed inbound, OpenRouter, Docker on OCI localhost. CRMPort — swap CRM without rewriting ops.

→

## 10 · Judgment — 30s

> I use n8n elsewhere. Here: LangGraph + tested policy beats a flowchart. Full corpus beats pgvector at 12 chunks — revisit at ~500+. Deterministic gate beats an LLM judge. Telegram for ops pings — WhatsApp templates block business-initiated.

→

## 11 · Receipts — 25s

> 139 tests. Four bugs only live caught. Security: PostgresSaver (not memory), localhost bind, PII redaction. Failures: reject early / keep lead / never regenerate an approved reply.

→

## 12 · Value — 20s

> Same run writes the FAQ you don’t have (`missing_fact`) and demand that never booked. No separate analytics job.

→

## 13 · Scope — 20s

> Not built: follow-up agent, rate limits, pin deps — in TODOS with triggers. Hired: wire your channels → grow corpus from the gap report → reuse this command centre for agency ops.

→

## 14 · Close — 20s

> First-90-days MVP shape, already shipped. Paid assessment next. Available now, IST. Repo + deeper live path whenever you want.

**Stop. Q&A.**

---

## If they only give you 8 minutes

**1 → 3 → 4 → 6 → 8+demo → 10 → 14.** Skip the rest unless asked.

---

## Q&A one-liners

| Ask | Answer |
|---|---|
| Autoresponder? | No. `would_send_to`. Send is opt-in after Approve. |
| Why not n8n? | Durable interrupt + tested policy. n8n elsewhere. |
| GHL real? | Yes, my sandbox. Default CRM is Supabase. |
| Invented prices? | PR-01 + edit recheck, no override. |
| Multi-agent? | Specialised nodes on shared state. Two LLM calls. |
