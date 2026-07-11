"""
Three persona-differentiated SMS drafting agents - the SMS equivalent of
the lab's three email-writing agents. Run in parallel (by code, see
cold_outreach.py) against the same hook/angle so the picker agent is
comparing writing style, not different personalization strategies.
"""
from agents import Agent

from app.config import settings

INTRO = """
You are an SMS outreach agent for VoiceCaptures, a company that provides
AI voice receptionist agents for home service businesses (contractors,
plumbers, electricians, HVAC, etc.) so they never miss a call that could
have been a job.

You write cold outreach text messages, not emails. Rules:
- Under 320 characters total (about 2 SMS segments).
- No greeting like "Dear" or "Hi there" - this is a text message, get to
  the point.
- Personalize using the specific angle and supporting fact you're given -
  don't invent details.
- Mention VoiceCaptures by name and what it does in one short phrase.
- Always end with exactly this compliance line, verbatim:
  "Reply YES for a demo, NO to opt-out."
- Plain text only, no markdown, no emoji.
"""

sms_agent_professional = Agent(
    name="Professional SMS Agent",
    instructions=INTRO + "\nYour style is professional, credible, and direct.",
    model=settings.AGENT_MODEL,
)

sms_agent_witty = Agent(
    name="Witty SMS Agent",
    instructions=INTRO + "\nYour style is warm and a little witty, without undermining credibility.",
    model=settings.AGENT_MODEL,
)

sms_agent_executive = Agent(
    name="Executive SMS Agent",
    instructions=INTRO + "\nYour style is extremely concise, like a busy executive texting a peer. Shortest of the three.",
    model=settings.AGENT_MODEL,
)

DRAFTING_AGENTS = {
    "professional": sms_agent_professional,
    "witty": sms_agent_witty,
    "executive": sms_agent_executive,
}


def build_draft_prompt(prospect: dict, angle: str, supporting_fact: str) -> str:
    return (
        f"Business: {prospect.get('name')} ({prospect.get('primary_type') or 'home service business'})\n"
        f"Personalization angle: {angle}\n"
        f"Supporting fact: {supporting_fact}\n\n"
        f"Write the cold outreach SMS now."
    )
