-- Run in Supabase SQL Editor after schema.sql.
-- Adds the approval-gate fields: a suggested reply sits here until you
-- review and approve it - nothing sends automatically.

alter table prospects
    add column if not exists pending_reply text,
    add column if not exists pending_reply_context text;  -- the inbound message it's responding to, shown alongside it for review

comment on column prospects.pending_reply is
    'Agent-drafted SMS reply awaiting human approval. Cleared once sent or skipped.';
