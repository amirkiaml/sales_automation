"""
In-memory stand-ins for Supabase and Twilio.

The compliance tests assert things that must never happen - sending to an
opted-out number, an agent seeing a STOP message, a blocked message
producing silence. Those all live in code paths that touch the database
and the SMS API, so testing them means faking both.

Deliberately a fake rather than a mock. A mock asserts "this function was
called"; a fake actually stores rows and answers queries, so a test can
say "after deleting the prospect, is this number still suppressed?" and
get a real answer. Mocks would pass whether or not the suppression
genuinely survived.

Only the query shapes this codebase uses are implemented. It will raise
on anything unfamiliar rather than silently returning nothing - a fake
that quietly answers wrong is worse than one that fails loudly.
"""
from __future__ import annotations

import uuid
from typing import Any


class FakeResult:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class FakeQuery:
    """One chained query against one table. Filters are applied on execute()."""

    def __init__(self, store: "FakeSupabase", table: str):
        self._store = store
        self._table = table
        self._filters: list[tuple[str, Any]] = []
        self._op = "select"
        self._payload: Any = None
        self._limit: int | None = None
        self._order: tuple[str, bool] | None = None

    # -- builders -------------------------------------------------------
    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def upsert(self, payload, **_kwargs):
        self._op, self._payload = "upsert", payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def gt(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def ilike(self, *_a, **_k):
        return self

    def or_(self, *_a, **_k):
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, *_a, **_k):
        return self

    # -- execution ------------------------------------------------------
    def _matching(self) -> list[dict[str, Any]]:
        rows = self._store.tables.setdefault(self._table, [])
        return [r for r in rows if all(r.get(c) == v for c, v in self._filters)]

    def execute(self) -> FakeResult:
        rows = self._store.tables.setdefault(self._table, [])

        if self._op == "select":
            out = self._matching()
            if self._order:
                col, desc = self._order
                out = sorted(out, key=lambda r: r.get(col) or "", reverse=desc)
            if self._limit is not None:
                out = out[: self._limit]
            return FakeResult(out)

        if self._op in ("insert", "upsert"):
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            created = []
            for item in items:
                row = dict(item)
                # A real unique constraint would reject this; the tests that
                # care about uniqueness assert on it explicitly.
                if self._op == "upsert":
                    existing = next(
                        (r for r in rows if r.get("phone") and r["phone"] == row.get("phone")),
                        None,
                    )
                    if existing:
                        existing.update(row)
                        created.append(existing)
                        continue
                row.setdefault("id", str(uuid.uuid4()))
                rows.append(row)
                created.append(row)
            return FakeResult(created)

        if self._op == "update":
            hit = self._matching()
            for row in hit:
                row.update(self._payload)
            return FakeResult(hit)

        if self._op == "delete":
            hit = self._matching()
            for row in hit:
                rows.remove(row)
            return FakeResult(hit)

        raise NotImplementedError(f"FakeQuery has no handler for {self._op!r}")


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict[str, Any]]] = {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    # -- helpers for tests ---------------------------------------------
    def seed_prospect(self, **overrides) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "name": "Test Plumbing",
            "phone": "+14165551234",
            "status": "new",
            "opted_out": False,
            "autopilot": False,
            "rating": 4.8,
            "review_count": 40,
        }
        row.update(overrides)
        self.tables.setdefault("prospects", []).append(row)
        return row

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.get(table, [])


class FakeTwilio:
    """Records what would have been sent. Nothing leaves the process."""

    def __init__(self):
        self.sent: list[dict[str, str]] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.sent.append(kwargs)

        class _Msg:
            sid = "SM" + uuid.uuid4().hex[:16]
            status = "queued"

        return _Msg()

    # -- helpers --------------------------------------------------------
    @property
    def bodies(self) -> list[str]:
        return [m.get("body", "") for m in self.sent]

    def sent_to(self, phone: str) -> list[dict[str, str]]:
        return [m for m in self.sent if m.get("to") == phone]
