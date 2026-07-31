"""
Thin Supabase client wrapper. Every other module talks to the DB through
these functions rather than importing the Supabase client directly -
keeps query logic in one place and makes it easy to swap the backend later.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import create_client, Client
from postgrest.exceptions import APIError

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


def list_prospects_page(
    status: Optional[str] = None, query: Optional[str] = None,
    page: int = 1, page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated prospect list with a real total count, so the dashboard
    can show '1-20 of 484' instead of silently truncating at a fixed
    limit. Filters by search query if given, else by status."""
    offset = (page - 1) * page_size
    q = get_client().table("prospects").select("*", count="exact").order("updated_at", desc=True)
    if query:
        like = f"%{query}%"
        q = q.or_(f"name.ilike.{like},phone.ilike.{like}")
    elif status:
        q = q.eq("status", status)
    result = q.range(offset, offset + page_size - 1).execute()
    return result.data, (result.count or 0)


def list_prospects(status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    query = get_client().table("prospects").select("*").order("updated_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    return query.execute().data


def search_prospects(query: str, limit: int = 50) -> list[dict[str, Any]]:
    like = f"%{query}%"
    result = (
        get_client().table("prospects").select("*")
        .or_(f"name.ilike.{like},phone.ilike.{like}")
        .order("updated_at", desc=True).limit(limit).execute()
    )
    return result.data


def get_prospect_by_id(prospect_id: str) -> Optional[dict[str, Any]]:
    result = get_client().table("prospects").select("*").eq("id", prospect_id).limit(1).execute()
    return result.data[0] if result.data else None


def delete_messages_for_prospect(prospect_id: str) -> None:
    """Permanently deletes every message row for this prospect. Used by
    the admin console's 'delete chat history' action - real deletion
    from Supabase, not a soft flag."""
    get_client().table("messages").delete().eq("prospect_id", prospect_id).execute()


def set_history_cleared_at(prospect_id: str) -> None:
    """Marks 'don't re-import anything from Twilio before this point' -
    without this, the poller's cursor (based on our own now-empty
    messages table) would fall back to a lookback window and resurrect
    everything Twilio still has on its end, since Twilio itself never
    deletes anything."""
    get_client().table("prospects").update(
        {"history_cleared_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", prospect_id).execute()


def set_autopilot(prospect_id: str, enabled: bool) -> None:
    get_client().table("prospects").update({"autopilot": enabled}).eq("id", prospect_id).execute()


def touch_last_reply(prospect_id: str) -> None:
    get_client().table("prospects").update(
        {"last_reply_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", prospect_id).execute()


def update_message_status(twilio_sid: str, status: str) -> None:
    """Used by the Twilio status-callback webhook to update delivery state
    (queued -> sent -> delivered/failed) on an already-logged message."""
    get_client().table("messages").update(
        {"twilio_status": status}
    ).eq("twilio_sid", twilio_sid).execute()


def set_pending_reply(prospect_id: str, pending_reply: str, context: str) -> None:
    get_client().table("prospects").update(
        {"pending_reply": pending_reply, "pending_reply_context": context}
    ).eq("id", prospect_id).execute()


def clear_pending_reply(prospect_id: str) -> None:
    get_client().table("prospects").update(
        {"pending_reply": None, "pending_reply_context": None}
    ).eq("id", prospect_id).execute()


def list_pending_replies() -> list[dict]:
    result = (
        get_client()
        .table("prospects")
        .select("*")
        .not_.is_("pending_reply", "null")
        .execute()
    )
    return result.data


def message_exists(twilio_sid: str) -> bool:
    """Dedup check for the poller - has this Twilio message already been logged?"""
    result = (
        get_client()
        .table("messages")
        .select("id")
        .eq("twilio_sid", twilio_sid)
        .limit(1)
        .execute()
    )
    return len(result.data) > 0


def get_last_inbound_timestamp() -> str | None:
    """Cursor for polling: the created_at of the most recent inbound message
    we've already logged. None if we've never seen one - poller falls back
    to a lookback window in that case."""
    result = (
        get_client()
        .table("messages")
        .select("created_at")
        .eq("direction", "inbound")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0]["created_at"] if result.data else None


def get_last_inbound_timestamp_for_prospect(prospect_id: str) -> str | None:
    """Same as get_last_inbound_timestamp() but scoped to one prospect -
    needed when polling a single contact's page, since the global cursor
    can advance past this contact's last-seen message if someone ELSE
    texted more recently, which would cause their message to be missed."""
    result = (
        get_client()
        .table("messages")
        .select("created_at")
        .eq("direction", "inbound")
        .eq("prospect_id", prospect_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0]["created_at"] if result.data else None


def get_pipeline_stats() -> dict:
    """Aggregate counts only - never returns names, numbers, or message
    content. Safe to show on a public demo page as proof the system is
    live, without exposing any real prospect's data."""
    try:
        total_messages = get_client().table("messages").select("id", count="exact", head=True).execute().count or 0
        pending_review = (
            get_client().table("prospects").select("id", count="exact", head=True)
            .not_.is_("pending_reply", "null").execute().count or 0
        )
        opted_out = (
            get_client().table("prospects").select("id", count="exact", head=True)
            .eq("opted_out", True).execute().count or 0
        )
        return {"total_messages": total_messages, "pending_review": pending_review, "opted_out": opted_out, "available": True}
    except Exception:
        # Public page should never break because a stats query failed.
        return {"available": False}


# ---------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------

def log_message(
    prospect_id: str,
    direction: str,
    body: str,
    phone: Optional[str] = None,
    twilio_sid: Optional[str] = None,
    twilio_status: Optional[str] = None,
    agent_name: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    assert direction in ("outbound", "inbound")
    payload = {
        "prospect_id": prospect_id,
        "direction": direction,
        "body": body,
        "phone": phone,
        "twilio_sid": twilio_sid,
        "twilio_status": twilio_status,
        "agent_name": agent_name,
        "metadata": metadata or {},
    }
    try:
        result = get_client().table("messages").insert(payload).execute()
        return result.data[0]
    except APIError as e:
        if e.code == "23505":
            # Another process already logged this exact Twilio message
            # (unique constraint on twilio_sid) - not an error, just means
            # a concurrent poller got there first. Caller should treat a
            # None return as "skip this message, it's already handled."
            return None
        raise


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
