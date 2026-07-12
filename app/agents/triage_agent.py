"""
Triage agent - the router for inbound replies. Runs on every inbound SMS
that isn't already caught by the hard-coded opt-out keyword check (see
webhook.py). Cheap/fast model on purpose: this runs on every single
inbound message, most of which are short and low-stakes to classify.
"""
from typing import Literal

from pydantic import BaseModel

from agents import Agent, Runner

from app.config import settings


class Triage(BaseModel):
    intent: Literal[
        "interested", "question", "not_interested", "opt_out", "hot_lead", "unclear"
    ]
    confidence: float  # 0-1
    reason: str        # one short phrase, for logs/debugging only


INSTRUCTIONS = """
You are triaging inbound SMS replies to a cold outreach campaign for
VoiceCaptures, an AI receptionist product for home service businesses.

Classify the reply's intent:
- interested: positive response, wants to learn more, said something like
  "yes" or "tell me more"
- question: asking something specific (pricing, how it works, etc.)
  before deciding
- not_interested: declining, "no thanks", "not right now", but not using
  a formal opt-out keyword
- opt_out: explicitly wants to stop being contacted (this is a fallback -
  most STOP-style keywords are already caught before you see them, so
  only use this for phrased-differently opt-out requests like "please
  remove me" or "stop calling")
- hot_lead: strong buying signal - asking to book a call, wants pricing
  to move forward, says something like "yes let's do this"
- unclear: ambiguous, sarcastic, or you're not confident what they mean

You'll also be given recent conversation history for context - the same
"not interested" can mean different things cold vs. mid-conversation.

If your confidence is below 0.6, use "unclear" regardless of your best
guess - it's safer to route to a human-reviewed path than to guess wrong
on an opt-out or hot lead.
"""

triage_agent = Agent(
    name="Triage Agent",
    instructions=INSTRUCTIONS,
    model=settings.TRIAGE_MODEL,
    output_type=Triage,
)


def _format_for_triage(history: list[dict], new_message: str) -> str:
    transcript = "\n".join(
        f"{'Prospect' if m['direction'] == 'inbound' else 'VoiceCaptures'}: {m['body']}"
        for m in history
    )
    return (
        f"Conversation so far:\n{transcript or '(no prior messages)'}\n\n"
        f"New inbound reply to classify:\n{new_message}"
    )


async def classify(history: list[dict], new_message: str) -> Triage:
    result = await Runner.run(triage_agent, _format_for_triage(history, new_message))
    return result.final_output
