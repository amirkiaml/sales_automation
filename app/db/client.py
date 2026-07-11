"""
Thin Supabase client wrapper. Every other module talks to the DB through
these functions rather than importing the Supabase client directly -
keeps query logic in one place and makes it easy to swap the backend later.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import create_client, Client

from app.config import settings

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SECRET_KEY:
            raise RuntimeError(
                "SUPABASE_URL / SUPABASE_SECRET_KEY not set. Check your .env."
            )
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SECRET_KEY)
    return _client


# ---------------------------------------------------------------------
# Prospects
# ---------------------------------------------------------------------

def upsert_prospect(prospect: dict[str, Any]) -> dict[str, Any]:
    """Insert a prospect, or update it if the phone number already exists."""
    result = get_client().table("prospects").upsert(
        prospect, on_conflict="phone"
    ).execute()
    return result.data[0]


def get_prospect_by_phone(phone: str) -> Optional[dict[str, Any]]:
    result = (
        get_client()
        .table("prospects")
        .select("*")
        .eq("phone", phone)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update_prospect_status(prospect_id: str, status: str, **extra_fields) -> None:
    payload = {"status": status, **extra_fields}
    get_client().table("prospects").update(payload).eq("id", prospect_id).execute()


def list_prospects(status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    query = get_client().table("prospects").select("*").limit(limit)
    if status:
        query = query.eq("status", status)
    return query.execute().data


# ---------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------

def log_message(
    prospect_id: str,
    direction: str,
    body: str,
    twilio_sid: Optional[str] = None,
    twilio_status: Optional[str] = None,
    agent_name: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    assert direction in ("outbound", "inbound")
    payload = {
        "prospect_id": prospect_id,
        "direction": direction,
        "body": body,
        "twilio_sid": twilio_sid,
        "twilio_status": twilio_status,
        "agent_name": agent_name,
        "metadata": metadata or {},
    }
    result = get_client().table("messages").insert(payload).execute()
    return result.data[0]


def get_conversation_history(prospect_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent messages for a prospect, oldest first - ready to feed
    straight into an agent as conversation context."""
    result = (
        get_client()
        .table("messages")
        .select("*")
        .eq("prospect_id", prospect_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(result.data))
