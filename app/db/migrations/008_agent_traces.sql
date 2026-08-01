-- Run in the Supabase SQL Editor. (Files in this folder are a record of
-- what to run - dropping one here does nothing on its own.)
--
-- Stores what actually happened on each agent run: which guardrails fired,
-- what the KB returned, which agent drafted, whether it sent.
--
-- Needed because the interesting events do not all produce a message. A
-- reply blocked by the scope guardrail leaves no outbound row, so the
-- messages table shows silence and cannot distinguish "the guardrail
-- caught a pricing question" from "nothing ran". Those are the runs worth
-- looking at.
--
-- Deliberately denormalised into a single jsonb array rather than a row
-- per step: a trace is read whole or not at all, and one row per run keeps
-- the write to a single insert on the inbound path.

create table if not exists agent_traces (
    id           uuid primary key default gen_random_uuid(),
    prospect_id  uuid references prospects(id) on delete cascade,
    entry_point  text not null,           -- 'autopilot' | 'generate' | 'cold_outreach'
    trigger_text text,                    -- the inbound message that started it
    outcome      text,                    -- 'sent' | 'escalated' | 'blocked_scope' | ...
    steps        jsonb not null default '[]'::jsonb,
    duration_ms  integer,
    created_at   timestamptz not null default now()
);

create index if not exists idx_agent_traces_prospect
    on agent_traces (prospect_id, created_at desc);
