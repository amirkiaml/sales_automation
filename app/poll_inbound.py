"""
Polls Twilio for new inbound messages instead of using a webhook - this
number's webhook is already owned by another app (Lovable), so instead of
fighting over that single URL slot, we just periodically ask Twilio's API
"anything new since I last checked?" Zero webhook config touched, zero
risk to whatever else is running on that number.

Usage:
    python -m app.poll_inbound              # check once, exit
    python -m app.poll_inbound --watch       # check every 60s, forever
    python -m app.poll_inbound --watch --interval 30

poll_once() returns (count, trace) where trace is a list of {"label",
"detail"} steps describing exactly what happened - used by the admin
console to show the pipeline running step by step, not just a final
count. Sending only ever happens on human approval (review_pending.py or
/admin/review) - nothing in this file sends a reply.
"""
import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone

from agents import Runner, trace as agent_trace

from app.config import settings
from app.tools.twilio_sms import get_twilio_client
from app.db.client import (
    get_prospect_by_phone,
    upsert_prospect,
    log_message,
    update_prospect_status,
    add_suppression,
    touch_last_reply,
    get_conversation_history,
    message_exists,
    get_last_inbound_timestamp,
    get_last_inbound_timestamp_for_prospect,
    set_pending_reply,
)
from app.agents.triage_agent import classify
from app.agents.draft_reply_agent import draft_reply
from app.agents.sdr_agent import sdr_agent, build_conversation_prompt

STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
LOOKBACK_MINUTES_IF_NO_HISTORY = 60  # first-ever run: only look back this far


def _step(trace: list, label: str, detail: str = "") -> None:
    trace.append({"label": label, "detail": detail})


def _fetch_new_inbound_messages() -> list:
    client = get_twilio_client()

    cursor = get_last_inbound_timestamp()
    if cursor:
        after = datetime.fromisoformat(cursor)
    else:
        after = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES_IF_NO_HISTORY)

    messages = client.messages.list(to=settings.TWILIO_FROM_NUMBER, date_sent_after=after)
    return [
        m for m in messages
        if m.direction == "inbound" and not message_exists(m.sid)
    ]


async def _process_message(msg, trace: list) -> None:
    from_phone = msg.from_
    body = (msg.body or "").strip()

    prospect = get_prospect_by_phone(from_phone)
    if prospect is None:
        prospect = upsert_prospect(
            {"name": "Unknown", "phone": from_phone, "source": "inbound_unknown", "status": "new"}
        )
        _step(trace, "New number", f"{from_phone} hasn't texted before - added as a new prospect")

    # Fixed here, not just in poll_prospect()'s cursor calc, because this
    # function is the one shared entry point BOTH poll_once() (global,
    # used by the background --watch process) and poll_prospect()
    # (per-contact) call. A cursor-only fix only protects whichever path
    # happens to compute it - this protects every path, unconditionally.
    cleared_at_raw = prospect.get("history_cleared_at")
    if cleared_at_raw and msg.date_sent:
        cleared_at = datetime.fromisoformat(cleared_at_raw)
        msg_sent_at = msg.date_sent if msg.date_sent.tzinfo else msg.date_sent.replace(tzinfo=timezone.utc)
        if msg_sent_at <= cleared_at:
            _step(trace, "Skipped", f'{prospect["name"]}: message predates this contact\'s history-cleared marker')
            return

    _step(trace, "Message received", f'{prospect["name"]} ({from_phone}): "{body}"')

    history = get_conversation_history(prospect["id"])

    logged = log_message(
        prospect_id=prospect["id"],
        direction="inbound",
        body=body,
        phone=from_phone,
        twilio_sid=msg.sid,
        metadata={"date_sent": str(msg.date_sent), "num_media": msg.num_media},
    )
    if logged is None:
        _step(trace, "Already processed", "a concurrent poller already handled this exact message - skipping to avoid a duplicate draft")
        print(f"[skipped - duplicate] {prospect['name']} ({from_phone})")
        return

    touch_last_reply(prospect["id"])
    _step(trace, "Logged to database", "messages table, prospect.last_reply_at updated")

    if body.lower() in STOP_KEYWORDS:
        update_prospect_status(prospect["id"], status="opted_out", opted_out=True)
        add_suppression(from_phone, reason="opt_out", prospect_id=prospect["id"])
        _step(trace, "Compliance handler", f'"{body}" matched an opt-out keyword - marked opted_out, stopped here')
        print(f"[opt-out] {prospect['name']} ({from_phone})")
        return

    _step(trace, "Triage agent running", "classifying intent against the conversation so far")
    triage = await classify(history, body)
    _step(trace, "Triage result", f"intent = {triage.intent} (confidence {triage.confidence:.2f}) - {triage.reason}")

    if triage.intent == "opt_out":
        update_prospect_status(prospect["id"], status="opted_out", opted_out=True)
        add_suppression(from_phone, reason="opt_out", prospect_id=prospect["id"])
        _step(trace, "Compliance handler", "triage classified this as an opt-out request - marked opted_out, stopped here")
        print(f"[opt-out, via triage] {prospect['name']} ({from_phone})")
        return

    status_map = {
        "interested": "interested",
        "not_interested": "not_interested",
        "question": "replied",
        "hot_lead": "needs_human",
        "unclear": "replied",
    }
    update_prospect_status(prospect["id"], status=status_map[triage.intent])
    _step(trace, "Prospect status updated", f'status set to "{status_map[triage.intent]}"')

    if prospect.get("autopilot"):
        _step(trace, "Autopilot active", "SDR agent will reply and send on its own for this contact")
        with agent_trace(f"Autopilot reply - {prospect['name']}"):
            await Runner.run(sdr_agent, build_conversation_prompt(prospect, history, body))
        _step(trace, "Autopilot reply sent", "no human review needed - see conversation for what was sent")
        return

    _step(trace, "Draft agent running", "reading full conversation history, writing a suggested reply")
    suggested = await draft_reply(prospect, history, body)
    _step(trace, "Suggested reply drafted", suggested)

    set_pending_reply(prospect["id"], pending_reply=suggested, context=body)
    _step(trace, "Queued for review", "no notification sent - check /admin/review or this contact's page")

    print(f"[pending review] {prospect['name']} ({from_phone}) - intent: {triage.intent}")


async def poll_once() -> tuple[int, list[dict]]:
    trace = []
    _step(trace, "Checking Twilio", f"looking for messages to {settings.TWILIO_FROM_NUMBER}")

    new_messages = _fetch_new_inbound_messages()
    _step(trace, "Found", f"{len(new_messages)} new message(s)")

    for msg in new_messages:
        await _process_message(msg, trace)

    return len(new_messages), trace


async def poll_prospect(prospect: dict) -> tuple[int, list[dict]]:
    """Scoped version of poll_once() for a single contact's page - checks
    only for messages from this specific phone number. Uses a per-prospect
    cursor (not the global one) so it can't miss a message just because a
    DIFFERENT prospect texted more recently and advanced the global cursor
    past this one's last-seen timestamp.

    Also respects history_cleared_at: Twilio never deletes anything on
    its end, so after a "delete history" action, the cursor must never
    look earlier than the clear point - otherwise the next poll would
    re-import everything Twilio still has, undoing the deletion."""
    trace = []
    client = get_twilio_client()

    message_cursor = get_last_inbound_timestamp_for_prospect(prospect["id"])
    cleared_cursor = prospect.get("history_cleared_at")
    candidates = [c for c in (message_cursor, cleared_cursor) if c]

    if candidates:
        after = max(datetime.fromisoformat(c) for c in candidates)
    else:
        after = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES_IF_NO_HISTORY)

    messages = client.messages.list(
        to=settings.TWILIO_FROM_NUMBER, from_=prospect["phone"], date_sent_after=after
    )
    new_messages = [m for m in messages if m.direction == "inbound" and not message_exists(m.sid)]

    for msg in new_messages:
        await _process_message(msg, trace)

    return len(new_messages), trace


async def watch(interval: int) -> None:
    print(f"Polling every {interval}s. Ctrl+C to stop.")
    while True:
        count, _trace = await poll_once()
        if count:
            print(f"Processed {count} new message(s).")
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    if args.watch:
        asyncio.run(watch(args.interval))
    else:
        count, _trace = asyncio.run(poll_once())
        print(f"Done. Processed {count} new message(s).")
