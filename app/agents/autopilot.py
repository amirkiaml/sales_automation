"""
Autopilot orchestration: the guarded path from an inbound message to a
sent reply.

Lives here rather than in webhook.py because poll_inbound.py needs the
identical sequence. Two copies of a compliance-relevant control flow is
how they drift.

The sequence, and why it is this order:

  1. Scope guardrail (code, no model). Restricted topics and explicit
     requests for a human never reach the agent at all. Cheapest check,
     hardest guarantee, so it goes first.
  2. KB retrieval. An empty result is meaningful - it means off-topic or
     uncovered, and the agent is told it may make no factual claims.
  3. Agent drafts a reply. It has no send tool; it returns text.
  4. Grounding guardrail (cheap model). Checks the draft against the
     entries actually retrieved.
  5. Send - a plain function call, only if everything above passed.

Every refusal path lands in the same place: the human review queue, with
the reason attached. Nothing is silently dropped, and nothing ungrounded
is sent. The operator sees "agent wanted to say X, blocked because Y" in
/admin/review and decides.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agents import Runner, trace

from app.agents.guardrails import check_grounding, check_scope
from app.agents.sdr_agent import build_conversation_prompt, sdr_agent
from app.db.client import (
    get_conversation_history, set_autopilot, set_pending_reply, update_prospect_status,
)
from app.kb.loader import load_kb, search
from app.observability import Trace
from app.tools.twilio_sms import SendFailed, send_sms

logger = logging.getLogger(__name__)


@dataclass
class AutopilotOutcome:
    action: str  # "sent" | "escalated" | "blocked_scope" | "blocked_grounding" | "send_failed"
    detail: str = ""
    draft: str = ""
    kb_ids: tuple[str, ...] = ()

    @property
    def sent(self) -> bool:
        return self.action == "sent"


def _escalate(
    prospect_id: str, reason: str, draft: str = "", disable_autopilot: bool = False
) -> None:
    """Route one message to a human. Optionally stop auto-replying for good.

    The distinction matters, and getting it wrong is why autopilot appeared
    broken: originally EVERY refusal disabled autopilot, so the first
    pricing question turned it off permanently and every later message -
    including ordinary ones the agent handles fine - dropped into the
    review queue with no indication why.

    Two different situations:

      disable_autopilot=True  - a human is now the counterparty. They asked
        for a person, or they're ready to buy. The agent replying on top of
        that conversation would be actively bad.

      disable_autopilot=False - a single message the agent isn't allowed to
        answer (pricing, an unsupported claim, a KB gap). A human answers
        that one message; the agent resumes on the next. A prospect asking
        one pricing question hasn't stopped being an autopilot conversation.

    The draft is preserved either way - the operator usually wants to edit
    it, not rewrite from scratch.

    Note flag_human_handoff_tool disables autopilot on its own, so genuine
    tool-initiated handoffs are covered without this flag.
    """
    update_prospect_status(prospect_id, status="needs_human")
    if disable_autopilot:
        set_autopilot(prospect_id, enabled=False)
    set_pending_reply(prospect_id, pending_reply=draft, context=reason)


def _send_holding_reply(prospect: dict, text: str, topic_id: str, tr) -> bool:
    """Send a fixed acknowledgement so a blocked message isn't met with silence.

    Verbatim from the KB - not generated, not paraphrased by a model, and
    containing no factual claim, which is why it can be sent automatically
    on a path where the agent itself is not trusted to speak. It goes
    through send_sms like anything else, so the suppression check still
    applies.

    The alternative is what the code did before: block, queue an empty
    draft for review, and leave the prospect with no reply at all. A
    pricing question is the highest-intent message in the funnel and
    silence is the worst possible response to it.
    """
    if not text:
        tr.step("holding_reply", status="skipped",
                reason=f"no holding_reply defined for '{topic_id}' in the KB")
        return False

    # Don't send the same canned line twice in a row. These are constants,
    # so a prospect who asks two uncovered questions would otherwise get
    # the identical text back to back, which reads broken rather than
    # helpful. The queue entry is still created either way - only the
    # duplicate outbound is suppressed.
    recent = get_conversation_history(prospect["id"], limit=4)
    last_outbound = next((m for m in recent if m["direction"] == "outbound"), None)
    if last_outbound and last_outbound["body"].strip() == text.strip():
        tr.step("holding_reply", status="skipped",
                reason="identical holding reply was the previous outbound message")
        return False
    try:
        send_sms(
            to_phone=prospect["phone"], body=text, prospect_id=prospect["id"],
            agent_name=f"holding_reply:{topic_id}",
        )
        tr.step("holding_reply", status="ok", topic=topic_id, sent=text[:160])
        return True
    except SendFailed as e:
        tr.step("holding_reply", status="error", error=str(e))
        return False


async def run_autopilot(prospect: dict, history: list[dict], body: str) -> AutopilotOutcome:
    """Draft, check and (maybe) send an automated reply to one inbound message."""
    prospect_id = prospect["id"]
    tr = Trace(prospect_id, entry_point="autopilot", trigger_text=body)

    # 1. Scope - keyword floor, then a cheap classifier for paraphrase.
    #    Both must pass. Fails closed.
    scope = await check_scope(body)
    tr.step(
        "scope_guardrail", status="ok" if scope.allowed else "blocked",
        layer=scope.layer, reason=scope.reason,
    )
    if not scope.allowed:
        logger.info("Autopilot blocked by scope guardrail (%s) for %s", scope.reason, prospect_id)
        # "asked for a person" means a human is taking over. A restricted
        # topic is one message the agent may not answer.
        is_handoff = "asked for a person" in scope.reason
        _send_holding_reply(prospect, scope.holding_reply, scope.topic_id or "restricted", tr)
        _escalate(
            prospect_id, reason=f"Scope guardrail: {scope.reason}",
            disable_autopilot=is_handoff,
        )
        tr.step("autopilot_state", status="ok",
                autopilot="disabled - human taking over" if is_handoff
                else "left on - single message refused, agent resumes next turn")
        tr.finish("blocked_scope")
        return AutopilotOutcome(action="blocked_scope", detail=scope.reason)

    # 2. Retrieval. Empty is a real answer, not a failure.
    entries = search(body)
    tr.step(
        "kb_retrieval", status="ok" if entries else "skipped",
        matched=[e.id for e in entries],
        note="" if entries else "no entries matched - agent may assert nothing",
    )

    # 3. Draft.
    with trace(f"Autopilot reply - {prospect.get('name')}"):
        prompt = build_conversation_prompt(prospect, history, body, kb_entries=entries)
        result = await Runner.run(sdr_agent, prompt)
        reply = result.final_output
        tr.step(
            "sdr_agent", status="ok", agent="Autopilot SDR Agent",
            claimed_kb_ids=list(reply.used_kb_ids),
            escalate=reply.escalate, handoff=reply.handoff, draft=reply.message[:300],
        )

        if reply.escalate or reply.handoff or not reply.message.strip():
            reason = (
                "Agent flagged a handoff: asked for a person or ready to buy"
                if reply.handoff
                else "Agent escalated: question not covered by the knowledge base"
            )
            _send_holding_reply(prospect, load_kb().kb_gap_holding_reply, "kb_gap", tr)
            # Only a real handoff stops autopilot. A KB gap is one message
            # the agent couldn't answer, not the end of the conversation.
            _escalate(
                prospect_id, reason=reason, draft=reply.message,
                disable_autopilot=reply.handoff,
            )
            tr.step("autopilot_state", status="ok",
                    autopilot="disabled - handoff" if reply.handoff else "left on")
            tr.finish("escalated")
            return AutopilotOutcome(action="escalated", detail=reason, draft=reply.message)

        # 4. Grounding - checked before the send, which is only possible
        #    because the agent cannot send for itself.
        verdict = await check_grounding(reply.message, entries)
        tr.step(
            "grounding_guardrail", status="ok" if verdict.grounded else "blocked",
            checked_against=[e.id for e in entries],
            unsupported_claim=verdict.unsupported_claim,
        )

    if not verdict.grounded:
        # Deliberately no holding reply here. The agent already produced a
        # reply and it was caught; sending a second automated message
        # compounds rather than helps. This one waits for a human.
        reason = f"Grounding guardrail: unsupported claim - {verdict.unsupported_claim}"
        logger.warning("Autopilot blocked ungrounded reply for %s: %s", prospect_id, reason)
        _escalate(prospect_id, reason=reason, draft=reply.message)
        tr.finish("blocked_grounding")
        return AutopilotOutcome(
            action="blocked_grounding", detail=reason, draft=reply.message,
            kb_ids=tuple(reply.used_kb_ids),
        )

    # 5. Send.
    try:
        send_sms(
            to_phone=prospect["phone"], body=reply.message, prospect_id=prospect_id,
            agent_name="sdr_agent:autopilot",
        )
        tr.step("send_sms", status="ok", to=prospect["phone"])
    except SendFailed as e:
        tr.step("send_sms", status="error", error=str(e))
        _escalate(prospect_id, reason=f"Send failed: {e}", draft=reply.message)
        tr.finish("send_failed")
        return AutopilotOutcome(action="send_failed", detail=str(e), draft=reply.message)

    tr.finish("sent")
    return AutopilotOutcome(
        action="sent", draft=reply.message, kb_ids=tuple(reply.used_kb_ids)
    )
