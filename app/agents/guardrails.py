"""
Guardrails around the autopilot SDR agent.

Two of them, doing different jobs at different points:

  INPUT  - scope guardrail. Runs before the agent, in plain code, no model.
           Blocks restricted topics (pricing, contracts, legal) and explicit
           requests for a human. Deterministic on purpose: whether a
           pricing question is "close enough to answer" must not be a
           judgement call, because the failure mode is a number the
           prospect will hold you to.

  OUTPUT - grounding guardrail. Runs on the drafted reply before it is
           sent, and checks every factual claim against the KB entries
           actually retrieved for this message. Catches the interesting
           failure: a question that IS in scope, answered with a detail
           that isn't in the KB.

Why the agent no longer holds the send tool
-------------------------------------------
An output guardrail runs after tool calls. While `send_sms_tool` was on
this agent, any grounding check would inspect a message Twilio had already
delivered - and you cannot unsend a text. So the agent now returns its
reply as structured output and the send is a plain function call made by
code once the guardrails pass. This also makes the code match the rule the
README already stated for cold outreach: the send is never a tool an agent
can decide to invoke.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from agents import Agent, Runner


from app.config import settings
from app.kb.loader import Entry, holding_reply_for, load_kb, match_restricted, wants_human


class ScopeVerdict(BaseModel):
    """Why (or whether) a message was refused before reaching the agent."""

    allowed: bool
    reason: str = ""
    escalate: bool = False
    layer: str = ""  # 'keywords' | 'classifier' - which one decided
    topic_id: str = ""
    holding_reply: str = ""  # verbatim, sent immediately; makes no factual claim


def check_scope_keywords(message: str) -> ScopeVerdict:
    """Layer 1: deterministic, no model.

    Catches the unambiguous cases for free and with certainty. "How much
    does it cost" must never depend on a sampled token.
    """
    if wants_human(message):
        return ScopeVerdict(allowed=False, reason="asked for a person", escalate=True,
                            layer="keywords", topic_id="handoff",
                            holding_reply=load_kb().handoff_holding_reply)

    restricted = match_restricted(message)
    if restricted:
        return ScopeVerdict(allowed=False, reason=restricted.reason, escalate=True,
                            layer="keywords", topic_id=restricted.id,
                            holding_reply=restricted.holding_reply)

    return ScopeVerdict(allowed=True, layer="keywords")


class ScopeClassification(BaseModel):
    category: str = Field(
        description="One of: answerable, restricted, handoff, off_topic"
    )
    topic_id: str = Field(
        default="", description="If restricted, the id of the matching restricted topic."
    )
    rationale: str = Field(default="", description="Under 12 words.")


def _classifier_instructions() -> str:
    """Built from the KB so the classifier can't drift from the keyword layer.

    Both layers read the same YAML. Hardcoding the topic list here would
    mean adding a restricted topic in one place and silently not the other.
    """
    kb = load_kb()
    topics = "\n".join(
        f"  - {r.id}: {r.reason} (e.g. {', '.join(r.triggers[:5])})" for r in kb.restricted
    )
    covered = "\n".join(f"  - {e.topic}: {e.id}" for e in kb.entries)
    return f"""
You classify a single inbound SMS from a sales prospect. Output one category.

RESTRICTED - the message asks about any of these, in ANY phrasing,
including slang, indirect or joking ones ("what's the damage?", "gonna
break the bank?", "what's the tab", "do I get stuck in something"):
{topics}

HANDOFF - they want to speak to a human, or ask who is texting them.

OFF_TOPIC - not about the product, their business, or this conversation.
Weather, sports, politics, personal questions, general trivia, spam.

ANSWERABLE - anything else, including ordinary product questions. The
knowledge base covers roughly:
{covered}

Rules:
- Err toward RESTRICTED. A missed restricted topic sends a wrong claim
  about money or contracts to a real person; a false positive only routes
  the message to a human, which costs nothing but a few minutes.
- A message can mention several things. If ANY part is restricted, the
  whole message is RESTRICTED.
- Casual acknowledgements ("ok", "sure", "thanks") are ANSWERABLE.
"""


_scope_agent = Agent(
    name="Scope Guardrail Classifier",
    instructions=_classifier_instructions(),
    model=settings.TRIAGE_MODEL,
    output_type=ScopeClassification,
)


async def check_scope(message: str) -> ScopeVerdict:
    """Two independent layers; a message gets through only if both pass.

    Layer 1 is literal string matching - free, instant, certain, and blind
    to paraphrase. Layer 2 is a cheap classifier that catches the phrasings
    layer 1 can't enumerate ("what's the damage?" means pricing and shares
    no words with any trigger).

    The classifier does not REPLACE the keyword list. If it did, "how much
    does it cost" would become probabilistic, and it currently isn't. It
    only ever adds refusals; it cannot overturn one.

    Fails closed: if the classifier errors, the message escalates. Costs
    nothing in practice, since autopilot can't draft a reply without the
    model API either way.
    """
    keyword_verdict = check_scope_keywords(message)
    if not keyword_verdict.allowed:
        return keyword_verdict

    try:
        result = await Runner.run(_scope_agent, message)
        classification = result.final_output
    except Exception as e:  # noqa: BLE001 - any failure must fail closed
        return ScopeVerdict(
            allowed=False, reason=f"scope classifier unavailable ({type(e).__name__})",
            escalate=True, layer="classifier", topic_id=topic,
            holding_reply=holding_reply_for(topic),
        )

    category = (classification.category or "").strip().lower()

    if category == "restricted":
        # The classifier sometimes returns category=restricted with an empty
        # or unrecognised topic_id. That used to become the literal string
        # "restricted topic", which matches no KB entry, so the holding
        # reply lookup silently returned "" and the prospect got blocked
        # AND met with silence - exactly what holding replies exist to
        # prevent, reintroduced through a different door.
        #
        # Fall back to any defined holding reply rather than depending on
        # the model populating a second field correctly.
        topic = classification.topic_id or ""
        reply = holding_reply_for(topic) if topic else ""
        if not reply:
            kb = load_kb()
            reply = next((r.holding_reply for r in kb.restricted if r.holding_reply), "")
            topic = topic or "restricted"
        return ScopeVerdict(
            allowed=False,
            reason=f"{topic} (paraphrase caught by classifier: {classification.rationale})",
            escalate=True, layer="classifier", topic_id=topic,
            holding_reply=reply,
        )
    if category == "handoff":
        return ScopeVerdict(allowed=False, reason="asked for a person", escalate=True,
                            layer="classifier", topic_id="handoff",
                            holding_reply=load_kb().handoff_holding_reply)

    # off_topic is NOT blocked here. The agent handles it with the Scope
    # Rule - a brief "I can only help with VoiceCaptures questions" is a
    # better reply than silently escalating small talk to a human.
    return ScopeVerdict(allowed=True)


class GroundingVerdict(BaseModel):
    grounded: bool = Field(
        description="True only if every factual claim in the reply is supported by the provided KB entries."
    )
    unsupported_claim: str = Field(
        default="", description="The specific unsupported claim, quoted, or empty if grounded."
    )


_GROUNDING_INSTRUCTIONS = """
You are a strict fact-checker for outbound sales SMS.

You are given (a) knowledge base entries and (b) a draft reply about to be
sent to a real prospect. Decide whether EVERY factual claim in the draft is
supported by the entries.

Rules:
- Rephrasing an entry is fine. Adding a fact is not.
- Numbers, timeframes, prices, guarantees and integration names are facts.
  If one appears in the draft and not in the entries, it is unsupported.
- Conversational filler ("happy to help", "makes sense") is not a claim.
- Asking a question is not a claim.
- A reply that only says a human will follow up is grounded by definition.
- A REFUSAL or REDIRECT is grounded by definition. "I can only help with
  VoiceCaptures questions", "that's not something I can discuss", "let me
  get you an accurate answer" - these assert nothing about the product, so
  there is nothing to support. Naming the product while declining to
  discuss something is not a claim about it.
- A greeting or acknowledgement ("hi, how can I help?", "got it") is
  grounded by definition.
- If the entries are empty, then any AFFIRMATIVE claim about what the
  product is or does is unsupported. An empty entry list does not make a
  refusal, greeting or question unsupported - those make no claim at all.

Be strict. A false "grounded" sends a wrong claim to a real person; a false
"not grounded" only routes the message to a human for review.
"""

_grounding_agent = Agent(
    name="Grounding Guardrail",
    instructions=_GROUNDING_INSTRUCTIONS,
    model=settings.TRIAGE_MODEL,
    output_type=GroundingVerdict,
)


async def check_grounding(draft: str, entries: list[Entry]) -> GroundingVerdict:
    """Verify a drafted reply against the KB entries retrieved for it.

    Uses the cheap model deliberately - this runs on every autopilot reply,
    and the task is comparison rather than generation.
    """
    if not draft.strip():
        return GroundingVerdict(grounded=False, unsupported_claim="(empty reply)")

    kb_text = "\n".join(f"[{e.id}] {e.answer}" for e in entries) or "(no entries)"
    prompt = (
        f"Knowledge base entries:\n{kb_text}\n\n"
        f"Draft reply:\n{draft}\n\n"
        f"Is every factual claim supported?"
    )
    result = await Runner.run(_grounding_agent, prompt)
    return result.final_output
