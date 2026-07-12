"""
Run locally with:
    uvicorn app.main:app --reload --port 8000

Then tunnel it publicly for Twilio to reach (see docs/local_testing.md):
    ngrok http 8000
"""
import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.config import settings
from app.routes.webhook import router as webhook_router
from app.routes.demo import router as demo_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

app = FastAPI(title="VoiceCaptures SMS Outreach")
app.include_router(webhook_router)
app.include_router(demo_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/demo")


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
