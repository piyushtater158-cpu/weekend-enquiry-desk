-- Deny-all RLS on every table. The app connects with a direct Postgres connection
-- string (session pooler) using the postgres role, never the anon/publishable key,
-- and the Supabase Data API should also be disabled in Project Settings - this
-- migration is the second, structural layer of that same decision (P7): Supabase
-- auto-publishes the public schema over PostgREST, reachable with the publishable
-- key, so a table without RLS is readable/writable by any role with a grant on it.
--
-- "Deny-all" here means: enable RLS, grant no policy. No role (anon, authenticated)
-- gets a policy, so every row is inaccessible through PostgREST even if the Data
-- API is later re-enabled by mistake. The app's own direct connection bypasses RLS
-- entirely (it does not go through PostgREST), so this costs the app nothing.

alter table chunks            enable row level security;
alter table official_links    enable row level security;
alter table messages          enable row level security;
alter table leads             enable row level security;
alter table agent_runs        enable row level security;
alter table run_steps         enable row level security;
alter table tool_calls        enable row level security;
alter table policy_violations enable row level security;
alter table decisions         enable row level security;
alter table sent_log          enable row level security;
alter table crm_sync          enable row level security;
alter table rejected_events   enable row level security;

revoke all on chunks            from anon, authenticated;
revoke all on official_links    from anon, authenticated;
revoke all on messages          from anon, authenticated;
revoke all on leads             from anon, authenticated;
revoke all on agent_runs        from anon, authenticated;
revoke all on run_steps         from anon, authenticated;
revoke all on tool_calls        from anon, authenticated;
revoke all on policy_violations from anon, authenticated;
revoke all on decisions         from anon, authenticated;
revoke all on sent_log          from anon, authenticated;
revoke all on crm_sync          from anon, authenticated;
revoke all on rejected_events   from anon, authenticated;
