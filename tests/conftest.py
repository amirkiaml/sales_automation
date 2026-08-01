"""
Lets the fast tests import from app/ without a configured environment.

app.config reads Supabase and OpenAI settings at import time. The tests in
this directory exercise pure functions that touch neither, so dummy values
are injected before any app import - otherwise running the suite would
require live credentials to test a regex.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-token")
os.environ.setdefault("TWILIO_FROM_NUMBER", "+15550000000")
