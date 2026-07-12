-- Run in Supabase SQL Editor after 003_autopilot.sql.

-- Bug fix: overlapping poll cycles (e.g. from frequent auto-refresh) can
-- both see the same Twilio message as "not yet logged" and both insert
-- it, producing duplicate rows a fraction of a second apart. A unique
-- constraint on twilio_sid makes the second insert fail cleanly instead
-- of creating a duplicate - handled gracefully in app/db/client.py.
create unique index if not exists idx_messages_twilio_sid_unique
    on messages (twilio_sid) where twilio_sid is not null;

-- Bug fix: Twilio never deletes anything on its end. Deleting a
-- contact's history from Supabase alone left the poller's cursor with
-- nothing to anchor to, so the next poll fell back to a lookback window
-- and re-imported everything Twilio still had - the "deleted" messages
-- came right back. This timestamp marks "don't look at anything from
-- Twilio before this point" regardless of what's in our own messages
-- table, so a cleared conversation actually stays cleared.
alter table prospects
    add column if not exists history_cleared_at timestamptz;
