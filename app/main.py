"""
Run locally with:
    uvicorn app.main:app --reload --port 8000

Then tunnel it publicly for Twilio to reach (see docs/local_testing.md):
    ngrok http 8000
"""
import logging
import secrets as _secrets

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routes.webhook import router as webhook_router
from app.routes.admin import router as admin_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

app = FastAPI(title="VoiceCaptures SMS Outreach")

# Falls back to a per-process random secret if none is set - admin
# sessions just won't survive a server restart in that case, which is a
# reasonable default for local dev but should be set explicitly in
# production so logins don't get silently invalidated on every deploy.
session_secret = settings.SESSION_SECRET_KEY or _secrets.token_hex(32)
if not settings.SESSION_SECRET_KEY:
    logger.warning("SESSION_SECRET_KEY not set - using a random one-time secret. Admin logins won't survive a restart.")
app.add_middleware(SessionMiddleware, secret_key=session_secret)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(webhook_router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/admin")


@app.on_event("startup")
async def check_config():
    missing = settings.validate_core()
    if missing:
        logger.warning(
            "Missing required env vars: %s. The app will run but calls that "
            "need them will fail.",
            ", ".join(missing),
        )


@app.get("/health")
async def health():
    return {"status": "ok"}
