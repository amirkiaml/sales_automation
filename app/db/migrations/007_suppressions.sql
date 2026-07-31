-- Run in Supabase SQL Editor after 006_allow_duplicate_phones.sql.
--
-- Added alongside "delete a prospect entirely". Deleting a prospect
-- cascades to its messages, which is the intent - but if that prospect
-- had opted out, the deletion also destroys the only record that they
-- ever did. Re-import the same lead CSV a month later and the system
-- would message someone who told it to stop. Under CASL an opt-out has
-- to be honoured indefinitely, so the record has to outlive the row that
-- happened to carry it.
--
-- This table is therefore append-only and deliberately NOT foreign-keyed
-- to prospects: it survives prospect deletion, re-import, and phone
-- number reassignment. It is keyed on the number itself because that is
-- the thing the obligation attaches to.

create table if not exists suppressions (
    phone       text primary key,          -- E.164
    reason      text not null,             -- 'stop_keyword' | 'triage_opt_out' | 'manual'
    prospect_id uuid,                      -- who it came from, if known. No FK on
                                           -- purpose: must survive their deletion.
    note        text,
    created_at  timestamptz not null default now()
);

create index if not exists idx_suppressions_created on suppressions (created_at desc);

-- Backfill from prospects already marked opted_out, so turning this on
-- doesn't silently start from an empty list.
insert into suppressions (phone, reason, prospect_id, note)
select phone, 'backfill', id, 'migrated from prospects.opted_out'
from prospects
where opted_out = true
on conflict (phone) do nothing;
