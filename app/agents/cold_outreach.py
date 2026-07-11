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
from app.agents.drafting_agents import DRAFTING_AGENTS, build_draft_prompt
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
        drafts = {
            label: result.final_output
            for label, result in zip(DRAFTING_AGENTS.keys(), results)
        }

        decision = await pick_best(drafts)
        winning_text = drafts[decision.winner]

    output = {
        "prospect_id": prospect["id"],
        "name": prospect["name"],
        "hook_angle": hook.angle,
        "supporting_fact": hook.supporting_fact,
        "drafts": drafts,
        "winner": decision.winner,
        "winner_reason": decision.reason,
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
