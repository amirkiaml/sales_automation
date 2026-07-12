-- Run in Supabase SQL Editor after 002_pending_reply.sql.
-- Per-contact opt-in for autonomous replying. Off by default - a
-- prospect only gets autopilot if you explicitly turn it on for them
-- from their detail page in /admin.

alter table prospects
    add column if not exists autopilot boolean not null default false;

comment on column prospects.autopilot is
    'When true, the SDR agent replies and sends automatically for this
     contact instead of queuing a suggestion for human review. Always
     off by default. Automatically turned back off whenever the agent
     escalates to a human (flag_human_handoff_tool) or a STOP keyword is
     received - compliance handling is never delegated to agent judgment.';
