-- Run in Supabase SQL Editor after 004_message_dedup_and_history_clear.sql.

-- Stores the phone number directly on each message row, not just via the
-- prospect_id join. Makes the message log independently auditable - you
-- can see who a message was with/from without trusting the prospect
-- linkage, which matters given the history-clearing bugs surfaced
-- recently.
alter table messages
    add column if not exists phone text;

-- Backfill existing rows from their linked prospect.
update messages m
set phone = p.phone
from prospects p
where m.prospect_id = p.id and m.phone is null;

create index if not exists idx_messages_phone on messages (phone);
