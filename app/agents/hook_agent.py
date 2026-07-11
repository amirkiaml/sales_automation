"""
Hook agent - runs before the drafting agents. Reads a prospect's business
data (hours, rating, review count, type) and picks the single sharpest,
factually-grounded angle to personalize the outreach around.

Keeping this as its own step (rather than letting each drafting agent
guess at personalization independently) means all three drafts anchor on
the same fact, and the picker agent is judging writing style, not
comparing three different personalization strategies against each other.
"""
from pydantic import BaseModel

from agents import Agent, Runner

from app.config import settings


class Hook(BaseModel):
    angle: str          # one sentence: the personalization strategy to use
    supporting_fact: str  # the specific fact from the data that grounds it


INSTRUCTIONS = """
You are a research assistant for VoiceCaptures, a company that sells AI
voice receptionists to home service businesses (contractors, plumbers,
electricians, HVAC, etc.) so they stop missing calls that turn into jobs.

Given structured data about a business (name, type, neighborhood, rating,
review count, opening hours), pick the single sharpest, most specific
personalization angle for a cold SMS. Ground it in one concrete fact from
the data - never invent details that aren't provided.

Good angles look for:
- Hours that end early or late (missed after-hours calls)
- High rating but low review count (missed opportunities to convert calls
  into reviews/jobs)
- No website, or a generic-sounding business type with high call volume
  likely (plumbers, contractors, HVAC - urgent, time-sensitive calls)

If nothing in the data stands out, fall back to a general angle about
missed calls costing jobs for their business type - do not force a weak
personalization.

Output the angle as one plain sentence, and the specific fact that
supports it (quote or closely paraphrase the data field it came from).
"""

hook_agent = Agent(
    name="Hook Agent",
    instructions=INSTRUCTIONS,
    model=settings.AGENT_MODEL,
    output_type=Hook,
)


def _format_prospect_for_hook(prospect: dict) -> str:
    hours = prospect.get("opening_hours") or {}
    hours_str = "; ".join(f"{day}: {times}" for day, times in hours.items()) or "not available"

    return (
        f"Business name: {prospect.get('name')}\n"
        f"Type: {prospect.get('primary_type') or 'unknown'}\n"
        f"Neighborhood: {prospect.get('neighborhood') or 'unknown'}\n"
        f"Rating: {prospect.get('rating') or 'unknown'}\n"
        f"Review count: {prospect.get('review_count') or 'unknown'}\n"
        f"Has website: {'yes' if prospect.get('website') else 'no'}\n"
        f"Opening hours: {hours_str}"
    )


async def generate_hook(prospect: dict) -> Hook:
    result = await Runner.run(hook_agent, _format_prospect_for_hook(prospect))
    return result.final_output
