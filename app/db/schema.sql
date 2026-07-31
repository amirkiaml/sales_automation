-- Run this in the Supabase SQL editor (Project > SQL Editor > New query)
-- on your new VoiceCaptures outreach project.

create extension if not exists "pgcrypto";  -- for gen_random_uuid()

-- ---------------------------------------------------------------------
-- prospects: one row per business you might text. Populated from your
-- lead-gen CSVs (name, type, neighborhood, phone, rating, hours, etc.)
-- plus outreach-tracking fields the app updates as the conversation moves.
-- ---------------------------------------------------------------------
create table if not exists prospects (
    id              uuid primary key default gen_random_uuid(),

    -- from the lead CSV
    name            text not null,
    primary_type    text,               -- e.g. "general_contractor", "plumber"
    neighborhood    text,
    address         text,
    phone           text not null,          -- E.164, e.g. +16472518320. NOT unique - see migration 006
    rating          numeric,
    review_count    integer,
    website         text,
    opening_hours   jsonb,              -- {"monday": "8:00 AM - 10:00 PM", ...}
    maps_url        text,
    source          text default 'csv_import',

    -- outreach state, updated by the app as the conversation progresses
    status          text not null default 'new'
                    check (status in (
                        'new', 'contacted', 'replied', 'interested',
                        'qualified', 'not_interested', 'opted_out',
                        'needs_human', 'closed'
                    )),
    opted_out       boolean not null default false,
    last_contacted_at  timestamptz,
    last_reply_at      timestamptz,

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_prospects_status on prospects (status);
create index if not exists idx_prospects_phone  on prospects (phone);

-- ---------------------------------------------------------------------
-- messages: full send/receive log, one row per SMS in either direction.
-- This is the conversation history the SDR agent reads before replying,
-- and the audit trail for the portfolio write-up / debugging.
-- ---------------------------------------------------------------------
create table if not exists messages (
    id              uuid primary key default gen_random_uuid(),
    prospect_id     uuid not null references prospects(id) on delete cascade,

    direction       text not null check (direction in ('outbound', 'inbound')),
    body            text not null,

    twilio_sid      text,               -- Twilio's MessageSid, for status lookups
    twilio_status   text,               -- queued/sent/delivered/failed/received
    agent_name      text,               -- which agent produced/handled this turn
                                         -- e.g. 'professional_agent', 'sdr_agent'
    metadata        jsonb default '{}'::jsonb,  -- raw webhook payload, NumMedia, etc.

    created_at      timestamptz not null default now()
);

create index if not exists idx_messages_prospect_id on messages (prospect_id);
create index if not exists idx_messages_created_at  on messages (created_at);

-- ---------------------------------------------------------------------
-- keep updated_at current on prospects automatically
-- ---------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_prospects_updated_at on prospects;
create trigger trg_prospects_updated_at
    before update on prospects
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------
-- Row Level Security: enabled with no policies. The app talks to Supabase
-- using the sb_secret_ key, which bypasses RLS entirely by design. This
-- just guarantees nothing can read/write these tables via a leaked
-- publishable key or an anonymous client.
-- ---------------------------------------------------------------------
alter table prospects enable row level security;
alter table messages  enable row level security;
