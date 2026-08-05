"""
Work out which numbers can receive SMS, from delivery results.

    python -m app.check_delivery
    python -m app.check_delivery --dry-run

Twilio's Lookup product answers this directly, but Line Type Intelligence
requires NPAC approval for Canadian numbers and returns error 60601
without it. That approval has no timeline, so this takes the free route:
Twilio already reports the line type after the fact, in the error code on
a failed send.

    error 30006 = "Landline or unreachable carrier"

Which is exactly the question. The cost is one wasted send per number -
$0.0083 - against $0.008 for a lookup, so the economics are a wash, and
these numbers were going to be messaged anyway.

The limitation, which is real: this only protects the SECOND message
onward. The first send is what produces the evidence. `check_carriers.py`
does not have that problem and should be preferred if NPAC access ever
comes through.
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter

from twilio.base.exceptions import TwilioRestException

from app.db.client import list_outbound_sids_needing_status, set_line_type
from app.tools.twilio_sms import get_twilio_client

logger = logging.getLogger(__name__)

# Twilio error codes that tell us something durable about the number
# itself, rather than about one delivery attempt.
#
# 30003/30005 are deliberately absent: unreachable handset and unknown
# destination are usually transient or about the moment, not the line.
# Recording them would permanently exclude numbers that work tomorrow.
PERMANENT_FAILURES = {
    30006: "landline",   # landline or unreachable carrier
    30004: "blocked",    # message blocked by the carrier or recipient
    21614: "landline",   # not SMS-capable
    21211: "invalid",    # not a valid phone number
}


def classify(status: str, error_code: int | None) -> str | None:
    """A durable line type from one message's outcome, or None.

    None means "this tells us nothing durable" - the row keeps its null
    line_type and stays eligible for a later answer. Only a conclusive
    result should stamp checked_at.

    'deliverable' rather than 'mobile' on success, because a delivered
    message does not prove a handset - fixedVoip numbers receive SMS
    perfectly well. What the send guard actually needs to know is whether
    a message can arrive, and that is what this records.
    """
    if status == "delivered":
        return "deliverable"
    if error_code in PERMANENT_FAILURES:
        return PERMANENT_FAILURES[error_code]
    # queued, sending, sent, or a transient failure: not an answer yet.
    return None


def fetch_status(sid: str) -> tuple[str, int | None] | None:
    """Twilio's current status for one message. None if unreachable."""
    try:
        msg = get_twilio_client().messages(sid).fetch()
        return msg.status, msg.error_code
    except TwilioRestException as e:
        logger.error("Could not fetch %s: %s", sid, e)
        return None


def run(dry_run: bool = False, limit: int = 500) -> None:
    rows = list_outbound_sids_needing_status(limit=limit)
    if not rows:
        print("No outbound messages awaiting classification.")
        return

    # One prospect can have several outbound messages. The first
    # conclusive answer wins; later ones cannot contradict a landline.
    seen: set[str] = set()
    outcomes: Counter[str] = Counter()
    inconclusive = 0

    print(f"Checking {len(rows)} outbound messages...\n")
    for row in rows:
        pid = row["prospect_id"]
        if pid in seen:
            continue

        result = fetch_status(row["twilio_sid"])
        if result is None:
            continue
        status, error_code = result

        line_type = classify(status, error_code)
        if line_type is None:
            inconclusive += 1
            continue

        seen.add(pid)
        outcomes[line_type] += 1
        marker = "would set" if dry_run else "set"
        print(f"  {row.get('phone', pid)[:16]:18} {status:12} "
              f"err={error_code or '-':<6} {marker} {line_type}")
        if not dry_run:
            set_line_type(pid, line_type)

    print(f"\n{len(seen)} prospects classified, {inconclusive} inconclusive")
    for line_type, n in outcomes.most_common():
        print(f"  {line_type:14} {n:>4}")

    reachable = outcomes.get("deliverable", 0)
    total = sum(outcomes.values())
    if total:
        print(f"\nreachable: {reachable}/{total} = {reachable / total:.0%}")
        print("That percentage is the ceiling on every downstream metric.")
    if dry_run:
        print("\nDry run - nothing written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be recorded, write nothing")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(dry_run=args.dry_run, limit=args.limit)
