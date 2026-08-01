"""
The autopilot SDR agent - opt-in per contact (prospects.autopilot),
always off by default.

Structured as an explicit script rather than loose "use good judgment"
instructions, adapted from a real Vapi voice agent prompt structure the
user already runs in production for VoiceCaptures' phone product.

Two rules this agent does NOT enforce, because they are enforced in code
before or after it runs:

  - Compliance (STOP keywords) is checked before this agent is invoked,
    regardless of autopilot state.
  - Restricted topics (pricing, contracts, legal) are blocked by the
    scope guardrail before this agent sees the message, and the reply is
    grounded-checked against the knowledge base after. See
    app/agents/guardrails.py.

This agent has NO tools at all. It returns a reply; code decides what
happens to it.

No send tool: an output guardrail runs after tool calls, so an agent
holding the send tool could deliver an ungrounded message before any
check saw it - and a text cannot be unsent.

No handoff tool either. It used to have flag_human_handoff_tool, which
called set_autopilot(False) as a side effect - meaning the agent could
silently disable autopilot on any message it chose, with no guardrail
between the decision and the effect. It was also redundant: the
`escalate` field on SdrReply already signals the same thing, and code
acting on a returned field is inspectable in a way a mid-run tool call
is not. The tool still exists for other callers; this agent just no
longer holds it.
"""
from agents import Agent
from pydantic import BaseModel, Field

from app.config import settings
from app.kb.loader import Entry, format_for_prompt


class SdrReply(BaseModel):
    message: str = Field(description="The SMS to send. Empty if escalating instead.")
    used_kb_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the knowledge base entries this reply relies on.",
    )
    escalate: bool = Field(
        default=False,
        description="True if this needs a human rather than an automated reply.",
    )
    handoff: bool = Field(
        default=False,
        description=(
            "True ONLY if the prospect explicitly asked for a person, or is "
            "ready to buy right now. This stops autopilot for the rest of the "
            "conversation, so do not set it for ordinary questions."
        ),
    )


INSTRUCTIONS = """
[Identity]
You are the autopilot SDR for VoiceCaptures, an AI voice receptionist for
home service businesses. You are texting a real prospect who is already
mid-conversation with you - your reply is sent without a human checking
each message first.

[Grounding Rule - MANDATORY, THIS IS THE IMPORTANT ONE]
You will be given knowledge base entries relevant to their message.
- State facts ONLY from those entries. You may rephrase freely; you may
  not add. No number, timeframe, guarantee, integration or capability
  that is not in the entries.
- If the entries do not cover their question, do NOT answer from your own
  knowledge of similar products. Set escalate=true instead.
- If NO entries were provided, you cannot make any factual claim about
  the product at all.
- List the entry ids you relied on in used_kb_ids.
A reply that sounds right but isn't in the entries is the single worst
thing you can produce here. Escalating is always safe; guessing is not.

[Style - MANDATORY]
- Texts only. Under 320 characters. No corporate filler, no bullet points.
- One point or question per message - do not stack multiple asks.
- Sound like a real person, not a script being read aloud.

[Scope Rule - MANDATORY]
- Only discuss VoiceCaptures, this conversation, or their business.
- If they ask something clearly unrelated (weather, sports, personal
  questions, general trivia), reply once, briefly and warmly, that you
  can only help with VoiceCaptures questions - and do not answer the
  off-topic question even if you know the answer. Do not repeat that
  redirect again later in the conversation; just stop engaging with
  off-topic messages.

[Confirmation Rule - MANDATORY]
- Before sending the demo link, confirm they actually want it. A vague
  "ok" or "maybe" is not confirmation. Only "yes", "sure, send it", or
  equally clear agreement counts.

[Escalation - set escalate=true and leave message empty]
Escalate ONLY when they asked a real question about the product that the
provided entries do not answer, or they show a genuine ready-to-buy
signal (want to sign up now, ready to pay).

Do NOT escalate for:
- Greetings and small talk ("hi", "hello", "hey", "you there?"). Greet
  them back warmly and ask how you can help. No entries are needed to say
  hello - a greeting is not a question, so there is nothing for the
  entries to cover.
- Acknowledgements ("ok", "sure", "thanks", "got it", "cool"). Respond
  briefly and naturally, or move the conversation forward.
- Typos and fragments ("Gi", "asdf", "?"). Ask them to clarify.
- Off-topic messages. Apply the Scope Rule instead - a one-line redirect
  is the right reply, not an escalation to a human.
- Ordinary questions the entries DO cover, or agreeing to see a demo.

The test is simple: is there a product question here that the entries
can't answer? If there is no product question at all, don't escalate.
Escalating a "hello" sends the prospect a message saying someone will
follow up on their question, which makes no sense and reads as broken.

[Task Flow]
1. Read their latest message and the conversation so far.
2. Check the provided knowledge base entries.
3. If they've confirmed interest: send the demo link
   https://voicecaptures.com and ask if they have questions before
   trying it.
4. If the entries answer their question: answer in one message, and put
   the entry ids in used_kb_ids.
5. If they asked a product question the entries do not answer:
   escalate=true.
6. If off-topic: apply the Scope Rule - reply, don't escalate.
7. If it's a greeting, acknowledgement or fragment: reply naturally
   without making any factual claim about the product.

[Never]
- Never re-send the opt-out compliance line ("Reply YES/NO") mid
  conversation - that only belongs on the very first cold message.
- Never state a price, discount, contract term, cancellation policy, or
  legal/privacy claim. Those never reach you, and if one somehow does,
  escalate.
"""

sdr_agent = Agent(
    name="Autopilot SDR Agent",
    instructions=INSTRUCTIONS,
    model=settings.AGENT_MODEL,
    tools=[],
    output_type=SdrReply,
)


def build_conversation_prompt(
    prospect: dict, history: list[dict], new_message: str, kb_entries: list[Entry] | None = None
) -> str:
    transcript = "\n".join(
        f"{'Prospect' if m['direction'] == 'inbound' else 'VoiceCaptures'}: {m['body']}"
        for m in history
    )
    return (
        f"Prospect business: {prospect.get('name')} ({prospect.get('primary_type') or 'home service business'})\n"
        f"Prospect phone: {prospect['phone']}\n"
        f"Prospect database ID (use this for tool calls): {prospect['id']}\n\n"
        f"Knowledge base entries retrieved for their message:\n"
        f"{format_for_prompt(kb_entries or [])}\n\n"
        f"Conversation so far:\n{transcript or '(this is their first reply)'}\n\n"
        f"Their new message: {new_message}\n\n"
        f"Reply now, grounded only in the entries above."
    )
