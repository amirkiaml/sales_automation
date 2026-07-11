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
- [ ] Inbound webhook server (FastAPI)
- [ ] Triage agent
- [ ] Conversational SDR agent (interactive mode)
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
