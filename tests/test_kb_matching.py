"""
Knowledge base matching: the deterministic layer that decides, before any
model runs, whether a message is a restricted topic, a request for a
human, or something the agent may answer.

Regression origin: matching was a plain `trigger in message` substring
test, so any word CONTAINING a trigger fired it. "fee" matched "feet", so
"do you like feet" was classified as a pricing question and answered with
a holding reply about exact numbers. "rate" matched "accurate", "api"
matched "rapid", "cost" matched "costume".

This layer is the hard guarantee in the system - the classifier behind it
is probabilistic, this is not - so it gets the strictest tests.

No API key, no database, no network.
"""
import pytest

from app.kb.loader import (
    holding_reply_for,
    load_kb,
    match_restricted,
    search,
    wants_human,
)


class TestSubstringRegression:
    """Words that merely CONTAIN a trigger must not fire it."""

    # NOTE: "that quote was accurate" was in this list and failed - but
    # correctly, because "quote" is itself a pricing trigger, not a
    # substring collision. The test was wrong, not the code. Kept as a
    # comment because "the test was wrong" is a real outcome worth
    # recording rather than quietly deleting.
    @pytest.mark.parametrize(
        "message, contained_trigger",
        [
            ("do you like feet", "fee"),
            ("you like feet?", "fee"),
            ("we need a rapid response", "api"),
            ("he wore a costume", "cost"),
            ("my son plays in a band", "and"),
        ],
    )
    def test_containing_word_does_not_trigger(self, message, contained_trigger):
        assert match_restricted(message) is None, (
            f"{message!r} wrongly matched on the substring {contained_trigger!r}"
        )


class TestRestrictedTopics:
    @pytest.mark.parametrize(
        "message, expected_topic",
        [
            ("how much does it cost", "pricing"),
            ("No what's the pricing", "pricing"),
            ("what do you charge", "pricing"),
            ("is there a contract", "contract_terms"),
            ("can I cancel anytime", "contract_terms"),
            ("do you record calls", "legal_compliance"),
            ("does it integrate with Jobber", "integrations_specifics"),
        ],
    )
    def test_restricted_topics_are_caught_by_keywords(self, message, expected_topic):
        # These must never depend on a model. A sampled token deciding
        # whether a pricing question is "close enough to answer" is the
        # failure this layer exists to make impossible.
        match = match_restricted(message)
        assert match is not None, f"{message!r} was not caught"
        assert match.id == expected_topic

    def test_every_restricted_topic_has_a_holding_reply(self):
        # A topic without one blocks the message AND says nothing back,
        # which is silence in response to the highest-intent messages in
        # the funnel.
        for topic in load_kb().restricted:
            assert holding_reply_for(topic.id), (
                f"restricted topic {topic.id!r} has no holding_reply - "
                "prospects asking about it get silence"
            )

    def test_holding_replies_state_no_numbers(self):
        # Holding replies are sent unchecked by the grounding guardrail
        # precisely because they assert nothing. A digit is the canary for
        # that assumption breaking.
        for topic in load_kb().restricted:
            reply = holding_reply_for(topic.id)
            assert not any(ch.isdigit() for ch in reply), (
                f"{topic.id} holding_reply contains a number: {reply!r}. "
                "These are sent without a grounding check - keep them claim-free."
            )


class TestHandoffDetection:
    @pytest.mark.parametrize(
        "message",
        ["can I talk to someone", "is there a real person", "who is this", "call me"],
    )
    def test_requests_for_a_human_are_caught(self, message):
        assert wants_human(message) is True

    @pytest.mark.parametrize(
        "message", ["what is voicecaptures", "sounds good", "how does it work"],
    )
    def test_ordinary_messages_are_not_handoffs(self, message):
        assert wants_human(message) is False


class TestRetrieval:
    @pytest.mark.parametrize(
        "message, expected_id",
        [
            ("what is voicecaptures", "what_is_voicecaptures"),
            ("is this a robot", "is_it_a_robot"),
            ("are you alive", "is_it_a_robot"),
            ("do you have a free trial", "free_trial"),
        ],
    )
    def test_covered_questions_retrieve_the_right_entry(self, message, expected_id):
        assert expected_id in [e.id for e in search(message)]

    @pytest.mark.parametrize(
        "message",
        [
            "what's the weather like in the uk",
            "who won the game last night",
            "lol",
            "asdfgh",
        ],
    )
    def test_off_topic_retrieves_nothing(self, message):
        # THE load-bearing property. An empty result is what tells the
        # agent it may assert nothing. A similarity search would return a
        # nearest neighbour for every one of these and need an arbitrary
        # threshold to reject it; this returns [] on its own.
        assert search(message) == []


class TestKnowledgeBaseIntegrity:
    def test_kb_loads_and_is_not_empty(self):
        assert load_kb().entries

    def test_entry_ids_are_unique(self):
        ids = [e.id for e in load_kb().entries]
        assert len(ids) == len(set(ids))

    def test_no_unverified_placeholders_remain(self):
        # Fails until the placeholder answers are replaced with real
        # product facts. Everything the agent says is grounded in this
        # file, so a placeholder here is a false claim texted to a real
        # contractor. Delete the entry or fill it in - both make this pass.
        unverified = [e.id for e in load_kb().entries if "UNVERIFIED" in e.answer]
        assert not unverified, (
            f"KB entries still contain placeholder text: {unverified}. "
            "Replace with verified facts or delete the entry."
        )
