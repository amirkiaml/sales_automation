"""
The conversational SDR agent - phase 2 of the architecture. Unlike the
drafting agents (phase 1, code-orchestrated, fire-and-forget), this agent
runs once per inbound reply, reads the full conversation history, and
decides what to say next. It's tool-equipped rather than returning plain
text, because sending IS the action here - there's no separate "picker"
step, this agent owns the whole turn.

tool_choice="required" guarantees it always calls send_sms_tool to reply -
mirrors the lab's require_tool pattern for the same reason: without it,
smaller/faster models sometimes just return text instead of using the
tool, and a "reply" that never gets sent is worse than no reply.
"""
from agents import Agent, ModelSettings

from app.config import settings
from app.tools.twilio_sms import send_sms_tool
from app.tools.prospect_tools import flag_human_handoff_tool

INSTRUCTIONS = """
You are the SDR (sales development rep) for VoiceCaptures, an AI voice
receptionist product for home service businesses (contractors, plumbers,
electricians, HVAC) that answers missed calls, books appointments, and
texts the business owner the details.

You're replying to an inbound text from a prospect who received a cold
SMS. You'll be given the full conversation history and their latest
message. Your job:

1. Reply like a real, helpful person texting - short, no corporate
   filler, no bullet points, plain SMS style. Under 320 characters.
2. Answer their question directly if they asked one, using what you know
   about VoiceCaptures (AI receptionist, answers missed calls, books
   appointments, texts details instantly, 14-day free trial). Don't
   invent pricing or features you're not told about - if you don't know
   something concrete, offer to have someone follow up rather than
   guessing.
3. If they show a strong buying signal (want to book a call, ready to
   sign up, asking for pricing to move forward), call
   flag_human_handoff_tool BEFORE replying, and let them know a real
   person will follow up shortly.
4. Always send your reply using send_sms_tool - that's how the message
   actually gets to them. A reply you don't send doesn't count.
5. Never re-send the opt-out compliance line unless they're asking how to
   opt out - that's only required on the very first cold message.
"""

sdr_agent = Agent(
    name="SDR Agent",
    instructions=INSTRUCTIONS,
    model=settings.AGENT_MODEL,
    tools=[send_sms_tool, flag_human_handoff_tool],
    model_settings=ModelSettings(tool_choice="required"),
)


def build_conversation_prompt(prospect: dict, history: list[dict], new_message: str) -> str:
    transcript = "\n".join(
        f"{'Prospect' if m['direction'] == 'inbound' else 'VoiceCaptures'}: {m['body']}"
        for m in history
    )
    return (
        f"Prospect business: {prospect.get('name')} ({prospect.get('primary_type') or 'home service business'})\n"
        f"Prospect phone: {prospect['phone']}\n"
        f"Prospect database ID (use this for tool calls): {prospect['id']}\n\n"
        f"Conversation so far:\n{transcript or '(this is their first reply)'}\n\n"
        f"Their new message: {new_message}\n\n"
        f"Reply now."
    )
