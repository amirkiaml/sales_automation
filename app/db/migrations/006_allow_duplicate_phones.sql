-- Run in Supabase SQL Editor after 005_message_phone.sql.
--
-- Allows several prospects to share a phone number, so any client can be
-- pointed at the operator's own number for end-to-end testing without
-- first having to clear it off whichever prospect held it before.
--
-- What this costs, stated plainly because it is a real tradeoff:
-- inbound SMS is routed to a prospect purely by the sender's number
-- (webhook.py, poll_inbound.py -> get_prospect_by_phone). With duplicates
-- allowed, that lookup is ambiguous. It is resolved deterministically in
-- get_prospect_by_phone by taking the most recently updated match, which
-- means "the prospect you most recently pointed at this number wins" -
-- exactly the testing workflow this migration exists for. It is still a
-- weaker guarantee than uniqueness gave, and production lead data should
-- not deliberately contain duplicate numbers.
--
-- ON CONFLICT (phone) stops working once the unique index is gone, so
-- upsert_prospect() no longer uses it - see app/db/client.py.

alter table prospects drop constraint if exists prospects_phone_key;

-- Keep a non-unique index: every inbound message does a lookup on this
-- column, so dropping the unique constraint would otherwise cost a
-- sequential scan per message.
create index if not exists idx_prospects_phone on prospects (phone);

-- Lookup resolves ties by updated_at, so index that path too.
create index if not exists idx_prospects_phone_updated on prospects (phone, updated_at desc);
