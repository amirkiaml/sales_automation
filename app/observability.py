"""
Records what each agent run actually did, step by step.

The point is the runs that produce NO message. A reply blocked by the
scope guardrail leaves nothing in the messages table, so from the outside
it is indistinguishable from nothing having happened. Those are exactly
the runs worth inspecting - they are where the guardrails either earned
their keep or over-blocked a question the agent should have answered.

Kept deliberately dumb: a list of dicts and one insert at the end. This
is a debugging aid for the operator console, not a metrics pipeline. When
Langfuse goes in (p.12), that handles latency/cost/percentiles and this
stays as the human-readable "what happened on this one conversation" view.

Never raises into the caller. A tracing failure must not take down the
send path it is observing.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class Trace:
    """Collects steps for one agent run, then persists once."""

    def __init__(self, prospect_id: str, entry_point: str, trigger_text: str = ""):
        self.prospect_id = prospect_id
        self.entry_point = entry_point
        self.trigger_text = trigger_text
        self.steps: list[dict[str, Any]] = []
        self.outcome: str = "incomplete"
        self._started = time.perf_counter()

    def step(self, name: str, status: str = "ok", **detail: Any) -> None:
        """Record one step.

        `name`   - what ran ('scope_guardrail_keywords', 'kb_retrieval').
        `status` - 'ok' | 'blocked' | 'skipped' | 'error'.
        `detail` - anything small and JSON-serialisable worth seeing later.
        """
        self.steps.append(
            {
                "name": name,
                "status": status,
                "at_ms": round((time.perf_counter() - self._started) * 1000),
                **{k: v for k, v in detail.items() if v not in (None, "", [], ())},
            }
        )

    def finish(self, outcome: str) -> None:
        self.outcome = outcome
        try:
            from app.db.client import save_trace

            save_trace(
                prospect_id=self.prospect_id,
                entry_point=self.entry_point,
                trigger_text=self.trigger_text,
                outcome=outcome,
                steps=self.steps,
                duration_ms=round((time.perf_counter() - self._started) * 1000),
            )
        except Exception:  # noqa: BLE001
            # Observability must never break the thing it observes.
            logger.exception("Failed to persist agent trace (continuing anyway)")
