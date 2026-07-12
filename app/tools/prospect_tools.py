"""
Tools the SDR agent can call beyond sending SMS (see app/tools/twilio_sms.py
for send_sms_tool).
"""
from agents import function_tool

from app.db.client import update_prospect_status


@function_tool
def flag_human_handoff_tool(prospect_id: str, reason: str) -> str:
    """
    Flag this conversation for a human to take over. Use this when the
    prospect shows a strong buying signal (wants to book a call, ready to
    move forward, asking for pricing to commit) or asks something you
    genuinely can't answer confidently.

    Args:
        prospect_id: The prospect's database ID
        reason: One short phrase explaining why this needs a human
    """
    update_prospect_status(prospect_id, status="needs_human")
    return f"Flagged for human handoff: {reason}"
