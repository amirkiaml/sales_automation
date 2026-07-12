"""
Public-facing demo page - lets someone without command-line access (a
recruiter, for instance) try the agent pipelines in a browser.

Safety model: a site visitor can NEVER trigger a real Twilio send. Their
"request a real text" option only writes to the pending_reply/Pushover
approval queue (app/review_pending.py) - same path real prospect replies
use. The one exception is the site owner: an optional password field,
checked server-side against DEMO_ADMIN_PASSWORD, unlocks an instant real
send that bypasses the queue. If DEMO_ADMIN_PASSWORD is unset in .env,
that path is disabled entirely - there's no password to match.
"""
import base64
import json
import secrets

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.agents.cold_outreach import run_cold_outreach_for_prospect
from app.agents.drafting_agents import DRAFTING_AGENTS
from app.agents.draft_reply_agent import draft_reply
from app.agents.triage_agent import classify as classify_intent
from app.db.client import upsert_prospect, set_pending_reply, get_demo_stats
from app.tools.notifications import notify_pending_reply
from app.tools.twilio_sms import send_sms
from app.tools.rate_limit import check_and_record

router = APIRouter(tags=["demo"])
templates = Jinja2Templates(directory="app/templates")

# Real agent names for the "written by" / "picked by" labels, so the UI
# names the actual Agent objects rather than the internal dict keys.
AGENT_DISPLAY_NAMES = {label: agent.name for label, agent in DRAFTING_AGENTS.items()}


def _encode_history(history: list[dict]) -> str:
    return base64.urlsafe_b64encode(json.dumps(history).encode()).decode()


def _decode_history(encoded: str) -> list[dict]:
    if not encoded:
        return []
    try:
        return json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except Exception:
        return []


def _is_admin(password: str) -> bool:
    if not settings.DEMO_ADMIN_PASSWORD or not password:
        return False
    return secrets.compare_digest(password, settings.DEMO_ADMIN_PASSWORD)


@router.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    return templates.TemplateResponse(request, "demo.html", {"stats": get_demo_stats()})


@router.post("/demo/cold-outreach", response_class=HTMLResponse)
async def demo_cold_outreach(
    request: Request,
    name: str = Form(...),
    primary_type: str = Form(""),
    rating: str = Form(""),
    review_count: str = Form(""),
    opening_hours: str = Form(""),
    request_real_text: bool = Form(False),
    phone: str = Form(""),
    admin_password: str = Form(""),
):
    stats = get_demo_stats()
    cold_form = {
        "name": name, "primary_type": primary_type, "rating": rating,
        "review_count": review_count, "opening_hours": opening_hours,
        "phone": phone, "request_real_text": request_real_text,
    }

    prospect = {
        "id": "demo-preview",
        "name": name,
        "primary_type": primary_type or None,
        "rating": float(rating) if rating.strip() else None,
        "review_count": int(review_count) if review_count.strip() else None,
        "opening_hours": {"note": opening_hours} if opening_hours.strip() else {},
    }

    is_admin = _is_admin(admin_password)

    if is_admin and request_real_text and phone:
        client_ip = request.client.host if request.client else "unknown"
        if not check_and_record(f"admin:{client_ip}"):
            result = await run_cold_outreach_for_prospect(prospect, dry_run=True)
            cold_result = _cold_result_dict(result, request_error="Admin send rate limit hit - try again shortly.")
        else:
            admin_prospect = upsert_prospect({
                "name": name, "phone": phone, "primary_type": primary_type or None,
                "rating": float(rating) if rating.strip() else None,
                "review_count": int(review_count) if review_count.strip() else None,
                "opening_hours": {"note": opening_hours} if opening_hours.strip() else {},
                "source": "demo_admin", "status": "new",
            })
            result = await run_cold_outreach_for_prospect(admin_prospect, dry_run=False)
            cold_result = _cold_result_dict(result, sent_now=True)
        return templates.TemplateResponse(
            request, "demo.html", {"cold_form": cold_form, "cold_result": cold_result, "stats": stats, "agent_names": AGENT_DISPLAY_NAMES}
        )

    if request_real_text and admin_password and not is_admin:
        result = await run_cold_outreach_for_prospect(prospect, dry_run=True)
        cold_result = _cold_result_dict(result, request_error="Incorrect password.")
        return templates.TemplateResponse(
            request, "demo.html", {"cold_form": cold_form, "cold_result": cold_result, "stats": stats, "agent_names": AGENT_DISPLAY_NAMES}
        )

    result = await run_cold_outreach_for_prospect(prospect, dry_run=True)
    cold_result = _cold_result_dict(result)

    if request_real_text and phone:
        client_ip = request.client.host if request.client else "unknown"
        if not check_and_record(client_ip):
            cold_result["request_error"] = "Too many demo requests from this visitor for now - try again later."
        else:
            demo_prospect = upsert_prospect({
                "name": name, "phone": phone, "source": "demo_request", "status": "new",
            })
            set_pending_reply(
                demo_prospect["id"], pending_reply=result["sent_text"],
                context="[Demo] cold outreach draft requested for review",
            )
            notify_pending_reply(f"[DEMO] {name}", "(cold outreach demo request)", result["sent_text"])
            cold_result["requested"] = True

    return templates.TemplateResponse(
        request, "demo.html", {"cold_form": cold_form, "cold_result": cold_result, "stats": stats, "agent_names": AGENT_DISPLAY_NAMES}
    )


def _cold_result_dict(result: dict, **overrides) -> dict:
    base = {
        "hook_angle": result["hook_angle"],
        "drafts": result["drafts"],
        "winner": result["winner"],
        "winner_reason": result.get("winner_reason"),
        "requested": False,
    }
    base.update(overrides)
    return base


@router.post("/demo/conversation/start", response_class=HTMLResponse)
async def demo_conversation_start(
    request: Request,
    name: str = Form(...),
    primary_type: str = Form(""),
):
    """Kicks off the chat with a real sample cold-outreach message, so the
    visitor gets to reply to something realistic instead of typing into a
    blank box pretending they already received a text."""
    stats = get_demo_stats()
    prospect = {"id": "demo-preview", "name": name, "primary_type": primary_type or None}
    result = await run_cold_outreach_for_prospect(prospect, dry_run=True)

    chat_history = [{"direction": "outbound", "body": result["sent_text"], "flag": None}]
    conv_form = {"name": name, "primary_type": primary_type, "history": _encode_history(chat_history)}
    conv_result = {"transcript": chat_history, "requested": False, "ended": False}

    return templates.TemplateResponse(
        request, "demo.html", {"conv_form": conv_form, "conv_result": conv_result, "stats": stats}
    )


@router.post("/demo/conversation", response_class=HTMLResponse)
async def demo_conversation(
    request: Request,
    name: str = Form(""),
    primary_type: str = Form(""),
    message: str = Form(...),
    history: str = Form(""),
    request_real_text: bool = Form(False),
    phone: str = Form(""),
    admin_password: str = Form(""),
):
    stats = get_demo_stats()
    chat_history = _decode_history(history)
    prospect = {"name": name or "Demo Business", "primary_type": primary_type or None}

    reply_text = await draft_reply(prospect, history=chat_history, new_message=message)

    # Same triage agent the real pipeline uses, run here purely to decide
    # whether this demo conversation would be handed to a human in the
    # real system - nothing is sent, just simulated and shown.
    triage = await classify_intent(chat_history, message)
    flag = "needs_human" if triage.intent == "hot_lead" else None

    chat_history.append({"direction": "inbound", "body": message, "flag": None})
    chat_history.append({"direction": "outbound", "body": reply_text, "flag": flag})

    conv_form = {"name": name, "primary_type": primary_type, "history": _encode_history(chat_history)}
    conv_result = {"transcript": chat_history, "requested": False, "ended": flag == "needs_human"}

    is_admin = _is_admin(admin_password)

    if is_admin and request_real_text and phone:
        client_ip = request.client.host if request.client else "unknown"
        if not check_and_record(f"admin:{client_ip}"):
            conv_result["request_error"] = "Admin send rate limit hit - try again shortly."
        else:
            admin_prospect = upsert_prospect({
                "name": name or "Demo Business", "phone": phone, "source": "demo_admin", "status": "new",
            })
            send_result = send_sms(
                to_phone=phone, body=reply_text, prospect_id=admin_prospect["id"],
                agent_name="draft_reply_agent:demo_admin",
            )
            conv_result["sent_now"] = True
            conv_result["twilio_sid"] = send_result["sid"]
        return templates.TemplateResponse(
            request, "demo.html", {"conv_form": conv_form, "conv_result": conv_result, "stats": stats}
        )

    if request_real_text and admin_password and not is_admin:
        conv_result["request_error"] = "Incorrect password."
        return templates.TemplateResponse(
            request, "demo.html", {"conv_form": conv_form, "conv_result": conv_result, "stats": stats}
        )

    if request_real_text and phone:
        client_ip = request.client.host if request.client else "unknown"
        if not check_and_record(client_ip):
            conv_result["request_error"] = "Too many demo requests from this visitor for now - try again later."
        else:
            demo_prospect = upsert_prospect({
                "name": name or "Demo Business", "phone": phone, "source": "demo_request", "status": "new",
            })
            set_pending_reply(
                demo_prospect["id"], pending_reply=reply_text,
                context=f"[Demo] conversation request - last message: {message}",
            )
            notify_pending_reply(f"[DEMO] {name or 'Demo visitor'}", message, reply_text)
            conv_result["requested"] = True

    return templates.TemplateResponse(
        request, "demo.html", {"conv_form": conv_form, "conv_result": conv_result, "stats": stats}
    )
