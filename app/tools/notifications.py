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
    if not settings.PUSHOVER_USER or not settings.PUSHOVER_TOKEN:
        print(f"[Pushover not configured - would have sent] {title}: {message}")
        return

    requests.post(PUSHOVER_URL, data={
        "user": settings.PUSHOVER_USER,
        "token": settings.PUSHOVER_TOKEN,
        "title": title,
        "message": message,
    })


def notify_pending_reply(prospect_name: str, inbound_body: str, suggested_reply: str) -> None:
    send_pushover(
        title=f"Reply needed: {prospect_name}",
        message=f"They said: {inbound_body}\n\nSuggested reply: {suggested_reply}\n\nReview: python -m app.review_pending",
    )
