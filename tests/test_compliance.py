"""
The rules that must always hold.

Everything else in this suite tests behaviour. This file tests
obligations: things where a failure is a legal problem or a real person
receiving a message they told us not to send. They are asserted against
fakes rather than mocks, so "is this number still suppressed after the
prospect was deleted?" gets a real answer from real stored state.

Every test here exists because of something that actually happened or
came close to happening:

  - A blocked message producing silence. Twice, from two different
    causes: an unnamed restricted topic, and a dedup rule that was right
    until scope blocks started disabling autopilot. Neither was caught by
    a test; both were caught by reading a live transcript.

  - Sending to an opted-out number. Caught in testing by the suppression
    check inside send_sms, which is the only reason it did not go out.

  - An opt-out being forgotten. Deleting a prospect cascades to their
    messages; if the opt-out lived only on the prospect row, a re-import
    of the same lead CSV would resurrect them.

The value is in the interactions. Each rule was correct in isolation
every time it broke.
"""
from __future__ import annotations

import pytest

from tests.fakes import FakeSupabase, FakeTwilio


@pytest.fixture
def db(monkeypatch):
    """Swap the Supabase client for an in-memory fake."""
    from app.db import client as client_module

    fake = FakeSupabase()
    monkeypatch.setattr(client_module, "get_client", lambda: fake)
    return fake


@pytest.fixture
def twilio(monkeypatch):
    """Swap the Twilio client for one that records instead of sending."""
    from app.tools import twilio_sms

    fake = FakeTwilio()
    monkeypatch.setattr(twilio_sms, "get_twilio_client", lambda: fake)
    return fake


class TestSuppressionBlocksSending:
    """An opted-out number must never receive a message. No exceptions."""

    def test_send_to_suppressed_number_raises(self, db, twilio):
        from app.db.client import add_suppression
        from app.tools.twilio_sms import SendFailed, send_sms

        p = db.seed_prospect(phone="+14165551234")
        add_suppression("+14165551234", reason="opt_out", prospect_id=p["id"])

        with pytest.raises(SendFailed):
            send_sms(to_phone="+14165551234", body="hello", prospect_id=p["id"])

        assert twilio.sent == [], "a suppressed number received a message"

    def test_unsuppressed_number_sends_normally(self, db, twilio):
        from app.tools.twilio_sms import send_sms

        p = db.seed_prospect(phone="+14165559999")
        send_sms(to_phone="+14165559999", body="hello", prospect_id=p["id"])

        assert len(twilio.sent) == 1

    def test_check_lives_in_send_not_the_call_site(self, db, twilio):
        """Any future call path inherits the guard for free.

        The check is inside send_sms rather than at each caller precisely
        so a new code path cannot forget it. This asserts that placement,
        not just the behaviour.
        """
        import inspect

        from app.tools import twilio_sms

        source = inspect.getsource(twilio_sms.send_sms)
        assert "is_suppressed" in source, (
            "the suppression check has moved out of send_sms - every call "
            "site now has to remember it, which is how it gets forgotten"
        )


class TestOptOutOutlivesTheProspect:
    """The obligation has to survive the row that happened to carry it."""

    def test_deleting_an_opted_out_prospect_keeps_the_suppression(self, db):
        from app.db.client import add_suppression, delete_prospect, is_suppressed

        p = db.seed_prospect(phone="+14165551111", opted_out=True)
        add_suppression("+14165551111", reason="opt_out", prospect_id=p["id"])

        delete_prospect(p["id"])

        assert db.rows("prospects") == []
        assert is_suppressed("+14165551111"), (
            "deleting the prospect forgot the opt-out - re-importing the "
            "same lead CSV would now message someone who said stop"
        )

    def test_reimporting_the_lead_does_not_resurrect_them(self, db):
        from app.db.client import add_suppression, is_suppressed, upsert_prospect

        add_suppression("+14165552222", reason="opt_out")

        # Same number arrives again in a fresh CSV, with opted_out unset.
        upsert_prospect({"name": "Test Plumbing", "phone": "+14165552222",
                         "status": "new", "opted_out": False})

        assert is_suppressed("+14165552222"), (
            "a re-imported lead cleared an existing opt-out"
        )

    def test_suppressions_have_no_foreign_key_to_prospects(self, db):
        """Recorded without a prospect_id at all, e.g. an unknown number."""
        from app.db.client import add_suppression, is_suppressed

        add_suppression("+14165553333", reason="opt_out")
        assert is_suppressed("+14165553333")


class TestStopKeywordsBypassTheAgents:
    """Compliance must not depend on a model classifying correctly."""

    @pytest.mark.parametrize(
        "body", ["STOP", "stop", "Stop", "  STOP  ", "unsubscribe", "QUIT", "cancel"]
    )
    def test_stop_variants_are_recognised(self, body):
        from app.routes.webhook import STOP_KEYWORDS

        assert body.strip().lower() in STOP_KEYWORDS

    def test_stop_check_runs_before_the_triage_agent(self):
        """Ordering, asserted on the source.

        A model deciding whether STOP means stop is the wrong shape for
        this. The keyword check has to short-circuit first, and this
        pins the order so a refactor cannot quietly swap them.
        """
        import inspect

        from app.routes import webhook

        source = inspect.getsource(webhook)
        stop_at = source.index("STOP_KEYWORDS")
        stop_check = source.index("body.lower() in STOP_KEYWORDS")
        triage_at = source.index("await classify(")
        assert stop_check < triage_at, (
            "the STOP check no longer runs before triage - an opt-out now "
            "depends on a model classifying it correctly"
        )
        assert stop_at < triage_at


class TestBlockedMessagesAreNeverSilent:
    """A refused message must still produce a reply.

    This broke twice from unrelated causes. The prospect asked about
    price, got blocked, and heard nothing - which is the worst possible
    response to the highest-intent message in the funnel.
    """

    def test_every_restricted_topic_has_a_holding_reply(self):
        from app.kb.loader import holding_reply_for, load_kb

        for topic in load_kb().restricted:
            assert holding_reply_for(topic.id), (
                f"{topic.id} blocks a message and says nothing back"
            )

    def test_handoff_and_kb_gap_have_holding_replies(self):
        from app.kb.loader import load_kb

        kb = load_kb()
        assert kb.handoff_holding_reply, "asking for a person gets silence"
        assert kb.kb_gap_holding_reply, "an uncovered question gets silence"

    def test_scope_blocks_do_not_dedup(self):
        """The rule that broke, pinned.

        Dedup suppresses a canned line identical to the previous outbound.
        That was correct while scope blocks left autopilot running and
        could fire repeatedly. Once they started disabling autopilot they
        could not repeat, and dedup stopped preventing spam and started
        causing silence.

        Nothing in the code connects those two rules, so this test is the
        connection.
        """
        import inspect

        from app.agents import autopilot

        source = inspect.getsource(autopilot.run_autopilot)
        block = source[: source.index("kb_retrieval")]
        assert "dedup=False" in block, (
            "the scope-block holding reply is deduping again - if the same "
            "line was the last outbound, the prospect gets silence"
        )

    def test_holding_replies_make_no_factual_claims(self):
        """They bypass the grounding guardrail, so they must assert nothing.

        A digit is the cheapest canary: the moment a price or a timeframe
        appears in one, an unverified claim is going out unchecked.
        """
        from app.kb.loader import holding_reply_for, load_kb

        kb = load_kb()
        replies = [holding_reply_for(t.id) for t in kb.restricted]
        replies += [kb.handoff_holding_reply, kb.kb_gap_holding_reply]
        for reply in replies:
            assert not any(ch.isdigit() for ch in reply), (
                f"holding reply contains a number: {reply!r}"
            )


class TestOutboundIsSafeToSend:
    """Everything leaving the system passes through one sanitizer."""

    def test_non_gsm_characters_are_stripped(self, db, twilio):
        from app.tools.twilio_sms import send_sms

        p = db.seed_prospect(phone="+14165554444")
        send_sms(
            to_phone="+14165554444",
            body="It\u2019s here \U0001F60A \u2014 ready",
            prospect_id=p["id"],
        )

        body = twilio.sent[0]["body"]
        assert "\u2019" not in body and "\U0001F60A" not in body and "\u2014" not in body
        assert body == "It's here - ready"

    def test_sanitizer_runs_before_the_suppression_check(self, db, twilio):
        """Order matters less here, but a message that skips sanitizing
        would bill at UCS-2 rates. Pinning that it happens at all."""
        import inspect

        from app.tools import twilio_sms

        source = inspect.getsource(twilio_sms.send_sms)
        assert "sanitize_for_sms" in source
