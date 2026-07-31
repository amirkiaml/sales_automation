"""
FastAPI routes Twilio calls directly:

  POST /webhooks/twilio/sms     - inbound SMS from a prospect
  POST /webhooks/twilio/status  - delivery status callbacks for sent SMS

Twilio signs every webhook request with an X-Twilio-Signature header;
validate_twilio_signature() checks it against your auth token so random
internet traffic can't pretend to be Twilio and trigger agent replies.
"""
import logging

from fastapi import APIRouter, Request, Response
from twilio.request_validator import RequestValidator

from agents import Runner, trace

from app.config import settings
from app.db.client import (
    get_prospect_by_phone,
    upsert_prospect,
    log_message,
    update_prospect_status,
    add_suppression,
    touch_last_reply,
    get_conversation_history,
    update_message_status,
)
from app.agents.triage_agent import classify
from app.agents.sdr_agent import sdr_agent, build_conversation_prompt

logger = logging.getLogger("webhook")
router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])

# Twilio's standard opt-out keywords - handled before any agent runs, both
# for reliability (never depends on a model call succeeding) and because
# Twilio's own carrier-level STOP handling may already intercept these
# before they even reach your app on some number types.
STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}

EMPTY_TWIML = Response(content="<Response></Response>", media_type="text/xml")


def _validate_signature(request: Request, form: dict) -> bool:
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    # Twilio signs the exact URL it called. Behind a proxy (Railway, ngrok)
    # the scheme is often rewritten to http internally - trust
    # X-Forwarded-Proto if present so the signature check matches what
    # Twilio actually sent.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    url = str(request.url).replace(request.url.scheme, proto, 1)
    return validator.validate(url, form, signature)


@router.post("/sms")
async def inbound_sms(request: Request):
    form = dict(await request.form())

    if settings.APP_BASE_URL and not _validate_signature(request, form):
        logger.warning("Invalid Twilio signature on inbound SMS webhook - rejecting.")
        return Response(status_code=403)

    from_phone = form.get("From", "")
    body = (form.get("Body") or "").strip()
    message_sid = form.get("MessageSid")

    prospect = get_prospect_by_phone(from_phone)
    if prospect is None:
        # Unknown number texting in - keep the message rather than drop it.
        prospect = upsert_prospect(
            {"name": "Unknown", "phone": from_phone, "source": "inbound_unknown", "status": "new"}
        )

    # Pull history BEFORE logging this message, so triage/SDR get "prior
    # context" and "new message" as clearly separate things.
    history = get_conversation_history(prospect["id"])

    log_message(
        prospect_id=prospect["id"],
        direction="inbound",
        body=body,
        phone=from_phone,
        twilio_sid=message_sid,
        metadata=form,  # full raw webhook payload - From, To, NumMedia, etc.
    )
    touch_last_reply(prospect["id"])

    if body.lower() in STOP_KEYWORDS:
        update_prospect_status(prospect["id"], status="opted_out", opted_out=True)
        add_suppression(from_phone, reason="opt_out", prospect_id=prospect["id"])
        return EMPTY_TWIML

    with trace(f"Inbound reply - {prospect['name']}"):
        triage = await classify(history, body)

        if triage.intent == "opt_out":
            update_prospect_status(prospect["id"], status="opted_out", opted_out=True)
            add_suppression(from_phone, reason="opt_out", prospect_id=prospect["id"])
            return EMPTY_TWIML

        status_map = {
            "interested": "interested",
            "not_interested": "not_interested",
            "question": "replied",
            "hot_lead": "replied",
            "unclear": "replied",
        }
        update_prospect_status(prospect["id"], status=status_map[triage.intent])

        prompt = build_conversation_prompt(prospect, history, body)
        await Runner.run(sdr_agent, prompt)

    return EMPTY_TWIML


@router.post("/status")
async def status_callback(request: Request):
    form = dict(await request.form())

    if settings.APP_BASE_URL and not _validate_signature(request, form):
        logger.warning("Invalid Twilio signature on status callback webhook - rejecting.")
        return Response(status_code=403)

    message_sid = form.get("MessageSid")
    message_status = form.get("MessageStatus")
    if message_sid and message_status:
        update_message_status(message_sid, message_status)

    return Response(status_code=204)
