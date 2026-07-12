"""
In-memory rate limiter for real sends triggered from the public demo page.
Deliberately simple - a dict of timestamps per IP, no external dependency.

Known limitation, worth naming rather than hiding: this resets if the
process restarts, and doesn't share state across multiple server
instances. Fine for a portfolio demo behind a single process; a
production version would back this with Redis.
"""
import time
from collections import defaultdict

MAX_SENDS_PER_HOUR = 5

_send_log: dict[str, list[float]] = defaultdict(list)


def check_and_record(client_ip: str) -> bool:
    """Returns True and records a send if under the limit, False if not."""
    now = time.time()
    recent = [t for t in _send_log[client_ip] if now - t < 3600]
    if len(recent) >= MAX_SENDS_PER_HOUR:
        _send_log[client_ip] = recent
        return False
    recent.append(now)
    _send_log[client_ip] = recent
    return True
