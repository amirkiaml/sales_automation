# VoiceCaptures SMS Outreach Agent

Agentic SMS sales outreach for VoiceCaptures, built on the OpenAI Agents SDK
and Twilio. Sends bulk cold outreach via a deterministic multi-agent
drafting/picker pipeline, then hands live conversations to a stateful
conversational SDR agent that responds to inbound replies in real time.

> Architecture, setup, and design rationale documented below as each
> piece is built.

## Status

- [x] Project scaffolding
- [x] Database schema (Supabase)
- [x] Cold outreach pipeline (drafting agents + picker + Twilio send tool)
- [x] Inbound ingestion (polling, not webhook - see note below) + triage agent
- [x] Draft-reply agent + Pushover notification + human approval CLI
- [x] Public demo page (/demo) for recruiters to try both pipelines directly
- [x] Admin console for the real system (/admin) - dashboard, review
  queue, conversation threads, manual send, live poll trigger

## Design note: admin console vs public demo

`/demo` is the safe, public-facing preview (no Twilio access, request
queue only). `/admin` is the opposite: a password-protected operator
console showing real prospects and real conversation data, built mainly
so the actual workflow (poll → triage → draft → human review → send) is
screen-recordable for a portfolio video instead of living only in a
terminal. Same `DEMO_ADMIN_PASSWORD`, session-based login via
`SESSION_SECRET_KEY` (set this explicitly in production, or admin
sessions won't survive a restart). `app/poll_inbound.py` and
`app/review_pending.py` still work as CLI tools if you'd rather use those
day to day - `/admin` is an additional interface, not a replacement.

## Design note: minimal leads

Only `name` and `phone` are required anywhere in the pipeline - the CSV
importer, the demo page, and the hook agent all already degrade
gracefully to a generic-but-relevant message when other fields (hours,
rating, review count) are missing. No special-casing needed; this was
built into the hook agent's instructions from the start.

## Workflow diagrams

**Cold outreach (3 drafting agents + picker)** — what tool 1 on the demo page runs:

![Cold outreach workflow](app/static/images/cold-outreach-flow.svg)

**Conversational agent (guided chat + simulated handoff)** — what tool 2 on the demo page runs:

![Conversational agent workflow](app/static/images/conversation-flow.svg)

**The real system** — what actually ships a reply to a live prospect (not simulated; shown in the `/admin` console, not yet embedded in the demo page - planned):

![Real system inbound reply workflow](app/static/images/real-pipeline-flow.svg)

## Bug fix log

- **Website field false negative** (found via demo testing): the hook
  agent's prospect formatter treated a missing `website` field as
  confirmed "no website," which is wrong whenever a field is simply
  absent (demo submissions) or unpopulated (blank CSV column) rather
  than a verified fact. Fixed to say "unknown" like every other optional
  field. Affected both the demo page and any real lead missing a website
  in the CSV.
- **Demo panel clarity**: the 3-drafts-and-picker feature only exists in
  the cold outreach tool (by design - a live reply needs one answer, not
  three competing styles), but this wasn't obvious from the UI. Panel
  headings and descriptions now say so explicitly.
- **Chat continuity**: submitting a message in the conversational tool
  reset the page back to the top, making it feel like a form rather than
  a chat. Fixed with a small scroll-and-refocus script - after a reply,
  the page jumps back to the chat panel and refocuses the input.
- **Poller/approval visibility**: `poll_inbound.py` and `review_pending.py`
  are command-line tools invisible to anyone browsing the site. Added a
  third demo panel that explains the real pipeline step by step and shows
  live, PII-free aggregate counts (`get_demo_stats()` in `client.py`) as
  proof it's a running system, not just the two interactive previews.

- **Guided chat + simulated handoff**: the conversational tool now starts
  with a real generated cold-open message (button, after entering just
  name/type), never re-asks for business info mid-thread (carried via
  hidden fields), and runs the real triage agent on each reply to detect
  a "hot lead" - when it does, the input disappears and a note explains
  that a human would take over here in the real system, mirroring the
  actual handoff design without ever risking a real send.
- **Picker transparency**: cold outreach results now show which named
  agent wrote each draft ("Written by: Witty SMS Agent") and the picker
  agent's actual reason for its choice, not just which one won.
- **Required-field styling**: red asterisk instead of a muted "required"
  label, standard convention.

## Design note: demo page

`/demo` reuses the exact same pipeline functions the CLI tools call
(`run_cold_outreach_for_prospect`, `draft_reply`) - nothing is
reimplemented for the browser version. The conversational tool is a real
multi-turn chat (history carried in a hidden form field between
requests), so a visitor can hold an actual back-and-forth with the agent.

Critically: `app/routes/demo.py` never imports or calls Twilio. No code
path a site visitor can reach is able to send a real SMS. If someone
wants to see a real text, their request goes into the exact same
pending_reply/Pushover approval queue real prospect replies use - a
human decides whether to actually send it via `app/review_pending.py`.
Review-requests are rate-limited per visitor IP (5/hour, in-memory) to
prevent notification spam if the link gets shared further than intended.

## Design note: polling instead of webhook

The Twilio number used for this project already has a webhook owned by
another app. Rather than fight over that single URL slot, inbound
messages are picked up by periodically polling Twilio's Messages API
(`app/poll_inbound.py`) instead of a push-based webhook. Trade-off:
near-real-time instead of instant, in exchange for zero risk to the
number's existing setup and zero webhook config changes.

A FastAPI webhook implementation still exists in `app/routes/webhook.py` /
`app/main.py` for a dedicated-number setup later - not currently wired
into the active flow.

## Design note: human-in-the-loop replies

Cold outreach (phase 1) is fully autonomous - low risk, generic message,
runs at volume. Live replies to a real person go through an approval gate
instead: an agent drafts a suggested reply with no ability to send it,
you get a Pushover notification, and `app/review_pending.py` is where you
approve, edit, or skip before anything reaches the prospect.
- [ ] Compliance / opt-out handling
- [ ] Deployment (Railway)

## Project layout

```
app/
  agents/     agent definitions (drafters, picker, triage, SDR)
  tools/      function_tools (send_sms, db read/write)
  db/         Supabase client + schema
  routes/     FastAPI webhook routes
  config.py   env var loading
tests/
docs/
```
