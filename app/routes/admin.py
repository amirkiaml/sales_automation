"""
Password-protected operator console for the real system - not the public
demo. Shows real prospects, real conversation threads, and the actual
pending-review queue, with the ability to approve/edit/send replies and
trigger the poller directly from the browser instead of the terminal.
This exists mainly so the workflow is screen-recordable for a portfolio
video; app/review_pending.py and app/poll_inbound.py still work fine as
CLI tools if you'd rather use those.
"""
import secrets

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db.client import (
    list_prospects, get_prospect_by_id, list_pending_replies,
    clear_pending_reply, get_conversation_history, get_demo_stats,
)
from app.tools.twilio_sms import send_sms
from app.tools.rate_limit import check_and_record
from app.poll_inbound import poll_once

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


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

    if not settings.DEMO_ADMIN_PASSWORD or not secrets.compare_digest(password, settings.DEMO_ADMIN_PASSWORD):
        return templates.TemplateResponse(request, "admin_login.html", {"error": "Incorrect password."})

    request.session["is_admin"] = True
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, status: str = ""):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospects = list_prospects(status=status or None, limit=30)
    stats = get_demo_stats()
    return templates.TemplateResponse(
        request, "admin_dashboard.html",
        {"prospects": prospects, "stats": stats, "active_status": status}
    )


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


@router.post("/poll")
async def trigger_poll(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect

    count = await poll_once()
    return RedirectResponse(url=f"/admin/review?polled=1&count={count}", status_code=303)


@router.post("/review/{prospect_id}/send")
async def send_reply(request: Request, prospect_id: str, text: str = Form(...)):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if prospect:
        send_sms(
            to_phone=prospect["phone"], body=text, prospect_id=prospect_id,
            agent_name="draft_reply_agent:human_approved",
        )
        clear_pending_reply(prospect_id)
    return RedirectResponse(url="/admin/review", status_code=303)


@router.post("/review/{prospect_id}/discard")
async def discard_reply(request: Request, prospect_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect

    clear_pending_reply(prospect_id)
    return RedirectResponse(url="/admin/review", status_code=303)


@router.get("/prospects/{prospect_id}", response_class=HTMLResponse)
async def prospect_detail(request: Request, prospect_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if not prospect:
        return RedirectResponse(url="/admin", status_code=303)

    messages = get_conversation_history(prospect_id, limit=100)
    return templates.TemplateResponse(
        request, "admin_prospect_detail.html", {"prospect": prospect, "messages": messages}
    )


@router.post("/prospects/{prospect_id}/send")
async def manual_send(request: Request, prospect_id: str, text: str = Form(...)):
    redirect = _require_login(request)
    if redirect:
        return redirect

    prospect = get_prospect_by_id(prospect_id)
    if prospect:
        send_sms(to_phone=prospect["phone"], body=text, prospect_id=prospect_id, agent_name="manual:admin")
    return RedirectResponse(url=f"/admin/prospects/{prospect_id}", status_code=303)
