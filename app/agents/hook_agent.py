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
- A generic-sounding business type with likely high call volume (urgent,
  time-sensitive calls) - plumbers, contractors, HVAC, electricians

NEVER use as an angle - these are true facts that read as insults, and a
cold text from a stranger criticising someone's business gets a grudge,
not a reply:
- A rating below 4.0, or any rating framed as a problem. "Your 2.7 rating
  shows missed opportunities" is not outreach, it is an insult with a
  sales pitch attached.
- A low review count framed as a deficiency on its own. High-rating-but-
  few-reviews is fine because the compliment carries it; "only 1 review"
  as the headline is not.
- Anything that implies the business is failing, badly run, or unpopular.

The test: read the angle back as if you were the owner, on a Tuesday,
from a number you don't recognise. If any part of it stings, pick a
different angle. Falling back to the generic missed-calls angle is always
better than a personalized insult.

Only use "no website" as an angle if the data explicitly confirms it -
never treat a missing or unclear website field as proof they don't have
one; that's a data gap, not a fact about the business.

If nothing in the data stands out, fall back to a general angle about
missed calls costing jobs for their business type - do not force a weak
personalization.

Output the angle as one plain sentence, and the specific fact that
supports it (quote or closely paraphrase the data field it came from).
"""

hook_agent = Agent(
    name="Hook Agent",
    instructions=INSTRUCTIONS,
    model=settings.DRAFTING_MODEL,
    output_type=Hook,
)


# Ratings at or above this are safe to build an angle on. Below it, the
# field is withheld entirely rather than passed with an instruction not to
# use it.
#
# The instruction version was measured at 49/50 - one message referenced a
# 3.9 rating and described it as "impressive", the model straining to obey
# the tone rule while breaking the content rule. The failure mode is a
# cold text telling a stranger their business looks bad, which is not a
# 2%-of-the-time kind of mistake to accept.
#
# Third time in this project that a rule had to move from the prompt into
# code, after the compliance footer and the curly apostrophes. An
# instruction followed 98% of the time is not a guarantee.
SAFE_RATING_FLOOR = 4.0


def _rating_for_prompt(prospect: dict) -> str:
    """The rating, or 'unknown' when it is low enough to be an insult.

    Withholding rather than filtering downstream: the agent cannot
    reference a number it was never given, which makes the rule
    structural instead of aspirational.

    'unknown' rather than omitting the line, because the agent already
    handles unknown fields gracefully - it falls through to a generic
    angle - and a missing key would be a new shape to reason about.
    """
    rating = prospect.get("rating")
    if rating is None:
        return "unknown"
    try:
        if float(rating) < SAFE_RATING_FLOOR:
            return "unknown"
    except (TypeError, ValueError):
        return "unknown"
    return str(rating)


def _format_prospect_for_hook(prospect: dict) -> str:
    hours = prospect.get("opening_hours") or {}
    hours_str = "; ".join(f"{day}: {times}" for day, times in hours.items()) or "not available"

    return (
        f"Business name: {prospect.get('name')}\n"
        f"Type: {prospect.get('primary_type') or 'unknown'}\n"
        f"Neighborhood: {prospect.get('neighborhood') or 'unknown'}\n"
        f"Rating: {_rating_for_prompt(prospect)}\n"
        f"Review count: {prospect.get('review_count') or 'unknown'}\n"
        f"Website: {prospect.get('website') or 'unknown - do not assume they lack one'}\n"
        f"Opening hours: {hours_str}"
    )


async def generate_hook(prospect: dict) -> Hook:
    result = await Runner.run(hook_agent, _format_prospect_for_hook(prospect))
    return result.final_output
