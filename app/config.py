"""
Central place to load and validate environment variables.
Import `settings` anywhere in the app instead of calling os.getenv directly.
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)


class Settings:
    # OpenAI / Agents SDK
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    TRIAGE_MODEL: str = os.getenv("TRIAGE_MODEL", "gpt-4o-mini")
    AGENT_MODEL: str = os.getenv("AGENT_MODEL", "gpt-4o")
    # Cold outreach: hook, three drafters, picker. Writing a 120-character
    # cold text is not a frontier-model task, and running five gpt-4o calls
    # per prospect burned most of the 30k TPM budget - a 50-prospect batch
    # lost one lead to a 429. Override if the drafts get noticeably worse.
    DRAFTING_MODEL: str = os.getenv("DRAFTING_MODEL", "gpt-4o-mini")

    # Twilio
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
    # USD per SMS *segment* (not per message). Twilio list price for
    # US/Canada as of Aug 2026. This is a FLOOR: Canadian carrier fees are
    # billed on top and vary by destination carrier, and failed messages
    # add a $0.001 processing fee. Check Console > Billing for your real
    # effective rate and override here.
    TWILIO_COST_PER_SEGMENT: float = float(os.getenv("TWILIO_COST_PER_SEGMENT", "0.0083"))
    TWILIO_MESSAGING_SERVICE_SID: str = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")

    # Supabase (new sb_publishable_/sb_secret_ key system, not the legacy
    # anon/service_role JWTs — see https://supabase.com/docs/guides/getting-started/api-keys)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SECRET_KEY: str = os.getenv("SUPABASE_SECRET_KEY", "")

    # App
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "")

    def validate_core(self) -> list[str]:
        """Return a list of missing required vars. Doesn't raise -
        lets the app boot and surface a clear startup warning instead."""
        required = {
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
            "TWILIO_ACCOUNT_SID": self.TWILIO_ACCOUNT_SID,
            "TWILIO_AUTH_TOKEN": self.TWILIO_AUTH_TOKEN,
            "TWILIO_FROM_NUMBER": self.TWILIO_FROM_NUMBER,
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_SECRET_KEY": self.SUPABASE_SECRET_KEY,
        }
        return [name for name, value in required.items() if not value]


settings = Settings()
