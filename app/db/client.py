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
    """Insert a prospect, or update the existing one with this phone number.

    Implemented as an explicit lookup-then-write rather than Postgres
    ON CONFLICT (phone): migration 006 dropped the unique constraint on
    phone so several prospects can share a number for testing, and
    ON CONFLICT requires a unique index on the conflict target.

    When duplicates exist this updates the most recently touched one,
    matching get_prospect_by_phone's routing rule so an import and an
    inbound message always resolve to the same row.

    Not atomic - two concurrent imports of the same number can both see
    "no match" and insert. Acceptable here: imports are operator-initiated
    and single-threaded. If that changes, the fix is a unique index on
    (phone) with a partial predicate excluding test rows, not a lock.
    """
    existing = get_prospect_by_phone(prospect["phone"])
    if existing:
        payload = {k: v for k, v in prospect.items() if k != "id"}
        result = (
            get_client().table("prospects")
            .update(payload).eq("id", existing["id"]).execute()
        )
        return result.data[0] if result.data else existing

    result = get_client().table("prospects").insert(prospect).execute()
    return result.data[0]


def get_prospect_by_phone(phone: str) -> Optional[dict[str, Any]]:
    """Resolve an inbound number to a prospect.

    Phone numbers are no longer unique (migration 006), so this picks the
    most recently updated match. That makes routing deterministic instead
    of "whatever Postgres returned first", and gives the intended testing
    behaviour: point a prospect at your own number and it immediately
    becomes the one that receives replies, because editing it bumps
    updated_at.

    Consequence worth knowing: the previous holder of that number stops
    receiving inbound until it is touched again.
    """
    result = (
        get_client()
        .table("prospects")
        .select("*")
        .eq("phone", phone)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def list_prospects_sharing_phone(phone: str, exclude_id: str = "") -> list[dict[str, Any]]:
    """Other prospects on the same number - drives the admin warning."""
    result = (
        get_client().table("prospects")
        .select("id,name,status,updated_at")
        .eq("phone", phone)
        .order("updated_at", desc=True)
        .execute()
    )
    return [r for r in (result.data or []) if r["id"] != exclude_id]


def update_prospect_status(prospect_id: str, status: str, **extra_fields) -> None:
    payload = {"status": status, **extra_fields}
    get_client().table("prospects").update(payload).eq("id", prospect_id).execute()


class DuplicatePhoneError(Exception):
    """Raised when an edit would collide with another prospect's number."""


def update_prospect(prospect_id: str, fields: dict[str, Any]) -> None:
    """Update arbitrary prospect columns by id.

    Distinct from upsert_prospect(), which conflicts on `phone` - that is
    correct for CSV import but wrong here, because changing a phone number
    would insert a second row instead of editing the existing one.

    `phone` is UNIQUE, so an edit can collide with another prospect. That
    surfaces as a Postgres 23505 and is re-raised as DuplicatePhoneError
    so the caller can show a real message instead of a 500.
    """
    try:
        get_client().table("prospects").update(fields).eq("id", prospect_id).execute()
    except APIError as e:
        if getattr(e, "code", None) == "23505" or "23505" in str(e):
            raise DuplicatePhoneError(fields.get("phone", "")) from e
        raise


def add_suppression(phone: str, reason: str, prospect_id: str = "", note: str = "") -> None:
    """Record a permanent do-not-contact for this number.

    Append-only and independent of the prospects table, so an opt-out
    survives the prospect being deleted or the CSV being re-imported.
    """
    payload = {"phone": phone, "reason": reason, "note": note or None}
    if prospect_id:
        payload["prospect_id"] = prospect_id
    try:
        get_client().table("suppressions").insert(payload).execute()
    except APIError as e:
        # Already suppressed - the obligation is already recorded, so the
        # duplicate is a no-op rather than an error.
        if getattr(e, "code", None) == "23505" or "23505" in str(e):
            return
        raise


def is_suppressed(phone: str) -> bool:
    result = (
        get_client().table("suppressions")
        .select("phone").eq("phone", phone).limit(1).execute()
    )
    return bool(result.data)


def delete_prospect(prospect_id: str) -> None:
    """Delete a prospect and, by FK cascade, all of its messages.

    Any opt-out is written to `suppressions` first. Deleting the row must
    not delete the obligation - see migration 007.
    """
    prospect = get_prospect_by_id(prospect_id)
    if not prospect:
        return
    if prospect.get("opted_out"):
        add_suppression(
            prospect["phone"], reason="prospect_deleted",
            prospect_id=prospect_id,
            note=f"opted-out prospect '{prospect.get('name', '')}' deleted",
        )
    get_client().table("prospects").delete().eq("id", prospect_id).execute()


def save_trace(
    prospect_id: str, entry_point: str, trigger_text: str, outcome: str,
    steps: list[dict[str, Any]], duration_ms: int,
) -> None:
    get_client().table("agent_traces").insert({
        "prospect_id": prospect_id, "entry_point": entry_point,
        "trigger_text": trigger_text[:500], "outcome": outcome,
        "steps": steps, "duration_ms": duration_ms,
    }).execute()


def get_traces_for_prospect(prospect_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Newest first. Returns [] if the table doesn't exist yet.

    Tolerates a missing table on purpose: tracing is a debugging aid, and
    an operator who hasn't run migration 008 should get a console without
    traces rather than a 500 on every prospect page.
    """
    try:
        result = (
            get_client().table("agent_traces").select("*")
            .eq("prospect_id", prospect_id)
            .order("created_at", desc=True).limit(limit).execute()
        )
        return result.data or []
    except APIError:
        return []


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
