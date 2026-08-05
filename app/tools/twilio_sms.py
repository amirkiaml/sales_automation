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
from app.db.client import log_message, is_suppressed, get_prospect_by_id

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


# Line types a message can actually reach. An allowlist rather than a
# blocklist of landlines: Twilio returns eleven types and can add more,
# and an unrecognised value should stop a send rather than sail through
# it. Unknown or unchecked numbers are allowed - the first send is what
# produces the evidence, so refusing them would mean never learning.
SENDABLE_LINE_TYPES = {
    "deliverable", "mobile", "fixedVoip", "nonFixedVoip", "personal",
    "unknown", "",
}


class SendFailed(Exception):
    """Twilio refused the send. Message is operator-facing."""


class NotSendable(SendFailed):
    """The number is known not to accept SMS.

    Distinct from a send that failed once: this one fails every time, so
    the operator should stop rather than retry.
    """


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


# Characters models emit that are not in the GSM-7 alphabet. Any one of
# them switches the whole message to UCS-2, which drops the segment size
# from 160 characters to 70 - a 150-character message with one curly
# apostrophe bills as THREE segments instead of one.
#
# This is in code rather than the prompt because the prompt already says
# not to use them and the model does it anyway. Same reason the compliance
# footer is appended here: anything with a cost attached should not depend
# on an instruction being followed.
_GSM_SUBSTITUTIONS = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"', "\u201e": '"',                  # curly double quotes
    "\u2013": "-", "\u2014": "-", "\u2212": "-",                  # en/em dash, minus
    "\u2026": "...",                                              # ellipsis
    "\u00a0": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ",    # exotic spaces
    "\u2022": "-", "\u00b7": "-",                                 # bullets
    "\u2032": "'", "\u2033": '"',                                 # primes
    "\u2192": "->", "\u2190": "<-",                               # arrows
    "\u00ab": '"', "\u00bb": '"',                                 # guillemets
    "\u0060": "'", "\u00b4": "'",                                 # backtick, acute
}


# The GSM-7 alphabet. A character outside this set switches the message
# to UCS-2 encoding and more than doubles the per-segment cost.
_GSM_ALPHABET = set(
    "@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\u00d8\u00f8\u00c5\u00e5"
    "\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e\u00c6\u00e6"
    "\u00df\u00c9 !\"#\u00a4%&'()*+,-./0123456789:;<=>?"
    "\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1\u00dc\u00a7"
    "\u00bfabcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0"
    "\n\r"
) | set("^{}\\[~]|\u20ac")


def sanitize_for_sms(text: str) -> str:
    """Make a draft safe and cheap to send.

    Two jobs:
      1. Replace non-GSM punctuation with ASCII equivalents, so one curly
         apostrophe doesn't triple the segment count.
      2. Collapse newlines and strip wrapping quotes. Models occasionally
         return their message wrapped in quotation marks or split across
         lines, and both go out verbatim otherwise.
    """
    for bad, good in _GSM_SUBSTITUTIONS.items():
        text = text.replace(bad, good)

    text = " ".join(text.split())          # collapse newlines and runs of spaces

    # Anything still outside GSM-7 forces the whole message to UCS-2,
    # where segments are 70 characters instead of 160. Emoji are the
    # common case - a model added a smiley to a greeting and would have
    # multiplied the bill for it. Substitutions above handle punctuation;
    # this drops whatever is left.
    text = "".join(ch for ch in text if ch in _GSM_ALPHABET)
    text = " ".join(text.split())          # tidy any gaps the removal left

    # A model returning "..." around the whole message is common enough to
    # be worth handling, but only strip when BOTH ends match - a message
    # that legitimately ends in a quote stays intact.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()

    return text


def send_sms(to_phone: str, body: str, prospect_id: str, agent_name: str = "") -> dict:
    """Send an SMS via Twilio and log it. Returns Twilio's response info."""
    # Checked here rather than at each call site so there is exactly one
    # place a message can leave the system, and it is guarded.
    body = sanitize_for_sms(body)

    # Checked here, alongside suppression, for the same reason: one place
    # a message can leave the system, and it is guarded. A new code path
    # inherits both checks without having to remember them.
    if prospect_id:
        prospect = get_prospect_by_id(prospect_id)
        line_type = (prospect or {}).get("line_type") or ""
        if line_type not in SENDABLE_LINE_TYPES:
            raise NotSendable(
                f"{to_phone} is a {line_type} and cannot receive SMS. "
                "Nothing sent."
            )

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
