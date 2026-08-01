"""
Draft-reply agent - the human-in-the-loop version of the SDR agent. Same
job (read conversation history, propose a good next reply) but with no
tools at all: it can't send anything, flag anything, or take any action.
It just returns text. A human approves, edits, or skips before anything
reaches the prospect.

This is a deliberate design choice, not a limitation: cold outreach (phase
1) stays fully autonomous because the risk of a bad generic message is
low and it runs at volume. Live back-and-forth with a real person carries
more risk and more judgment calls (pricing questions, tone, when to
actually escalate) - so that stays human-approved.
"""
from agents import Agent, Runner

from app.config import settings
from app.kb.loader import Entry, format_for_prompt, match_restricted, search

INSTRUCTIONS = """
You are drafting a SUGGESTED reply for a human sales rep at VoiceCaptures
to review before sending - you are not sending anything yourself.

VoiceCaptures is an AI voice receptionist for home service businesses
(contractors, plumbers, electricians, HVAC).

You'll be given knowledge base entries, the conversation history, and the
prospect's latest message.

[Grounding Rule - MANDATORY]
State facts ONLY from the knowledge base entries provided. Rephrase
freely; do not add. No number, timeframe, guarantee, integration or
capability that isn't in the entries. If the entries don't cover their
question, say so in the draft - write something like "[NOT IN KB: they
asked about X]" so the human reviewing knows to fill it in, rather than
inventing an answer that reads as confident and ships because it looked
fine at a glance.

Draft a reply that:
- Sounds like a real person texting - short, direct, no corporate filler.
- Under 320 characters.
- Answers their question if the entries cover it.
- Matches the tone of the conversation so far.

Output ONLY the suggested SMS text, nothing else - no preamble, no
explanation of your reasoning.
"""

draft_reply_agent = Agent(
    name="Draft Reply Agent",
    instructions=INSTRUCTIONS,
    model=settings.AGENT_MODEL,
)


def build_draft_prompt(
    prospect: dict, history: list[dict], new_message: str, kb_entries: list[Entry] | None = None
) -> str:
    transcript = "\n".join(
        f"{'Prospect' if m['direction'] == 'inbound' else 'VoiceCaptures'}: {m['body']}"
        for m in history
    )
    return (
        f"Prospect business: {prospect.get('name')} ({prospect.get('primary_type') or 'home service business'})\n\n"
        f"Knowledge base entries retrieved for their message:\n{format_for_prompt(kb_entries or [])}\n\n"
        f"Conversation so far:\n{transcript or '(this is their first reply)'}\n\n"
        f"Their new message: {new_message}\n\n"
        f"Draft the suggested reply now."
    )


async def draft_reply(prospect: dict, history: list[dict], new_message: str) -> str:
    """Draft a reply for human review, grounded in the knowledge base.

    Uses the same KB as the autopilot agent. Without this the review path
    would be the LESS constrained of the two, which is backwards - most
    messages go through review, so it is where an invented fact is most
    likely to actually reach someone.
    """
    entries = search(new_message)
    result = await Runner.run(
        draft_reply_agent, build_draft_prompt(prospect, history, new_message, kb_entries=entries)
    )
    return result.final_output
