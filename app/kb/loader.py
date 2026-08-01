"""
Loads and searches the VoiceCaptures knowledge base.

Deliberately not a vector store. The KB is a couple of dozen facts about
one product; dense retrieval over that is strictly worse than literal
matching (it returns a nearest neighbour for everything, including "what's
the weather in the UK"), and it would add an embedding dependency to a
path that runs on every inbound SMS. Retrieval here should return NOTHING
for an off-topic question - that empty result is a signal the rest of the
system depends on, and a similarity search never produces it.

If this file ever grows past ~100 entries, revisit: the cost is a linear
scan per message, which is fine at this size and not at that one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

KB_PATH = Path(__file__).parent / "voicecaptures.yaml"

# Words too common to carry meaning when scoring overlap.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do",
    "does", "for", "from", "get", "has", "have", "how", "i", "if", "in",
    "is", "it", "its", "me", "my", "no", "not", "of", "on", "or", "so",
    "that", "the", "their", "them", "then", "there", "they", "this", "to",
    "up", "us", "was", "we", "what", "when", "who", "will", "with", "you",
    "your", "yes", "ok", "okay",
    # Contraction forms, apostrophe already stripped by _tokens. Without
    # these, "whats" is treated as a content word and scores against any
    # trigger phrased as a question.
    "whats", "thats", "hows", "wheres", "whos", "whens", "im", "ive", "id",
    "dont", "doesnt", "isnt", "arent", "cant", "wont", "youre", "youve",
    "its", "lets", "well", "theres", "heres", "gonna", "wanna",
}

# One overlapping CONTENT word is enough - "robot" alone is strong
# evidence for the is-it-a-robot entry. The earlier off-topic false
# positive came from "whats" being treated as content, which the stopword
# list above now handles. Raising this to require two overlaps was tried
# and rejected: it silenced legitimate one-keyword questions, which is a
# worse failure than the one it prevented.
MIN_RETRIEVAL_SCORE = 2.0


@dataclass(frozen=True)
class Entry:
    id: str
    topic: str
    triggers: tuple[str, ...]
    answer: str


@dataclass(frozen=True)
class Restricted:
    id: str
    triggers: tuple[str, ...]
    reason: str
    holding_reply: str = ""


@dataclass
class KnowledgeBase:
    entries: list[Entry] = field(default_factory=list)
    restricted: list[Restricted] = field(default_factory=list)
    handoff_triggers: tuple[str, ...] = ()
    kb_gap_holding_reply: str = ""
    handoff_holding_reply: str = ""

    def by_id(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None)


def _tokens(text: str) -> set[str]:
    """Content words only, apostrophes stripped.

    The regex used to keep apostrophes, so "what's" tokenised as "what's"
    and never matched the stopword "what" - it survived as a content word
    and scored +2 overlap against any trigger phrased as a question. That
    made "what's the weather like in the uk" retrieve the product-overview
    entry, breaking the property the whole design rests on: off-topic
    messages must retrieve NOTHING.
    """
    words = re.findall(r"[a-z']+", text.lower())
    cleaned = (w.replace("'", "") for w in words)
    return {w for w in cleaned if w not in _STOPWORDS and len(w) > 2}


@lru_cache(maxsize=1)
def load_kb() -> KnowledgeBase:
    """Parse and validate the KB. Cached; call load_kb.cache_clear() to reload.

    Validation is strict and raises at import time rather than at 2am on a
    live inbound message. A malformed KB should stop the app from starting,
    because the failure mode of a silently-empty KB is an agent that
    answers everything from parametric knowledge - exactly what this
    module exists to prevent.
    """
    raw = yaml.safe_load(KB_PATH.read_text(encoding="utf-8")) or {}

    entries: list[Entry] = []
    seen: set[str] = set()
    for item in raw.get("entries") or []:
        for required in ("id", "answer", "triggers"):
            if not item.get(required):
                raise ValueError(f"KB entry missing '{required}': {item.get('id', item)!r}")
        if item["id"] in seen:
            raise ValueError(f"Duplicate KB entry id: {item['id']!r}")
        seen.add(item["id"])
        entries.append(
            Entry(
                id=item["id"],
                topic=item.get("topic", "general"),
                triggers=tuple(t.lower() for t in item["triggers"]),
                answer=" ".join(item["answer"].split()),
            )
        )

    restricted = [
        Restricted(
            id=item["id"],
            triggers=tuple(t.lower() for t in item.get("triggers", [])),
            reason=item.get("reason", "restricted topic"),
            holding_reply=" ".join((item.get("holding_reply") or "").split()),
        )
        for item in raw.get("restricted") or []
    ]

    handoff = tuple(t.lower() for t in (raw.get("handoff") or {}).get("triggers", []))

    if not entries:
        raise ValueError("KB has no entries - the agent would have nothing to ground on.")

    return KnowledgeBase(
        entries=entries, restricted=restricted, handoff_triggers=handoff,
        kb_gap_holding_reply=" ".join((raw.get("kb_gap_holding_reply") or "").split()),
        handoff_holding_reply=" ".join(((raw.get("handoff") or {}).get("holding_reply") or "").split()),
    )


def _phrase_in(phrase: str, text: str) -> bool:
    """Whole-word phrase match.

    Was a plain `phrase in text` substring test, which fired on any word
    that happened to CONTAIN a trigger: "fee" matched "feet", so "do you
    like feet" was classified as a pricing question and answered with a
    holding reply about exact numbers. "rate" would match "accurate",
    "api" would match "rapid", "cost" would match "costume".

    \b handles both ends, so multi-word triggers ("how much") still work.
    """
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def match_restricted(message: str) -> Restricted | None:
    """Return the restricted topic this message hits, if any.

    Deterministic and checked before any model sees the message. Pricing
    is not a judgement call - a model that decides a pricing question is
    'close enough to answer' has already caused the problem.
    """
    low = message.lower()
    for r in load_kb().restricted:
        if any(_phrase_in(trigger, low) for trigger in r.triggers):
            return r
    return None


def holding_reply_for(topic_id: str) -> str:
    """The verbatim acknowledgement to send when a topic is blocked."""
    r = next((x for x in load_kb().restricted if x.id == topic_id), None)
    return r.holding_reply if r else ""


def wants_human(message: str) -> bool:
    low = message.lower()
    return any(_phrase_in(t, low) for t in load_kb().handoff_triggers)


def search(message: str, limit: int = 3) -> list[Entry]:
    """Return KB entries relevant to this message, best first.

    Returns an EMPTY list when nothing matches. That is the point: an
    off-topic question ('weather in the UK') produces no entries, and the
    caller treats no-entries as out-of-scope rather than as a weak match.
    """
    low = message.lower()
    msg_tokens = _tokens(message)
    scored: list[tuple[float, Entry]] = []

    for entry in load_kb().entries:
        score = 0.0
        # A literal trigger phrase is strong evidence.
        for trigger in entry.triggers:
            if _phrase_in(trigger, low):
                score += 10.0 + len(trigger) / 10.0
        # Token overlap against triggers catches paraphrases.
        trigger_tokens = _tokens(" ".join(entry.triggers))
        overlap = msg_tokens & trigger_tokens
        score += 2.0 * len(overlap)

        if score >= MIN_RETRIEVAL_SCORE:
            scored.append((score, entry))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [entry for _, entry in scored[:limit]]


def format_for_prompt(entries: list[Entry]) -> str:
    if not entries:
        return "(no knowledge base entries matched this message)"
    return "\n".join(f"[{e.id}] {e.answer}" for e in entries)
