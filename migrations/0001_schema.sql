-- Weekend Enquiry Desk schema. No pgvector (see README: "Why there is no pgvector").
-- Run against the Supabase SESSION POOLER (port 5432), not the transaction pooler -
-- DDL and the LangGraph PostgresSaver checkpointer both need session semantics.

create table if not exists chunks (
    id            text primary key,
    section_id    text not null,
    heading       text,
    text          text not null,
    url           text not null,
    accessed_at   timestamptz not null,
    source        text not null
);

create table if not exists official_links (
    key    text primary key,
    label  text not null,
    url    text not null,
    note   text
);

create table if not exists messages (
    id            uuid primary key default gen_random_uuid(),
    channel       text not null,
    raw_payload   jsonb not null,
    body          text not null,
    sender_ref    text,
    received_at   timestamptz not null default now(),
    is_synthetic  boolean not null default false,
    external_ref  text not null unique
);

create table if not exists leads (
    id            uuid primary key default gen_random_uuid(),
    message_id    uuid not null references messages(id),
    intent        text not null,
    event_date    date,
    party_size    integer,
    pet_flag      boolean not null default false,
    kid_flag      boolean not null default false,
    language      text,
    status        text not null default 'new',
    crm_contact_id text,
    crm_provider  text not null default 'none',
    missing_fact  text,  -- written by the reporting node on escalate/dismiss; what the
                          -- corpus lacked. The content-gap report groups and counts this.
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create table if not exists agent_runs (
    id            uuid primary key default gen_random_uuid(),
    message_id    uuid not null references messages(id),
    thread_id     text not null unique,
    status        text not null default 'running',
    started_at    timestamptz not null default now(),
    ended_at      timestamptz,
    error_kind    text,
    error_detail  text
);

create table if not exists run_steps (
    id          uuid primary key default gen_random_uuid(),
    run_id      uuid not null references agent_runs(id),
    node        text not null,
    input_json  jsonb,
    output_json jsonb,
    started_at  timestamptz not null default now(),
    ended_at    timestamptz,
    error       text
);

create table if not exists tool_calls (
    id          uuid primary key default gen_random_uuid(),
    run_id      uuid not null references agent_runs(id),
    step_id     uuid references run_steps(id),
    tool_name   text not null,
    args_json   jsonb,
    result_json jsonb,
    ms          integer,
    error       text
);

create table if not exists policy_violations (
    id        uuid primary key default gen_random_uuid(),
    run_id    uuid not null references agent_runs(id),
    rule_id   text not null,
    severity  text not null,
    detail    text not null,
    excerpt   text
);

create table if not exists decisions (
    id           uuid primary key default gen_random_uuid(),
    run_id       uuid not null references agent_runs(id),
    lead_id      uuid not null references leads(id),
    action       text not null,          -- approve | edit | dismiss
    editor_note  text,
    edited_body  jsonb,
    decided_at   timestamptz not null default now(),
    decided_by   text
);

create table if not exists sent_log (
    id               uuid primary key default gen_random_uuid(),
    decision_id      uuid not null references decisions(id),
    rendered_body    text not null,
    citation_block   jsonb not null,
    would_send_to    text not null,       -- named this way deliberately: there is no sender
    logged_at        timestamptz not null default now()
);

create table if not exists crm_sync (
    id               uuid primary key default gen_random_uuid(),
    lead_id          uuid not null references leads(id),
    provider         text not null,
    status           text not null,       -- pending | synced | failed
    external_id      text,
    error            text,
    attempts         integer not null default 0,
    last_attempt_at  timestamptz
);

create table if not exists rejected_events (
    id           uuid primary key default gen_random_uuid(),
    reason       text not null,
    raw_body     text,
    received_at  timestamptz not null default now()
);

create index if not exists idx_leads_status on leads(status);
create index if not exists idx_leads_message on leads(message_id);
create index if not exists idx_agent_runs_message on agent_runs(message_id);
create index if not exists idx_run_steps_run on run_steps(run_id);
create index if not exists idx_tool_calls_run on tool_calls(run_id);
create index if not exists idx_policy_violations_run on policy_violations(run_id);
create index if not exists idx_decisions_run on decisions(run_id);
create index if not exists idx_crm_sync_lead on crm_sync(lead_id);
