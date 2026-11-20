-- Channel delivery audit (Twilio WhatsApp sandbox sends, future SMS/etc).
-- Mirrors crm_sync: one row per attempt, status synced|failed|skipped.

create table if not exists channel_deliveries (
    id               uuid primary key default gen_random_uuid(),
    decision_id      uuid not null references decisions(id),
    provider         text not null,          -- twilio_whatsapp
    status           text not null,          -- synced | failed | skipped
    external_id      text,                   -- Twilio Message SID
    error            text,
    to_address       text,
    attempts         integer not null default 1,
    last_attempt_at  timestamptz not null default now()
);

create index if not exists idx_channel_deliveries_decision on channel_deliveries(decision_id);

alter table channel_deliveries enable row level security;
revoke all on channel_deliveries from anon, authenticated;
