"""
Tools the SDR agent can call beyond sending SMS (see app/tools/twilio_sms.py
for send_sms_tool).
"""
from agents import function_tool

from app.db.client import update_prospect_status, set_autopilot


@function_tool
def flag_human_handoff_tool(prospect_id: str, reason: str) -> str:
    """
    Flag this conversation for a human to take over. Use this when the
    prospect explicitly asks to speak with a person, or shows a genuine
    ready-to-commit signal (wants to sign up now, needs pricing to finalize
    a purchase) - NOT for ordinary interest like agreeing to see a demo,
    which you should handle yourself.

    Args:
        prospect_id: The prospect's database ID
        reason: One short phrase explaining why this needs a human
    """
    update_prospect_status(prospect_id, status="needs_human")
    set_autopilot(prospect_id, enabled=False)  # stop auto-replying once a human is taking over
    return f"Flagged for human handoff: {reason}"
