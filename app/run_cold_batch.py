"""
Run the cold outreach pipeline against prospects with status='new'.

Usage:
    python -m app.run_cold_batch --dry-run --limit 3    # review only, no sends
    python -m app.run_cold_batch --limit 10              # real sends, 10 prospects

Defaults to --dry-run and a small limit on purpose - this sends real SMS
and costs real money (Twilio + OpenAI) per prospect, so nothing goes out
by accident.
"""
import argparse
import asyncio
import json
import time

from app.db.client import list_prospects
from app.agents.cold_outreach import run_cold_outreach_for_prospect

SECONDS_BETWEEN_SENDS = 1.5  # basic rate limiting, well under Twilio throughput limits


async def run_batch(limit: int, dry_run: bool) -> None:
    prospects = list_prospects(status="new", limit=limit)
    if not prospects:
        print("No prospects with status='new' found.")
        return

    print(f"Running {'DRY RUN' if dry_run else 'LIVE'} for {len(prospects)} prospect(s)...\n")

    for i, prospect in enumerate(prospects):
        result = await run_cold_outreach_for_prospect(prospect, dry_run=dry_run)
        print(f"--- {result['name']} ---")
        print(f"Angle: {result['hook_angle']}")
        print(f"Winner: {result['winner']} ({result['winner_reason']})")
        print(f"Message: {result['sent_text']}")
        print(f"Sent: {result['sent']}")
        print()

        if not dry_run and i < len(prospects) - 1:
            time.sleep(SECONDS_BETWEEN_SENDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true", help="Actually send messages (overrides --dry-run)")
    args = parser.parse_args()

    dry_run = not args.live
    asyncio.run(run_batch(limit=args.limit, dry_run=dry_run))
