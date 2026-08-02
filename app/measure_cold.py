"""
Measure the cold outreach pipeline without sending anything.

    python -m app.measure_cold --limit 50
    python -m app.measure_cold --limit 50 --out runs/cold_50.csv

Runs the full pipeline (hook -> 3 drafters -> picker) in dry-run mode and
records what it cost, how long it took, and what it chose. Nothing is sent
and nothing is written to the prospects table.

This exists because the pipeline was entirely unmeasured. It runs five
model calls per prospect and I had no idea what that cost, how long it
took, whether the picker actually splits between personas, or how many
leads have enough data for personalization to mean anything.

Cost note: five model calls per prospect. 50 prospects is fine; 484 is
~2400 calls. Start small.

Token counting works by wrapping Runner.run for the duration of this
script. The pipeline returns a plain dict and discards the RunResult
objects that carry usage, so there is nothing to read after the fact.
Wrapping here rather than changing cold_outreach.py keeps the production
path unchanged - this is a measurement tool, and it should not require
the thing it measures to be modified.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import csv
import statistics
import time
from collections import Counter
from pathlib import Path

from agents import Runner

from app.agents.cold_outreach import run_cold_outreach_for_prospect
from app.config import settings
from app.db.client import list_prospects
from app.tools.twilio_sms import sanitize_for_sms

# USD per 1M tokens. These are hardcoded and WILL go stale - a wrong number
# here is worse than none because it looks authoritative. Check current
# rates at openai.com/api/pricing before quoting anything from this script.
PRICING = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
    "gpt-4.1": {"in": 2.00, "out": 8.00},
}

# Per-task bucket. asyncio copies the context when it creates a task, so
# concurrent prospects don't write into each other's totals.
_usage_bucket: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "usage_bucket", default=None
)
_original_run = Runner.run


async def _run_with_usage(*args, **kwargs):
    """Wraps Runner.run to record token usage for whichever prospect is running."""
    result = await _original_run(*args, **kwargs)
    bucket = _usage_bucket.get()
    if bucket is not None:
        try:
            u = result.context_wrapper.usage
            model = getattr(getattr(args[0], "model", None), "__str__", lambda: "")() or str(
                getattr(args[0], "model", "") or ""
            )
            bucket.append(
                {"model": model, "requests": u.requests,
                 "input": u.input_tokens, "output": u.output_tokens}
            )
        except Exception:  # noqa: BLE001 - measurement must not break the run
            pass
    return result


Runner.run = _run_with_usage


def cost_usd(calls: list[dict]) -> float:
    """Best-effort cost. Unknown models fall back to the configured agent model."""
    total = 0.0
    for c in calls:
        rates = PRICING.get(c["model"]) or PRICING.get(settings.AGENT_MODEL)
        if not rates:
            continue
        total += c["input"] / 1_000_000 * rates["in"]
        total += c["output"] / 1_000_000 * rates["out"]
    return total


def has_personalization(p: dict) -> bool:
    """Does this lead have anything beyond name and phone?

    The hook agent degrades to a generic angle without these. If most of
    the list is thin, the personalization pipeline is doing less work than
    it appears to.
    """
    return any(p.get(f) for f in ("opening_hours", "rating", "review_count", "neighborhood"))


def segments(text: str) -> int:
    """SMS segment count. GSM-7 gives 160 chars, or 153 when concatenated.

    A single non-GSM character - a curly apostrophe, an emoji, an en dash -
    switches the whole message to UCS-2 and the limits drop to 70 and 67.
    That is a silent doubling, so it is worth counting rather than assuming.
    """
    if not text:
        return 0
    gsm = set(
        "@£$¥èéùìòÇØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
        "\n\r\f"
    ) | set("^{}\\[~]|€")
    if all(ch in gsm for ch in text):
        single, multi = 160, 153
    else:
        single, multi = 70, 67
    return 1 if len(text) <= single else -(-len(text) // multi)


async def measure_one(prospect: dict) -> dict:
    calls: list[dict] = []
    _usage_bucket.set(calls)
    t0 = time.perf_counter()
    error = ""
    result = {}
    try:
        result = await run_cold_outreach_for_prospect(prospect, dry_run=True)
    except Exception as e:  # noqa: BLE001 - one bad row shouldn't kill the batch
        error = f"{type(e).__name__}: {e}"
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    # Sanitize exactly as send_sms will, or the segment counts here
    # will not match what Twilio actually bills.
    text = sanitize_for_sms(result.get("sent_text", ""))
    return {
        "model_calls": len(calls),
        "input_tokens": sum(c["input"] for c in calls),
        "output_tokens": sum(c["output"] for c in calls),
        "cost_usd": round(cost_usd(calls), 6),
        "prospect_id": prospect.get("id", ""),
        "name": prospect.get("name", ""),
        "has_personalization": has_personalization(prospect),
        "elapsed_ms": elapsed_ms,
        "hook_angle": result.get("hook_angle", ""),
        "winner": result.get("winner", ""),
        "winner_reason": result.get("winner_reason", ""),
        "message": text,
        "message_chars": len(text),
        "sms_segments": segments(text),
        "sms_cost_usd": round(segments(text) * settings.TWILIO_COST_PER_SEGMENT, 6),
        "error": error,
    }


async def run(limit: int, out_path: str | None, concurrency: int) -> None:
    prospects = list_prospects(status="new", limit=limit)
    if not prospects:
        print("No prospects with status='new'. Import the leads CSV first.")
        return

    print(f"Measuring {len(prospects)} prospects (dry run, nothing sent)...\n")
    t0 = time.perf_counter()

    # Bounded concurrency: faster than sequential, but not 50 simultaneous
    # bursts into a rate limit.
    sem = asyncio.Semaphore(concurrency)

    async def guarded(p):
        async with sem:
            return await measure_one(p)

    rows = await asyncio.gather(*(guarded(p) for p in prospects))
    wall_s = time.perf_counter() - t0

    ok = [r for r in rows if not r["error"]]
    failed = [r for r in rows if r["error"]]

    print(f"=== {len(ok)} succeeded, {len(failed)} failed, {wall_s:.1f}s wall clock ===\n")

    if ok:
        times = sorted(r["elapsed_ms"] for r in ok)
        print("LATENCY per prospect (full pipeline)")
        print(f"  median   {statistics.median(times):>7.0f} ms")
        print(f"  p95      {times[int(len(times) * 0.95) - 1]:>7.0f} ms")
        print(f"  slowest  {times[-1]:>7.0f} ms\n")

        print("PICKER - which persona won")
        wins = Counter(r["winner"] for r in ok)
        for persona, n in wins.most_common():
            print(f"  {persona:<14} {n:>4}  {n / len(ok):>5.0%}")
        if len(wins) == 1:
            print("  ^ one persona won every time - the other two may be dead weight")
        print()

        print("LEAD DATA")
        rich = sum(1 for r in ok if r["has_personalization"])
        print(f"  with extra fields   {rich:>4}  {rich / len(ok):>5.0%}")
        print(f"  name + phone only   {len(ok) - rich:>4}  {(len(ok) - rich) / len(ok):>5.0%}")
        print("  ^ thin leads fall through to the generic hook angle\n")

        print("MESSAGE LENGTH")
        chars = sorted(r["message_chars"] for r in ok)
        segs = Counter(r["sms_segments"] for r in ok)
        print(f"  median chars  {statistics.median(chars):>6.0f}")
        print(f"  longest       {chars[-1]:>6}")
        for s in sorted(segs):
            print(f"  {s} segment(s)  {segs[s]:>4}  ({segs[s] / len(ok):.0%}) - billed as {s}x")
        ucs2 = sum(1 for r in ok if r["sms_segments"] and r["message_chars"] <= 160
                   and r["sms_segments"] > 1)
        if ucs2:
            print(f"  {ucs2} message(s) under 160 chars but multi-segment - non-GSM character")
        print()

        print("HOOK ANGLES - 5 most common")
        for angle, n in Counter(r["hook_angle"] for r in ok).most_common(5):
            print(f"  {n:>3}  {angle[:70]}")
        print()

    if failed:
        print("FAILURES")
        for r in failed[:5]:
            print(f"  {r['name']}: {r['error']}")
        print()

    if ok:
        costs = sorted(r["cost_usd"] for r in ok)
        tot_in = sum(r["input_tokens"] for r in ok)
        tot_out = sum(r["output_tokens"] for r in ok)
        tot_cost = sum(r["cost_usd"] for r in ok)
        print("COST")
        print(f"  model calls / prospect  {statistics.median(r['model_calls'] for r in ok):>8.0f}")
        print(f"  input tokens  (total)   {tot_in:>8,}")
        print(f"  output tokens (total)   {tot_out:>8,}")
        print(f"  median $/prospect       {statistics.median(costs):>8.4f}")
        print(f"  batch total             {tot_cost:>8.4f}")
        print(f"  extrapolated to 484     {tot_cost / len(ok) * 484:>8.2f}")
        sms_total = sum(r["sms_cost_usd"] for r in ok)
        seg_total = sum(r["sms_segments"] for r in ok)
        print(f"  --- Twilio, at ${settings.TWILIO_COST_PER_SEGMENT}/segment ---")
        print(f"  segments                {seg_total:>8}")
        print(f"  batch total             {sms_total:>8.4f}")
        print(f"  extrapolated to 484     {sms_total / len(ok) * 484:>8.2f}")
        print(f"  --- combined ---")
        print(f"  per prospect            {(tot_cost + sms_total) / len(ok):>8.4f}")
        print(f"  extrapolated to 484     {(tot_cost + sms_total) / len(ok) * 484:>8.2f}")
        print("  ^ Twilio figure is a FLOOR: carrier fees are billed on top.")
        print("  ^ OpenAI rates are hardcoded in PRICING and may be stale.\n")

    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--out", type=str, default="", help="CSV path for per-prospect rows")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.out or None, args.concurrency))
