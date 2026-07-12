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
"""
import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.tools.twilio_sms import get_twilio_client
from app.db.client import (
    get_prospect_by_phone,
    upsert_prospect,
    log_message,
    update_prospect_status,
    touch_last_reply,
    get_conversation_history,
    message_exists,
    get_last_inbound_timestamp,
    set_pending_reply,
)
from app.agents.triage_agent import classify
from app.agents.draft_reply_agent import draft_reply
from app.tools.notifications import notify_pending_reply

STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
LOOKBACK_MINUTES_IF_NO_HISTORY = 60  # first-ever run: only look back this far


def _fetch_new_inbound_messages() -> list:
    client = get_twilio_client()

    cursor = get_last_inbound_timestamp()
    if cursor:
        after = datetime.fromisoformat(cursor)
    else:
        after = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES_IF_NO_HISTORY)

    messages = client.messages.list(to=settings.TWILIO_FROM_NUMBER, date_sent_after=after)
    # Twilio's list includes both directions to this number - keep only
    # genuine inbound replies, and skip anything we've already logged
    # (the cursor is inclusive, so the boundary message often repeats).
    return [
        m for m in messages
        if m.direction == "inbound" and not message_exists(m.sid)
    ]


async def _process_message(msg) -> None:
    from_phone = msg.from_
    body = (msg.body or "").strip()

    prospect = get_prospect_by_phone(from_phone)
    if prospect is None:
        prospect = upsert_prospect(
            {"name": "Unknown", "phone": from_phone, "source": "inbound_unknown", "status": "new"}
        )

    history = get_conversation_history(prospect["id"])

    log_message(
        prospect_id=prospect["id"],
        direction="inbound",
        body=body,
        twilio_sid=msg.sid,
        metadata={"date_sent": str(msg.date_sent), "num_media": msg.num_media},
    )
    touch_last_reply(prospect["id"])

    if body.lower() in STOP_KEYWORDS:
        update_prospect_status(prospect["id"], status="opted_out", opted_out=True)
        print(f"[opt-out] {prospect['name']} ({from_phone})")
        return

    triage = await classify(history, body)

    if triage.intent == "opt_out":
        update_prospect_status(prospect["id"], status="opted_out", opted_out=True)
        print(f"[opt-out, via triage] {prospect['name']} ({from_phone})")
        return

    status_map = {
        "interested": "interested",
        "not_interested": "not_interested",
        "question": "replied",
        "hot_lead": "needs_human",  # skip the queue, this one's worth a fast look
        "unclear": "replied",
    }
    update_prospect_status(prospect["id"], status=status_map[triage.intent])

    suggested = await draft_reply(prospect, history, body)
    set_pending_reply(prospect["id"], pending_reply=suggested, context=body)
    notify_pending_reply(prospect["name"], body, suggested)

    print(f"[pending review] {prospect['name']} ({from_phone}) - intent: {triage.intent}")


async def poll_once() -> int:
    new_messages = _fetch_new_inbound_messages()
    for msg in new_messages:
        await _process_message(msg)
    return len(new_messages)


async def watch(interval: int) -> None:
    print(f"Polling every {interval}s. Ctrl+C to stop.")
    while True:
        count = await poll_once()
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
        count = asyncio.run(poll_once())
        print(f"Done. Processed {count} new message(s).")
