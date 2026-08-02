# Notebooks

Working through how each piece of this system actually works, mostly by
building it from nothing and watching the naive version break.

Every cell runs offline. No API key, no database — the model calls are
faked with plain functions so the control flow is visible without spending
anything. The last cell of each notebook imports the real module and
checks the toy version agrees with it.

Set the kernel to the project venv (`sales_automation`), then run top to
bottom. Order matters within a notebook; the failures are the point.

## What's here

**`00_current_behaviour.ipynb`** — not a lesson. Runs against the real KB
and the real modules and prints what they currently do. Useful after
editing `voicecaptures.yaml`, or when something in the console looks wrong
and I want to check the routing layer in isolation.

**`01_retrieval_from_scratch.ipynb`** — how the knowledge base finds
things. Starts with a dict and exact-match lookup, breaks it, and rebuilds
through five versions. Shows the substring bug live (`'fee'` inside
`'feet'`, `'cost'` inside `'costume'`) and the apostrophe bug that let an
off-topic weather question retrieve the product overview. Ends with the
phone normalizer, which is the other pure function here and broke in a
similar way. Covers `app/kb/loader.py` and `app/phone.py`.

**`02_guardrails_from_scratch.ipynb`** — the checks either side of the
agent. Opens with a fake agent inventing a monthly price out of nothing,
then builds the keyword layer, shows exactly which paraphrases it misses,
adds the classifier, then the grounding check. Contains the cell that
changed the architecture: the guardrail correctly rejects a draft that
Twilio has already delivered. Covers `app/agents/guardrails.py`.

**`03_wiring_it_together.ipynb`** — order of operations. Why the STOP
check runs before any model, why opt-outs live in their own table rather
than on the prospect row, what each guardrail failure should do, and why
runs that produce no message are the ones worth recording. Covers
`app/agents/autopilot.py`, `app/observability.py`, migrations 007 and 008.

**`04_cold_outreach_from_scratch.ipynb`** — the other half of the system.
Why three parallel model calls beat one call asking for three variants,
why the drafting agents share an angle, and why the picker returns a label
instead of the winning text. Covers `hook_agent.py`,
`drafting_agents.py`, `picker_agent.py`, `cold_outreach.py`.

**`05_measuring_it.ipynb`** — three dry runs over the same 50 leads and
what they found. GSM-7 segment counting and why a character count misses
it, the 16x cost reduction, the sanitizer. Ends with the finding no
metric caught: the agent was writing true, well-grounded, insulting
messages, and every number on that run looked healthy. Covers
`app/measure_cold.py` and `sanitize_for_sms`.

## What isn't covered

`app/db/client.py`, the routes, the templates, `main.py`, `config.py` and
the CLI scripts. Ordinary CRUD, FastAPI and env loading — nothing to
unpack that reading the file wouldn't tell you faster.

## A note on what these are

Reconstructions, not a record. The bugs are real and the fixes are the
real fixes, but I've put each failure where it teaches best rather than
where it happened. The `from_scratch` versions are also simplified past
the real logic on purpose — notebook 3's `check_scope` is three `if`
statements. Don't mistake the fake for the module.
