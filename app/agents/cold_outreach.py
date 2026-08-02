"""
Phase 1: cold outreach, orchestrated BY CODE (not by an LLM deciding the
sequence). This mirrors the lab's Part 2 pattern - predictable and
deterministic, which matters here because this runs at bulk-send scale.

Pipeline for one prospect:
    hook agent -> 3 drafting agents in parallel -> picker agent -> send (code)

The actual Twilio send is a plain function call, not a tool an agent
decides to invoke - nothing about "should this message be sent" is left
to model judgment in this phase.
"""
import asyncio
from datetime import datetime, timezone

from agents import Runner, trace


from app.agents.hook_agent import generate_hook
from app.agents.drafting_agents import (
    DRAFTING_AGENTS, append_compliance_footer, build_draft_prompt, clean_draft,
    fits_one_segment,
)
from app.agents.picker_agent import pick_best
from app.tools.twilio_sms import send_sms
from app.db.client import update_prospect_status


async def run_cold_outreach_for_prospect(prospect: dict, dry_run: bool = False) -> dict:
    """Runs the full pipeline for one prospect. Set dry_run=True to see the
    winning draft without actually sending or touching prospect status -
    useful for spot-checking output quality before a real batch send."""

    with trace(f"Cold outreach - {prospect['name']}"):
        hook = await generate_hook(prospect)

        prompt = build_draft_prompt(prospect, hook.angle, hook.supporting_fact)
        results = await asyncio.gather(
            *[Runner.run(agent, prompt) for agent in DRAFTING_AGENTS.values()]
        )
        # Cleaned before the picker sees them, so it judges the text that
        # will actually be sent rather than one padded with an invented
        # disclaimer.
        drafts = {
            label: clean_draft(result.final_output)
            for label, result in zip(DRAFTING_AGENTS.keys(), results)
        }

        # Prefer drafts that fit one SMS segment, before the picker sees
        # them. The length ceiling is an instruction and instructions hold
        # about 90% of the time - but three drafts are generated anyway,
        # so rather than truncating a winner mid-sentence, just judge the
        # ones that fit. Falls back to all three if none do, so a long
        # batch degrades to two segments rather than to nothing.
        fitting = {k: v for k, v in drafts.items() if fits_one_segment(v)}
        candidates = fitting or drafts
        oversize_dropped = sorted(set(drafts) - set(candidates))

        decision = await pick_best(candidates)
        # The picker returns a persona name, and it can name one that was
        # filtered out for being oversize - it sees the drafts, not the
        # dict keys. Crashed with KeyError on 1 of 37 before this guard.
        # Falling back to the shortest candidate rather than raising: a
        # slightly worse message beats no message.
        if decision.winner not in candidates:
            fallback = min(candidates, key=lambda k: len(candidates[k]))
            decision.winner = fallback
            decision.reason = (
                f"picker chose an unavailable draft; fell back to {fallback}"
            )
        # Appended in code, after the picker, so it cannot be dropped by a
        # model or edited on its way past.
        winning_text = append_compliance_footer(candidates[decision.winner])

    output = {
        "prospect_id": prospect["id"],
        "name": prospect["name"],
        "hook_angle": hook.angle,
        "supporting_fact": hook.supporting_fact,
        "drafts": drafts,
        "winner": decision.winner,
        "winner_reason": decision.reason,
        "oversize_dropped": oversize_dropped,
        "sent_text": winning_text,
    }

    if dry_run:
        output["sent"] = False
        return output

    send_result = send_sms(
        to_phone=prospect["phone"],
        body=winning_text,
        prospect_id=prospect["id"],
        agent_name=f"drafting_agent:{decision.winner}",
    )
    update_prospect_status(
        prospect["id"],
        status="contacted",
        last_contacted_at=datetime.now(timezone.utc).isoformat(),
    )

    output["sent"] = True
    output["twilio_sid"] = send_result["sid"]
    return output
