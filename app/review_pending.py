"""
Review queue for agent-drafted replies. Nothing reaches a prospect without
going through this.

Usage:
    python -m app.review_pending
"""
from app.db.client import list_pending_replies, clear_pending_reply
from app.tools.twilio_sms import send_sms


def review_all() -> None:
    pending = list_pending_replies()
    if not pending:
        print("No pending replies.")
        return

    print(f"{len(pending)} pending repl{'y' if len(pending) == 1 else 'ies'} to review.\n")

    for prospect in pending:
        print("=" * 60)
        print(f"Business: {prospect['name']} ({prospect.get('primary_type') or 'unknown'})")
        print(f"Phone: {prospect['phone']}")
        print(f"Their message: {prospect['pending_reply_context']}")
        print(f"\nSuggested reply:\n{prospect['pending_reply']}")
        print()

        choice = input("[a]pprove / [e]dit / [s]kip / [q]uit review: ").strip().lower()

        if choice == "q":
            break

        elif choice == "a":
            _send_and_clear(prospect, prospect["pending_reply"])

        elif choice == "e":
            edited = input("New reply text: ").strip()
            if edited:
                _send_and_clear(prospect, edited)
            else:
                print("Empty reply, skipped.")

        elif choice == "s":
            print("Skipped (left in queue for next time).\n")
            continue

        else:
            print("Not a valid option, skipping this one.\n")


def _send_and_clear(prospect: dict, text: str) -> None:
    result = send_sms(
        to_phone=prospect["phone"],
        body=text,
        prospect_id=prospect["id"],
        agent_name="draft_reply_agent:human_approved",
    )
    clear_pending_reply(prospect["id"])
    print(f"Sent (status: {result['status']}).\n")


if __name__ == "__main__":
    review_all()
