"""
Password-protected operator console for the real system. Shows real prospects, real conversation threads, and the actual
pending-review queue, with the ability to approve/edit/send replies and
trigger the poller directly from the browser instead of the terminal.
This exists mainly so the workflow is screen-recordable for a portfolio
video; app/review_pending.py and app/poll_inbound.py still work fine as
CLI tools if you'd rather use those.
"""
import csv
import io
import json
import secrets

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db.client import (
    list_prospects_page, search_prospects, get_prospect_by_id, list_pending_replies,
    clear_pending_reply, get_conversation_history, get_pipeline_stats, upsert_prospect, set_pending_reply,
    set_autopilot, update_prospect_status, delete_messages_for_prospect, set_history_cleared_at,
    update_prospect, DuplicatePhoneError, list_prospects_sharing_phone,
    delete_prospect,
)
from app.db.import_csv import import_rows, normalize_phone, InvalidPhoneError
from app.agents.cold_outreach import run_cold_outreach_for_prospect
from app.agents.draft_reply_agent import draft_reply
from app.tools.twilio_sms import send_sms, SendFailed
from app.tools.rate_limit import check_and_record
from app.poll_inbound import poll_once, poll_prospect

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _detail_with_error(request: Request, prospect_id: str, error: str):
    """Re-render the detail page with an error, without polling Twilio."""
    p = get_prospect_by_id(prospect_id)
    return templates.TemplateResponse(
        request, "admin_prospect_detail.html",
        {
            "prospect": p,
            "messages": get_conversation_history(prospect_id, limit=100),
            "new_count": 0, "trace": None, "error": error,
            "sharing": list_prospects_sharing_phone(p["phone"], p["id"]) if p else [],
        },
    )


def _require_login(request: Request):
    """Returns a redirect response if not logged in, else None."""
    if not _is_logged_in(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    if not check_and_record(f"login:{client_ip}"):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Too many attempts - try again later."}
        )

    if not settings.ADMIN_PASSWORD or not secrets.compare_digest(password, settings.ADMIN_PASSWORD):
        return templates.TemplateResponse(request, "admin_login.html", {"error": "Incorrect password."})

    request.session["is_admin"] = True
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request, status: str = "", q: str = "",
    imported: str = "", skipped: str = "",
    page: int = 1, page_size: int = 20,
):
    redirect = _require_login(request)
    if redirect:
        return redirect

    total = None
    if q.strip():
        prospects = search_prospects(q.strip(), limit=50)
    else:
        prospects, total = list_prospects_page(status=status or None, page=page, page_size=page_size)

    stats = get_pipeline_stats()
    total_pages = (total // page_size + (1 if total % page_size else 0)) if total is not None else None

    return templates.TemplateResponse(
        request, "admin_dashboard.html",
        {
            "prospects": prospects, "stats": stats, "active_status": status, "q": q,
            "imported": imported, "skipped": skipped,
            "page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
        }
    )


@router.post("/prospects/add")
async def add_prospect(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    primary_type: str = Form(""),
):
    redirect = _require_login(request)
    if redirect:
        return redirect

    upsert_prospect({
        "name": name, "phone": normalize_phone(phone), "primary_type": primary_type or None,
        "source": "manual_admin", "status": "new",
    })
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/prospects/import")
async def import_prospects_csv(request: Request, file: UploadFile = File(...)):
    redirect = _require_login(request)
    if redirect:
        return redirect

    raw = (await file.read()).decode("utf-8", errors="replace")
    sample = raw[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
    except csv.Error:
        dialect = csv.excel  # fall back to comma-delimited if sniffing fails on a small/odd file

    reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
    imported, skipped = import_rows(reader)
    return RedirectResponse(url=f"/admin?imported={imported}&skipped={skipped}", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request, polled: str = "", count: str = ""):
    redirect = _require_login(request)
    if redirect:
        return redirect

    pending = list_pending_replies()
    return templates.TemplateResponse(
        request, "admin_review.html",
        {"pending": pending, "polled": bool(polled), "poll_count": count}
    )


@router.post("/poll", response_class=HTMLResponse)
async def trigger_poll(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect

    count, trace = await poll_once()
    pending = list_pending_replies()
    return templates.TemplateResponse(
        request, "admin_review.html",
        {"pending": pending, "polled": True, "poll_count": count, "trace": trace}
    )


@router.post("/review/{prospect_id}/send")
async def send_reply(request: Request, prospect_id: str, text: str = Form(...), next: str = Form("/admin/review")):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if prospect:
        try:
            send_sms(
                to_phone=prospect["phone"], body=text, prospect_id=prospect_id,
                agent_name="draft_reply_agent:human_approved",
            )
        except SendFailed as e:
            # Leave the pending reply in place so the operator can retry
            # after fixing the number - discarding it would lose the draft.
            return _detail_with_error(request, prospect_id, str(e))
        clear_pending_reply(prospect_id)
    return RedirectResponse(url=next, status_code=303)


@router.post("/review/{prospect_id}/discard")
async def discard_reply(request: Request, prospect_id: str, next: str = Form("/admin/review")):
    redirect = _require_login(request)
    if redirect:
        return redirect

    clear_pending_reply(prospect_id)
    return RedirectResponse(url=next, status_code=303)


@router.post("/prospects/{prospect_id}/generate")
async def generate_reply(request: Request, prospect_id: str):
    """Two different agents depending on context, not one pipeline for
    everything:
      - No conversation history yet -> the real cold-outreach pipeline
        (hook agent + 3 personas + picker), same as a fresh cold text,
        compliance footer included.
      - Already mid-conversation -> the single-tone draft_reply_agent,
        responding to their actual last message with full history as
        context - not a fresh pitch that ignores what's already been said.
    """
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if not prospect:
        return RedirectResponse(url="/admin", status_code=303)

    history = get_conversation_history(prospect_id, limit=20)

    if not history:
        result = await run_cold_outreach_for_prospect(prospect, dry_run=True)
        suggested = result["sent_text"]
        context = f"First outreach — angle: {result['hook_angle']} — picked {result['winner']} ({result['winner_reason']})"
    else:
        last_inbound = next((m["body"] for m in reversed(history) if m["direction"] == "inbound"), None)
        prompt_message = last_inbound or "(No reply from them yet - draft a natural, low-pressure follow-up.)"
        suggested = await draft_reply(prospect, history=history, new_message=prompt_message)
        context = prompt_message

    set_pending_reply(prospect_id, pending_reply=suggested, context=context)
    return RedirectResponse(url=f"/admin/prospects/{prospect_id}", status_code=303)


@router.post("/prospects/{prospect_id}/autopilot")
async def toggle_autopilot(request: Request, prospect_id: str, enabled: bool = Form(...)):
    redirect = _require_login(request)
    if redirect:
        return redirect

    set_autopilot(prospect_id, enabled=enabled)
    return RedirectResponse(url=f"/admin/prospects/{prospect_id}", status_code=303)


@router.post("/prospects/{prospect_id}/delete-history")
async def delete_history(request: Request, prospect_id: str):
    """Permanently delete this prospect's message history.

    Order matters: set the cleared-at cursor BEFORE deleting rows. Twilio
    never deletes anything on its end, so if the rows go first and this
    call fails in between, the next poll re-imports everything and the
    delete silently undoes itself. Setting the cursor first fails safe -
    worst case the cursor is set and the rows are still there.

    Relevant here specifically because prospect_detail() polls Twilio on
    every render, so the redirect below triggers a poll immediately.
    """
    redirect = _require_login(request)
    if redirect:
        return redirect

    if not get_prospect_by_id(prospect_id):
        return RedirectResponse(url="/admin", status_code=303)

    set_history_cleared_at(prospect_id)
    delete_messages_for_prospect(prospect_id)
    clear_pending_reply(prospect_id)
    return RedirectResponse(url=f"/admin/prospects/{prospect_id}", status_code=303)


@router.post("/prospects/{prospect_id}/edit")
async def edit_prospect(
    request: Request,
    prospect_id: str,
    name: str = Form(...),
    phone: str = Form(...),
    primary_type: str = Form(""),
    neighborhood: str = Form(""),
    address: str = Form(""),
    rating: str = Form(""),
    review_count: str = Form(""),
    website: str = Form(""),
    maps_url: str = Form(""),
    opening_hours: str = Form(""),
):
    """Edit a prospect's business/contact details.

    Exists mainly so a prospect can be pointed at your own number to test
    the live pipeline end to end without messaging a real contractor.

    Deliberately does NOT expose `status`, `opted_out` or `autopilot`.
    opted_out especially: a plain form field that can flip it back to
    false is a compliance footgun on real prospects, and it belongs
    behind its own explicit control if you want it at all.
    """
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if not prospect:
        return RedirectResponse(url="/admin", status_code=303)

    def _num(raw, cast):
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return cast(raw)
        except ValueError:
            return None

    try:
        normalized_phone = normalize_phone(phone)
    except InvalidPhoneError:
        return _detail_with_error(
            request, prospect_id,
            f"'{phone}' isn't a usable phone number. Use full E.164 - "
            "a Toronto number is +14168226186, not +4168226186.",
        )

    fields = {
        "name": name.strip(),
        "phone": normalized_phone,
        "primary_type": primary_type.strip() or None,
        "neighborhood": neighborhood.strip() or None,
        "address": address.strip() or None,
        "rating": _num(rating, float),
        "review_count": _num(review_count, int),
        "website": website.strip() or None,
        "maps_url": maps_url.strip() or None,
    }

    # opening_hours is jsonb; edited as raw JSON. Bad JSON is rejected
    # rather than silently wiping the column.
    raw_hours = (opening_hours or "").strip()
    if raw_hours:
        try:
            fields["opening_hours"] = json.loads(raw_hours)
        except json.JSONDecodeError:
            return _detail_with_error(request, prospect_id, "Opening hours must be valid JSON.")
    else:
        fields["opening_hours"] = None

    # Duplicates are allowed (migration 006) so this normally won't fire;
    # kept so the code is still correct if the constraint is restored.
    try:
        update_prospect(prospect_id, fields)
    except DuplicatePhoneError:
        return _detail_with_error(
            request, prospect_id,
            f"{fields['phone']} already belongs to another prospect.",
        )

    return RedirectResponse(url=f"/admin/prospects/{prospect_id}", status_code=303)


@router.post("/prospects/{prospect_id}/delete")
async def delete_prospect_route(request: Request, prospect_id: str):
    """Delete a prospect entirely. Messages go with it via FK cascade.

    If the prospect had opted out, delete_prospect() writes the number to
    the suppression list first - the row goes, the do-not-contact
    obligation does not.
    """
    redirect = _require_login(request)
    if redirect:
        return redirect

    delete_prospect(prospect_id)
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/prospects/{prospect_id}", response_class=HTMLResponse)
async def prospect_detail(request: Request, prospect_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if not prospect:
        return RedirectResponse(url="/admin", status_code=303)

    # Check Twilio for anything new from just this number before rendering,
    # so opening the conversation is enough to see a reply come through -
    # no separate "check for messages" step needed.
    new_count, trace = await poll_prospect(prospect)
    if new_count:
        prospect = get_prospect_by_id(prospect_id)  # re-fetch: status/pending_reply may have changed

    messages = get_conversation_history(prospect_id, limit=100)
    return templates.TemplateResponse(
        request, "admin_prospect_detail.html",
        {
            "prospect": prospect, "messages": messages,
            "new_count": new_count, "trace": trace,
            "sharing": list_prospects_sharing_phone(prospect["phone"], prospect["id"]),
        },
    )


@router.post("/prospects/{prospect_id}/send")
async def manual_send(request: Request, prospect_id: str, text: str = Form(...)):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if prospect:
        try:
            send_sms(to_phone=prospect["phone"], body=text, prospect_id=prospect_id, agent_name="manual:admin")
        except SendFailed as e:
            return _detail_with_error(request, prospect_id, str(e))
    return RedirectResponse(url=f"/admin/prospects/{prospect_id}", status_code=303)
