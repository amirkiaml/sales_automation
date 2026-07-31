"""
Twilio SMS sending. Two entry points:

- send_sms(): plain function, used directly by the code-orchestrated cold
  outreach pipeline (no agent decision needed to send - see architecture
  notes in docs/).
- send_sms_tool: the same logic wrapped as a @function_tool, for the
  conversational SDR agent in phase 2 to call itself.

Both log the outbound message to the messages table.
"""
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from agents import function_tool

from app.config import settings
from app.db.client import log_message, is_suppressed

_twilio_client: Client | None = None

# Twilio codes worth translating into something an operator can act on.
_TWILIO_HINTS = {
    21211: "Twilio says that number isn't valid. Check it's full E.164 - "
           "a Toronto number is +1416... not +416...",
    21212: "The 'From' number in your .env isn't valid for this account.",
    21408: "Your Twilio account isn't permitted to send to that region.",
    21606: "That 'From' number can't send SMS. Check TWILIO_FROM_NUMBER.",
    21610: "That number has opted out and Twilio is blocking the send.",
    21614: "That number can't receive SMS (likely a landline).",
    21659: "'To' and 'From' can't be the same number.",
}


class SendFailed(Exception):
    """Twilio refused the send. Message is operator-facing."""


def _explain_twilio_error(e) -> str:
    hint = _TWILIO_HINTS.get(getattr(e, "code", None))
    base = f"Twilio error {getattr(e, 'code', '?')}"
    return f"{base}: {hint}" if hint else f"{base}: {getattr(e, 'msg', str(e))}"


def get_twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
            raise RuntimeError("Twilio credentials not set. Check your .env.")
        _twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    return _twilio_client


def send_sms(to_phone: str, body: str, prospect_id: str, agent_name: str = "") -> dict:
    """Send an SMS via Twilio and log it. Returns Twilio's response info."""
    # Checked here rather than at each call site so there is exactly one
    # place a message can leave the system, and it is guarded.
    if is_suppressed(to_phone):
        raise SendFailed(
            f"{to_phone} is on the suppression list (opted out). Not sending."
        )

    client = get_twilio_client()

    send_kwargs = {"to": to_phone, "body": body}
    if settings.TWILIO_MESSAGING_SERVICE_SID:
        send_kwargs["messaging_service_sid"] = settings.TWILIO_MESSAGING_SERVICE_SID
    else:
        send_kwargs["from_"] = settings.TWILIO_FROM_NUMBER

    try:
        message = client.messages.create(**send_kwargs)
    except TwilioRestException as e:
        # Surfaced to the operator instead of bubbling up as a 500. Twilio
        # rejects for reasons that are the operator's to fix (bad number,
        # unverified recipient on a trial account, To == From), so the UI
        # needs the reason, not a stack trace.
        raise SendFailed(_explain_twilio_error(e)) from e

    log_message(
        prospect_id=prospect_id,
        direction="outbound",
        body=body,
        phone=to_phone,
        twilio_sid=message.sid,
        twilio_status=message.status,
        agent_name=agent_name,
    )

    return {"sid": message.sid, "status": message.status}


@function_tool
def send_sms_tool(to_phone: str, body: str, prospect_id: str) -> str:
    """
    Send an SMS to a prospect and log it to the conversation history.

    Args:
        to_phone: The prospect's phone number in E.164 format (e.g. +16472518320)
        body: The SMS message text to send
        prospect_id: The prospect's database ID, for logging the message
    """
    result = send_sms(to_phone, body, prospect_id, agent_name="sdr_agent")
    return f"Message sent (status: {result['status']})"
