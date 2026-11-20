"""Database writes. Deterministic SQL, no ORM. Every function here is a thin,
mockable seam - node unit tests monkeypatch these rather than hitting a real DB.

leads is written at intake (status='new'), not at ops - see the design doc's
eng-review fix: ops only syncs to CRM, it never owns lead creation, because a
model-timeout fixture needs a lead row to exist as 'escalated' before ops ever
runs, and the demand-signal report counts leads that never reached ops at all.
"""
from __future__ import annotations

from typing import Any

from app.db import get_connection


def _jsonb(value: Any):
    """psycopg3 does not adapt a plain dict/list to a jsonb column on its own -
    caught live against the real database (unit tests mock repo.* entirely, so
    this never surfaced until the first real webhook POST). Every jsonb-bound
    parameter goes through this.
    """
    if value is None:
        return None
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def create_message(*, channel: str, raw_payload: dict, body: str, external_ref: str,
                    is_synthetic: bool, sender_ref: str | None = None,
                    received_at=None) -> str:
    """Insert a message. received_at defaults to now(); pass an explicit value
    when re-shooting a prior queue item so the clone keeps the original arrival.
    """
    with get_connection() as conn, conn.cursor() as cur:
        if received_at is None:
            cur.execute(
                "insert into messages (channel, raw_payload, body, sender_ref, is_synthetic, external_ref) "
                "values (%s, %s, %s, %s, %s, %s) returning id",
                (channel, _jsonb(raw_payload), body, sender_ref, is_synthetic, external_ref),
            )
        else:
            cur.execute(
                "insert into messages (channel, raw_payload, body, sender_ref, is_synthetic, "
                "external_ref, received_at) values (%s, %s, %s, %s, %s, %s, %s) returning id",
                (channel, _jsonb(raw_payload), body, sender_ref, is_synthetic,
                 external_ref, received_at),
            )
        return str(cur.fetchone()[0])


def get_message_for_run(*, run_id: str) -> dict | None:
    """Source + arrival metadata for the message behind a queue run - used by
    /api/rerun so a re-shoot keeps channel, sender, synthetic flag, body, and
    the original received_at rather than pretending it arrived now.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select m.id as message_id, m.channel, m.raw_payload, m.body, m.sender_ref,
                   m.is_synthetic, m.external_ref, m.received_at, r.id as run_id
            from agent_runs r
            join messages m on m.id = r.message_id
            where r.id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


def create_lead(*, message_id: str, intent: str, event_date: str | None, party_size: int | None,
                 pet_flag: bool, kid_flag: bool, language: str | None) -> str:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into leads (message_id, intent, event_date, party_size, pet_flag, kid_flag, "
            "language, status) values (%s, %s, %s, %s, %s, %s, %s, 'new') returning id",
            (message_id, intent, event_date, party_size, pet_flag, kid_flag, language),
        )
        return str(cur.fetchone()[0])


def update_lead_status(*, lead_id: str, status: str) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "update leads set status = %s, updated_at = now() where id = %s",
            (status, lead_id),
        )


def set_lead_missing_fact(*, lead_id: str, missing_fact: str) -> None:
    """Written by the reporting node on escalate/dismiss - the tag the content-gap
    report counts. Not a separate aggregation job; see migrations/0001_schema.sql."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "update leads set missing_fact = %s where id = %s",
            (missing_fact, lead_id),
        )


def create_agent_run(*, message_id: str, thread_id: str) -> str:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into agent_runs (message_id, thread_id, status) "
            "values (%s, %s, 'running') returning id",
            (message_id, thread_id),
        )
        return str(cur.fetchone()[0])


def finish_agent_run(*, run_id: str, status: str, error_kind: str | None = None,
                      error_detail: str | None = None) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "update agent_runs set status = %s, ended_at = now(), error_kind = %s, "
            "error_detail = %s where id = %s",
            (status, error_kind, error_detail, run_id),
        )


def supersede_agent_run(*, run_id: str, replaced_by_run_id: str) -> None:
    """Hide a queue card that was re-shot. Keeps the row for audit, but
    list_queue excludes status='superseded' so the new run replaces it in the UI.
    """
    finish_agent_run(
        run_id=run_id,
        status="superseded",
        error_kind="superseded_by_rerun",
        error_detail=replaced_by_run_id,
    )


def get_lead(*, lead_id: str) -> dict | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select l.id, l.intent, l.party_size, l.event_date, l.status, l.crm_provider, "
            "l.crm_contact_id, m.sender_ref, "
            "(select r.id from agent_runs r where r.message_id = l.message_id "
            " order by r.started_at desc limit 1) as run_id "
            "from leads l join messages m on m.id = l.message_id where l.id = %s",
            (lead_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


def record_crm_sync(*, lead_id: str, provider: str, status: str,
                     external_id: str | None = None, error: str | None = None) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into crm_sync (lead_id, provider, status, external_id, error, attempts, last_attempt_at) "
            "values (%s, %s, %s, %s, %s, 1, now())",
            (lead_id, provider, status, external_id, error),
        )
        if status == "synced" and external_id:
            cur.execute(
                "update leads set crm_contact_id = %s, crm_provider = %s where id = %s",
                (external_id, provider, lead_id),
            )


def record_decision(*, run_id: str, lead_id: str, action: str, editor_note: str | None,
                     edited_body: dict | None, decided_by: str | None) -> str:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into decisions (run_id, lead_id, action, editor_note, edited_body, decided_by) "
            "values (%s, %s, %s, %s, %s, %s) returning id",
            (run_id, lead_id, action, editor_note, _jsonb(edited_body), decided_by),
        )
        return str(cur.fetchone()[0])


def record_sent_log(*, decision_id: str, rendered_body: str, citation_block: dict,
                     would_send_to: str) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into sent_log (decision_id, rendered_body, citation_block, would_send_to) "
            "values (%s, %s, %s, %s)",
            (decision_id, rendered_body, _jsonb(citation_block), would_send_to),
        )


def record_channel_delivery(*, decision_id: str, provider: str, status: str,
                              external_id: str | None = None, error: str | None = None,
                              to_address: str | None = None) -> None:
    """One row per WhatsApp/SMS send attempt - same audit shape as crm_sync."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into channel_deliveries "
            "(decision_id, provider, status, external_id, error, to_address, attempts, last_attempt_at) "
            "values (%s, %s, %s, %s, %s, %s, 1, now())",
            (decision_id, provider, status, external_id, error, to_address),
        )


def record_rejected_event(*, reason: str, raw_body: str | None) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into rejected_events (reason, raw_body) values (%s, %s)",
            (reason, raw_body),
        )


def is_duplicate_external_ref(*, external_ref: str) -> bool:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("select 1 from messages where external_ref = %s", (external_ref,))
        return cur.fetchone() is not None


def record_run_step(*, run_id: str, node: str, input_json: dict, output_json: dict | None,
                     error: str | None = None) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into run_steps (run_id, node, input_json, output_json, ended_at, error) "
            "values (%s, %s, %s, %s, now(), %s)",
            (run_id, node, _jsonb(input_json), _jsonb(output_json), error),
        )


def record_tool_call(*, run_id: str, tool_name: str, args_json: dict, result_json: Any,
                      ms: int, error: str | None = None) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into tool_calls (run_id, tool_name, args_json, result_json, ms, error) "
            "values (%s, %s, %s, %s, %s, %s)",
            (run_id, tool_name, _jsonb(args_json), _jsonb(result_json), ms, error),
        )


def record_policy_violation(*, run_id: str, rule_id: str, severity: str, detail: str,
                             excerpt: str | None = None) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into policy_violations (run_id, rule_id, severity, detail, excerpt) "
            "values (%s, %s, %s, %s, %s)",
            (run_id, rule_id, severity, detail, excerpt),
        )


# --- Read side: command centre queue, run timeline, reports ---

def list_queue(limit: int = 50) -> list[dict]:
    """Actionable leads first (awaiting_human / new / drafted), oldest-first
    within each band so live WhatsApp cards are not buried under finished
    fixtures. Age stays visible on each item.

    The crm_* columns are the first read anywhere in this codebase of crm_sync -
    everything before this just wrote to it. record_crm_sync() hardcodes
    attempts=1 on every insert (one row per attempt, not a running counter), so
    "how many times has this lead been synced" has to be a count(*), not a read
    of the attempts column itself.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select r.id as run_id, r.thread_id, r.status as run_status,
                   r.error_kind, r.started_at,
                   l.id as lead_id, l.intent, l.status as lead_status,
                   l.pet_flag, l.kid_flag, l.party_size,
                   l.crm_contact_id, l.crm_provider,
                   m.id as message_id, m.body, m.channel, m.is_synthetic,
                   m.external_ref, m.received_at, m.sender_ref,
                   cs.status as crm_status, cs.error as crm_error,
                   cs.external_id as crm_external_id, cs.last_attempt_at as crm_last_attempt_at,
                   coalesce(cs_count.attempts, 0) as crm_attempts,
                   cd.status as wa_status, cd.error as wa_error,
                   cd.external_id as wa_external_id, cd.to_address as wa_to_address,
                   coalesce(cd_count.attempts, 0) as wa_attempts,
                   sl.rendered_body as sent_body,
                   sl.citation_block as sent_citation_block,
                   sl.would_send_to as sent_would_send_to,
                   sl.logged_at as sent_logged_at
            from agent_runs r
            join messages m on m.id = r.message_id
            left join leads l on l.message_id = m.id
            left join lateral (
                select status, error, external_id, last_attempt_at
                from crm_sync where lead_id = l.id
                order by last_attempt_at desc nulls last limit 1
            ) cs on true
            left join lateral (
                select count(*) as attempts from crm_sync where lead_id = l.id
            ) cs_count on true
            left join lateral (
                select cd.status, cd.error, cd.external_id, cd.to_address, cd.last_attempt_at
                from channel_deliveries cd
                join decisions d on d.id = cd.decision_id
                where d.run_id = r.id
                order by cd.last_attempt_at desc nulls last limit 1
            ) cd on true
            left join lateral (
                select count(*) as attempts
                from channel_deliveries cd
                join decisions d on d.id = cd.decision_id
                where d.run_id = r.id
            ) cd_count on true
            left join lateral (
                select sl.rendered_body, sl.citation_block, sl.would_send_to, sl.logged_at
                from decisions d
                join sent_log sl on sl.decision_id = d.id
                where d.run_id = r.id
                order by sl.logged_at desc nulls last
                limit 1
            ) sl on true
            where r.status <> 'superseded'
            order by
              case
                when l.status = 'awaiting_human' then 0
                when l.status in ('new', 'drafted') then 1
                else 2
              end,
              r.started_at asc
            limit %s
            """,
            (limit,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_run_timeline(run_id: str) -> dict:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select node, input_json, output_json, started_at, ended_at, error "
            "from run_steps where run_id = %s order by started_at asc",
            (run_id,),
        )
        cols = [d.name for d in cur.description]
        steps = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur.execute(
            "select tool_name, args_json, result_json, ms, error "
            "from tool_calls where run_id = %s order by id asc",
            (run_id,),
        )
        cols = [d.name for d in cur.description]
        tool_calls = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur.execute(
            "select rule_id, severity, detail from policy_violations where run_id = %s",
            (run_id,),
        )
        cols = [d.name for d in cur.description]
        violations = [dict(zip(cols, row)) for row in cur.fetchall()]

        # Latest CRM sync attempt for the lead this run belongs to - a CRM
        # failure otherwise leaves no trace anywhere in the timeline, since
        # sync_lead_to_crm's errors never go through record_tool_call.
        cur.execute(
            """
            select cs.provider, cs.status, cs.external_id, cs.error, cs.last_attempt_at
            from crm_sync cs
            join leads l on l.id = cs.lead_id
            join messages m on m.id = l.message_id
            join agent_runs r on r.message_id = m.id
            where r.id = %s
            order by cs.last_attempt_at desc nulls last
            limit 1
            """,
            (run_id,),
        )
        cols = [d.name for d in cur.description]
        crm_row = cur.fetchone()
        crm = dict(zip(cols, crm_row)) if crm_row else None

        cur.execute(
            """
            select cd.provider, cd.status, cd.external_id, cd.error, cd.to_address,
                   cd.last_attempt_at
            from channel_deliveries cd
            join decisions d on d.id = cd.decision_id
            where d.run_id = %s
            order by cd.last_attempt_at desc nulls last
            limit 1
            """,
            (run_id,),
        )
        cols = [d.name for d in cur.description]
        wa_row = cur.fetchone()
        channel_delivery = dict(zip(cols, wa_row)) if wa_row else None

    return {
        "steps": steps,
        "tool_calls": tool_calls,
        "violations": violations,
        "crm": crm,
        "channel_delivery": channel_delivery,
    }


def content_gap_report() -> list[dict]:
    """Ranked list of the questions sunkssd.io fails to answer - counted from
    missing_fact, which reporting.py writes on every escalate/dismiss. This is
    the whole report: a query over data already collected, no separate job."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select missing_fact, count(*) as occurrences "
            "from leads where missing_fact is not null "
            "group by missing_fact order by occurrences desc"
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def demand_signal_report() -> dict:
    """Demand SevenRooms cannot see: people who asked and have not booked,
    broken down by intent, pet/kid flags, and party size."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("select intent, count(*) from leads group by intent order by 2 desc")
        by_intent = dict(cur.fetchall())

        cur.execute("select count(*) from leads where pet_flag")
        pet_count = cur.fetchone()[0]

        cur.execute("select count(*) from leads where kid_flag")
        kid_count = cur.fetchone()[0]

        cur.execute("select count(*) from leads")
        total = cur.fetchone()[0]

        cur.execute(
            "select status, count(*) from leads where status in "
            "('dismissed', 'escalated', 'awaiting_ops_quote') group by status"
        )
        unconverted_by_status = dict(cur.fetchall())

        # Bucket party sizes so the report stays scannable (solo / small / large /
        # unknown) rather than a long tail of every distinct integer.
        cur.execute(
            """
            select case
                     when party_size is null then 'unknown'
                     when party_size = 1 then '1'
                     when party_size between 2 and 5 then '2-5'
                     else '6+'
                   end as bucket,
                   count(*)
            from leads
            group by 1
            order by 1
            """
        )
        by_party_size = dict(cur.fetchall())

    return {
        "total_leads": total,
        "by_intent": by_intent,
        "pet_flag_count": pet_count,
        "kid_flag_count": kid_count,
        "by_party_size": by_party_size,
        "unconverted_by_status": unconverted_by_status,
    }


def list_running_runs() -> list[dict]:
    """agent_runs rows still marked 'running' - used by scripts/reconcile_runs.py
    to find runs orphaned by a process restart (no corresponding checkpoint
    interrupt to resume). A run parked at the human-approval interrupt is also
    'running' here and that is correct, not orphaned - see
    app.main._show_draft_slot's docstring; the script is what tells the two
    cases apart by actually asking the checkpointer."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id as run_id, thread_id, started_at from agent_runs where status = 'running'"
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_lead_id() -> str | None:
    """Most recently updated lead that has already been decided - used by the
    header Demo GoHighLevel button when no lead_id is posted (sync whatever
    is already in the queue). Restricted to approved_logged/awaiting_ops_quote
    - app.main._sync_existing_lead rejects a sync on any other status, so
    without this filter the no-args demo path would 409 on almost every call
    right after a fixture run (the most-recently-updated lead is usually the
    one still awaiting a human decision, not one ready to push to a CRM)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from leads where status in ('approved_logged', 'awaiting_ops_quote') "
            "order by updated_at desc nulls last, created_at desc limit 1"
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
