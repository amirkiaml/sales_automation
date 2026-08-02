"""
Three persona-differentiated SMS drafting agents - the SMS equivalent of
the lab's three email-writing agents. Run in parallel (by code, see
cold_outreach.py) against the same hook/angle so the picker agent is
comparing writing style, not different personalization strategies.
"""
import re

from agents import Agent

from app.config import settings

# Appended in code, never left to the model.
#
# CASL requires every commercial electronic message to carry sender
# identification, contact information, and a working unsubscribe
# mechanism - and a violation of the form requirements stands on its own,
# regardless of whether consent existed. Two things were wrong before:
#
#   - No contact information at all. A business name is not enough; the
#     CRTC expects a mailing address plus a phone, email or web address.
#     For SMS a link to a page carrying those details is accepted, which
#     is what the URL is doing here.
#   - "NO to opt-out" is non-standard. CRTC guidance for SMS names STOP
#     and UNSUBSCRIBE. The system already honoured STOP; it just never
#     told anyone.
#
# It was previously an instruction in the drafting prompt, which is the
# same mistake as the curly apostrophes and the sub-4.0 ratings: an
# instruction followed most of the time is not a guarantee, and this one
# has legal weight.
COMPLIANCE_FOOTER = "Reply YES for a demo, STOP to opt out. voicecaptures.com"



# Patterns the model invents when told not to write a compliance line.
# Telling it "do not write an opt-out" reliably produced a *different*
# disclaimer instead - "Msg & data rates may apply", "[Compliance line:
# ...]", "This is a promotional text message" - often wrapped in markdown
# the prompt already forbids. Measured at 33 of 37 drafts over the length
# ceiling because of it.
#
# Stripped in code because the prompt instruction is what caused it.
_INVENTED_DISCLAIMER = re.compile(
    r"""
    [*_\[\(]*                      # optional markdown or bracket wrapper
    (?:
        msg\s*&?\s*data\s+rates.*?
      | this\s+is\s+a\s+promotional.*?
      | compliance\s+line.*?
      | text\s+stop\s+to\s+opt.*?
      | reply\s+stop\s+to\s+opt.*?
      | \d+\+\s*only.*?
    )
    [*_\]\)]*\s*$
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

MAX_BODY_CHARS = 100


def clean_draft(text: str) -> str:
    """Strip what the drafting agents add that they were told not to.

    Markdown first - SMS renders it literally, so asterisks arrive as
    asterisks - then any disclaimer the model invented, then trailing
    punctuation left behind by the removal.
    """
    text = " ".join(text.split())
    text = _INVENTED_DISCLAIMER.sub("", text).strip()
    # Bracketed fragments FIRST - stripping ">" as a stray character would
    # otherwise break the pattern that matches them.
    #
    # These are the model commenting on its instructions rather than
    # following them: "<1030544-02>", "56-character". Naming the footer's
    # length in the prompt was enough to make it write "56-character" into
    # the message body.
    text = re.sub(r"<[^>]{0,40}>", "", text)
    text = re.sub(r"\b\d{1,3}-character\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[*_`#>]+", "", text)
    text = " ".join(text.split())
    return text.strip(" -|").strip()


def fits_one_segment(body: str) -> bool:
    return len(body) + 1 + len(COMPLIANCE_FOOTER) <= 160


def append_compliance_footer(text: str) -> str:
    """Guarantee the footer, exactly once."""
    text = text.strip()
    if COMPLIANCE_FOOTER in text:
        return text
    return f"{text} {COMPLIANCE_FOOTER}"


INTRO = """
You are an SMS outreach agent for VoiceCaptures, a company that provides
AI voice receptionist agents for home service businesses (contractors,
plumbers, electricians, HVAC, etc.) so they never miss a call that could
have been a job.

You write cold outreach text messages, not emails. Rules:
- Under 100 characters. Hard limit. Shorter is better.
- No greeting like "Dear" or "Hi there" - this is a text message, get to
  the point.
- Personalize using the specific angle and supporting fact you're given -
  don't invent details.
- Mention VoiceCaptures by name and what it does in one short phrase.
- Write ONLY the sales message itself. Nothing else: no opt-out line, no
  website, no disclaimer, no rate notice, no notes to yourself, no
  reference numbers, no commentary about these instructions. The message
  you write is sent verbatim to a stranger.
- Plain text only, no markdown, no emoji.
- Straight apostrophes only ('), never curly ones. A single curly
  apostrophe switches the message to UCS-2 encoding, which drops the
  segment size from 160 characters to 70 and can double the bill again.
"""

sms_agent_professional = Agent(
    name="Professional SMS Agent",
    instructions=INTRO + "\nYour style is professional, credible, and direct.",
    model=settings.DRAFTING_MODEL,
)

sms_agent_witty = Agent(
    name="Witty SMS Agent",
    instructions=INTRO + "\nYour style is warm and a little witty, without undermining credibility.",
    model=settings.DRAFTING_MODEL,
)

sms_agent_executive = Agent(
    name="Executive SMS Agent",
    instructions=INTRO + "\nYour style is extremely concise, like a busy executive texting a peer. Shortest of the three.",
    model=settings.DRAFTING_MODEL,
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
