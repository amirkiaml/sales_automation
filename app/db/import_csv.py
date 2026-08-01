"""
Import a lead-gen CSV (e.g. scraped from Google Maps) into the prospects
table.

Usage:
    python -m app.db.import_csv path/to/leads.csv

Handles two things the raw export needs before it's DB-ready:
  1. Mojibake encoding fix - these exports are typically UTF-8 saved/read
     as Latin-1 somewhere in the pipeline, so "8:00 AM" becomes garbled
     with sequences like "â€¯" and "â€“". We re-decode field by field.
  2. opening_hours - arrives as one pipe-delimited string
     ("Monday: 8:00 AM - 10:00 PM | Tuesday: ..."); we parse it into a
     dict so later code (the hook/angle agent) can reason about it
     structurally instead of pattern-matching text.
"""
import csv
import re
import sys
from pathlib import Path

from app.db.client import upsert_prospect
from app.phone import InvalidPhoneError, normalize_phone  # noqa: F401


def fix_mojibake(text: str) -> str:
    """Undo a UTF-8-decoded-as-Latin-1 round trip. Safe no-op if the
    text wasn't mangled to begin with."""
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


# normalize_phone and InvalidPhoneError now live in app/phone.py so they
# can be imported (and tested) without pulling in the Supabase client.
# Re-exported here because callers already import them from this module.

    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if 7 <= len(digits) <= 15:
        return "+" + digits

    raise InvalidPhoneError(str(raw))


def parse_opening_hours(raw: str) -> dict[str, str]:
    """'Monday: 8:00 AM - 10:00 PM | Tuesday: ...' -> {'monday': '8:00 AM - 10:00 PM', ...}"""
    if not raw or not raw.strip():
        return {}
    hours = {}
    for chunk in raw.split("|"):
        chunk = chunk.strip()
        if ":" not in chunk:
            continue
        day, times = chunk.split(":", 1)
        # normalize the mojibake-fixed non-breaking space / en dash to plain ascii
        times = times.strip().replace("\u202f", " ").replace("\u2013", "-").replace("\u2009", " ")
        hours[day.strip().lower()] = times
    return hours


def parse_row(row: dict[str, str]) -> dict:
    row = {k: fix_mojibake(v or "") for k, v in row.items()}

    return {
        "name": row.get("name", "").strip(),
        "primary_type": row.get("primary_type", "").strip() or None,
        "neighborhood": row.get("neighborhood", "").strip() or None,
        "address": row.get("address", "").strip() or None,
        "phone": normalize_phone(row.get("phone", "")),
        "rating": float(row["rating"]) if row.get("rating") else None,
        "review_count": int(row["review_count"]) if row.get("review_count") else None,
        "website": row.get("website", "").strip() or None,
        "opening_hours": parse_opening_hours(row.get("opening_hours", "")),
        "maps_url": row.get("maps_url", "").strip() or None,
    }


def import_rows(reader: csv.DictReader) -> tuple[int, int]:
    """Shared by the CLI script and the admin console's upload form -
    one implementation, so a CSV imported either way behaves identically."""
    imported, skipped = 0, 0
    for row in reader:
        parsed = parse_row(row)
        if not parsed["name"] or not parsed["phone"]:
            skipped += 1
            continue
        upsert_prospect(parsed)
        imported += 1
    return imported, skipped


def import_csv(path: str) -> None:
    file_path = Path(path)
    with file_path.open(newline="", encoding="utf-8") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        reader = csv.DictReader(f, dialect=dialect)
        imported, skipped = import_rows(reader)

    print(f"Imported/updated {imported} prospects, skipped {skipped} rows (missing name/phone).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.db.import_csv path/to/leads.csv")
        sys.exit(1)
    import_csv(sys.argv[1])
