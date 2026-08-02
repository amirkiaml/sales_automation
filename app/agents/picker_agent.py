"""
Picker agent - reviews the three drafted SMS variants and picks a winner.

Unlike the lab's picker (which returns the full winning email text), this
one returns just the winning persona's label via structured output. The
code then looks up the original, unmodified draft by that label. Two
reasons:
  1. Guarantees the sent message is exactly what the drafting agent wrote
     (no risk of the picker subtly rewriting it while "selecting").
  2. Gives us a clean field (winning persona) to track reply rates by
     persona over time - turns the one-shot picker into an ongoing
     experiment, per the portfolio feature list.
"""
from typing import Literal

from pydantic import BaseModel

from agents import Agent, Runner

from app.config import settings


class PickerDecision(BaseModel):
    winner: Literal["professional", "witty", "executive"]
    reason: str  # one short phrase, for logging/debugging only


INSTRUCTIONS = """
You are evaluating three cold outreach SMS drafts written for the same
business and the same personalization angle, each in a different style:
professional, witty, executive.

Imagine you are the owner of a small home service business receiving this
text from a stranger. Pick the one you'd be most likely to actually reply
to, given real SMS behavior (short attention span, skeptical of cold
outreach, values directness).

Return the winning style label and a one-phrase reason.
"""

picker_agent = Agent(
    name="SMS Picker",
    instructions=INSTRUCTIONS,
    model=settings.DRAFTING_MODEL,
    output_type=PickerDecision,
)


async def pick_best(drafts: dict[str, str]) -> PickerDecision:
    """drafts: {"professional": "...", "witty": "...", "executive": "..."}"""
    prompt = "\n\n".join(
        f'[{label}]\n{text}' for label, text in drafts.items()
    )
    result = await Runner.run(picker_agent, prompt)
    return result.final_output
