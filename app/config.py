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

    # Twilio
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
    TWILIO_MESSAGING_SERVICE_SID: str = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")

    # Supabase (new sb_publishable_/sb_secret_ key system, not the legacy
    # anon/service_role JWTs — see https://supabase.com/docs/guides/getting-started/api-keys)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SECRET_KEY: str = os.getenv("SUPABASE_SECRET_KEY", "")

    # App
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
    PUSHOVER_USER: str = os.getenv("PUSHOVER_USER", "")
    PUSHOVER_TOKEN: str = os.getenv("PUSHOVER_TOKEN", "")
    DEMO_ADMIN_PASSWORD: str = os.getenv("DEMO_ADMIN_PASSWORD", "")
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
