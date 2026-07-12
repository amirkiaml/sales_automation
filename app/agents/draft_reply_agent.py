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

INSTRUCTIONS = """
You are drafting a SUGGESTED reply for a human sales rep at VoiceCaptures
to review before sending - you are not sending anything yourself.

VoiceCaptures is an AI voice receptionist for home service businesses
(contractors, plumbers, electricians, HVAC) that answers missed calls,
books appointments, and texts the business owner the details. 14-day free
trial.

You'll be given the conversation history and the prospect's latest
message. Draft a reply that:
- Sounds like a real person texting - short, direct, no corporate filler.
- Under 320 characters.
- Answers their question if they asked one, using only what you're told
  about VoiceCaptures - never invent pricing or features.
- Matches the tone of the conversation so far.

Output ONLY the suggested SMS text, nothing else - no preamble, no
explanation of your reasoning.
"""

draft_reply_agent = Agent(
    name="Draft Reply Agent",
    instructions=INSTRUCTIONS,
    model=settings.AGENT_MODEL,
)


def build_draft_prompt(prospect: dict, history: list[dict], new_message: str) -> str:
    transcript = "\n".join(
        f"{'Prospect' if m['direction'] == 'inbound' else 'VoiceCaptures'}: {m['body']}"
        for m in history
    )
    return (
        f"Prospect business: {prospect.get('name')} ({prospect.get('primary_type') or 'home service business'})\n\n"
        f"Conversation so far:\n{transcript or '(this is their first reply)'}\n\n"
        f"Their new message: {new_message}\n\n"
        f"Draft the suggested reply now."
    )


async def draft_reply(prospect: dict, history: list[dict], new_message: str) -> str:
    result = await Runner.run(draft_reply_agent, build_draft_prompt(prospect, history, new_message))
    return result.final_output
