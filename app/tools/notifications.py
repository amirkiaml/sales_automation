"""
Pushover push notifications - alerts you to a new inbound reply with its
suggested response, so you know to go review it. Same service the
original lab uses as its email fallback; here it's the primary alert
channel for the human-in-the-loop approval flow.
"""
import requests

from app.config import settings

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def send_pushover(title: str, message: str) -> None:
    # Hardcoded off - not settings-driven, so a stray PUSHOVER_ENABLED=true
    # left over in .env from earlier testing can't accidentally turn this
    # back on. To re-enable, remove this early return deliberately.
    print(f"[Pushover disabled] {title}: {message}")
    return


def notify_pending_reply(prospect_name: str, inbound_body: str, suggested_reply: str) -> None:
    send_pushover(
        title=f"Reply needed: {prospect_name}",
        message=f"They said: {inbound_body}\n\nSuggested reply: {suggested_reply}\n\nReview: python -m app.review_pending",
    )
