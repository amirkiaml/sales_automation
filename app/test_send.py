"""
Send ONE real test SMS through the full pipeline (hook -> drafts -> picker
-> Twilio send) to a phone number you control, before running anything
against real prospects.

This creates/updates a single prospect row with source='test' so it's
clearly distinguishable from real leads in the prospects table, and its
status becomes 'contacted' after sending - it won't accidentally get
picked up by a future real batch run.

Usage:
    python -m app.test_send --phone +15555550123

Optional business data flags let you simulate a realistic prospect so the
hook agent has something to personalize against (defaults are provided):
    python -m app.test_send --phone +15555550123 --name "Test Plumbing Co" \\
        --primary-type plumber --rating 4.8 --review-count 3
"""
import argparse
import asyncio

from app.db.client import upsert_prospect
from app.agents.cold_outreach import run_cold_outreach_for_prospect


async def main(args: argparse.Namespace) -> None:
    test_prospect_data = {
        "name": args.name,
        "primary_type": args.primary_type,
        "neighborhood": "Test Neighborhood",
        "address": "123 Test St, Toronto, ON",
        "phone": args.phone,
        "rating": args.rating,
        "review_count": args.review_count,
        "website": None,
        "opening_hours": {"monday": "8:00 AM - 6:00 PM", "sunday": "closed"},
        "maps_url": None,
        "source": "test",
        "status": "new",  # reset in case this test phone was used before
    }
    prospect = upsert_prospect(test_prospect_data)

    print(f"Sending real test SMS to {args.phone}...\n")
    result = await run_cold_outreach_for_prospect(prospect, dry_run=False)

    print(f"Angle: {result['hook_angle']}")
    print(f"Winner: {result['winner']} ({result['winner_reason']})")
    print(f"Message sent:\n{result['sent_text']}\n")
    print(f"Twilio SID: {result['twilio_sid']}")
    print(f"Sent: {result['sent']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True, help="Your test phone number, E.164 format, e.g. +15555550123")
    parser.add_argument("--name", default="Test Plumbing Co")
    parser.add_argument("--primary-type", default="plumber", dest="primary_type")
    parser.add_argument("--rating", type=float, default=4.8)
    parser.add_argument("--review-count", type=int, default=3, dest="review_count")
    args = parser.parse_args()

    asyncio.run(main(args))
