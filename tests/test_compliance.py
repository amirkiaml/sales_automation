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


class TestComplianceFooter:
    """CASL form requirements, pinned.

    Section 6 requires sender identification, contact information, and a
    working unsubscribe mechanism in every commercial electronic message.
    A form violation stands regardless of whether consent existed, so this
    cannot live in a prompt - it was an instruction for a while, which is
    the same mistake as the curly apostrophes with legal weight attached.
    """

    def test_footer_is_appended_when_missing(self):
        from app.agents.drafting_agents import (
            COMPLIANCE_FOOTER, append_compliance_footer)
        assert append_compliance_footer("Some draft.").endswith(COMPLIANCE_FOOTER)

    def test_footer_is_not_duplicated(self):
        from app.agents.drafting_agents import (
            COMPLIANCE_FOOTER, append_compliance_footer)
        once = append_compliance_footer("Some draft.")
        assert append_compliance_footer(once) == once

    def test_footer_names_a_standard_opt_out_keyword(self):
        """CRTC guidance for SMS names STOP and UNSUBSCRIBE.

        "NO to opt-out" was the old wording. The system honoured STOP the
        whole time and never said so.
        """
        from app.agents.drafting_agents import COMPLIANCE_FOOTER
        assert "STOP" in COMPLIANCE_FOOTER

    def test_footer_carries_contact_information(self):
        """A business name alone is not enough under s.6(2).

        For SMS, a link to a page carrying the name and mailing address is
        accepted in place of putting the address inline.
        """
        from app.agents.drafting_agents import COMPLIANCE_FOOTER
        assert "voicecaptures.com" in COMPLIANCE_FOOTER

    def test_the_opt_out_keyword_is_actually_honoured(self):
        """The footer must not promise an opt-out the code ignores.

        Only checks the word offered for opting out. YES also appears in
        the footer but is an interest keyword handled by triage, not an
        unsubscribe - an earlier version of this test flagged it, which
        was the test being wrong rather than the footer.
        """
        import re

        from app.agents.drafting_agents import COMPLIANCE_FOOTER
        from app.routes.webhook import STOP_KEYWORDS

        match = re.search(r"([A-Z]{3,})\s+to\s+opt", COMPLIANCE_FOOTER)
        assert match, "the footer does not name an opt-out keyword at all"
        assert match.group(1).lower() in STOP_KEYWORDS, (
            f"the footer tells prospects to reply {match.group(1)!r} but "
            f"STOP_KEYWORDS does not contain it"
        )


class TestLineTypeGuard:
    """A number known not to accept SMS must never be sent to.

    Found the hard way: 4 of the first 5 real messages failed with Twilio
    error 30006, landline or unreachable carrier. Business numbers on
    Google Maps are usually office landlines, and every end-to-end test
    for three days had gone to a mobile.
    """

    def test_landline_is_refused(self, db, twilio):
        from app.tools.twilio_sms import NotSendable, send_sms

        p = db.seed_prospect(phone="+14165551234", line_type="landline")
        with pytest.raises(NotSendable):
            send_sms(to_phone=p["phone"], body="hi", prospect_id=p["id"])
        assert twilio.sent == [], "a landline received a message"

    def test_deliverable_number_sends(self, db, twilio):
        from app.tools.twilio_sms import send_sms

        p = db.seed_prospect(phone="+14165559999", line_type="deliverable")
        send_sms(to_phone=p["phone"], body="hi", prospect_id=p["id"])
        assert len(twilio.sent) == 1

    def test_unchecked_number_still_sends(self, db, twilio):
        """The first send is what produces the evidence.

        Refusing unchecked numbers would mean never learning any line
        type at all, since the delivery result is the only signal
        available while Lookup is blocked for Canadian numbers.
        """
        from app.tools.twilio_sms import send_sms

        p = db.seed_prospect(phone="+14165558888")  # line_type absent
        send_sms(to_phone=p["phone"], body="hi", prospect_id=p["id"])
        assert len(twilio.sent) == 1

    @pytest.mark.parametrize("line_type", ["voicemail", "pager", "premium", "wat"])
    def test_unrecognised_types_are_refused(self, db, twilio, line_type):
        """Allowlist, not blocklist.

        Twilio returns eleven line types and can add more. An unfamiliar
        value should stop a send rather than sail through it.
        """
        from app.tools.twilio_sms import NotSendable, send_sms

        p = db.seed_prospect(phone="+14165557777", line_type=line_type)
        with pytest.raises(NotSendable):
            send_sms(to_phone=p["phone"], body="hi", prospect_id=p["id"])
        assert twilio.sent == []


class TestDeliveryClassification:
    """Only a conclusive result may stamp line_type_checked_at."""

    def test_landline_error_is_conclusive(self):
        from app.check_delivery import classify
        assert classify("undelivered", 30006) == "landline"

    def test_delivered_means_deliverable_not_mobile(self):
        """A delivered message does not prove a handset - fixedVoip
        receives SMS fine. What the guard needs is whether it arrives."""
        from app.check_delivery import classify
        assert classify("delivered", None) == "deliverable"

    @pytest.mark.parametrize("status,code", [
        ("queued", None), ("sending", None), ("sent", None),
        ("failed", 30003), ("failed", 30005),
    ])
    def test_inconclusive_results_return_none(self, status, code):
        """None keeps the row eligible for a later answer.

        30003 and 30005 are unreachable-handset and unknown-destination:
        usually about the moment, not the line. Recording them would
        permanently exclude numbers that work tomorrow.
        """
        from app.check_delivery import classify
        assert classify(status, code) is None
