"""
Pull leads from the Google Places API into a CSV the importer understands.

    python -m app.scrape_places --area vancouver --type plumber --out data/bc.csv
    python -m app.scrape_places --lat 49.28 --lng -123.12 --radius 5000 --type electrician
    python -m app.scrape_places --area victoria --type plumber --type hvac_contractor --limit 50

Deliberately writes a CSV rather than inserting straight into the
database. There is a reviewable artifact between "Google said this" and
"the agent is writing outreach about it", and given that this project has
already built a personalization angle out of a 1.0-star rating, that step
earns its keep. Import with:

    python -m app.db.import_csv data/bc.csv

or the upload form in /admin. Both go through the same parse_row, so a
CSV behaves identically either way.

Not a route, and not called by the app at runtime. There is no reason to
scrape on an HTTP request, and doing so would mean a second API key in
the web process.

Costs money. Places API Nearby Search with this field mask is billed per
request (~20 results each) - check current rates before running a large
sweep. The field mask is kept as narrow as the pipeline actually needs
for that reason.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"

# Only what the pipeline uses. Every extra field costs money on every
# request, and unused data in the CSV is data the hook agent might
# personalize on without anyone deciding it should.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName.text",
    "places.formattedAddress",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.rating",                              # hook agent uses this
    "places.userRatingCount",                     # and this
    "places.regularOpeningHours.weekdayDescriptions",  # and this
    "places.primaryTypeDisplayName.text",
    "places.addressComponents",                   # for neighborhood
])

# Convenience centres. Anywhere else, pass --lat/--lng.
AREAS: dict[str, tuple[float, float]] = {
    "vancouver": (49.2827, -123.1207),
    "burnaby": (49.2488, -122.9805),
    "surrey": (49.1913, -122.8490),
    "richmond": (49.1666, -123.1336),
    "victoria": (48.4284, -123.3656),
    "kelowna": (49.8880, -119.4960),
    "toronto": (43.6532, -79.3832),
    "calgary": (51.0447, -114.0719),
}

CSV_COLUMNS = [
    "name", "primary_type", "neighborhood", "address", "phone",
    "rating", "review_count", "website", "opening_hours", "maps_url",
]



# Businesses that are not the customer. Google's nearby search returns
# anything adjacent to the requested type, so a search for "plumber" comes
# back with Home Depot's service desk and national contractors with
# offices in six provinces.
#
# Texting a 1-800 number about missing calls is a wasted send, and the
# message reads as obviously untargeted to anyone who sees it. Filtering
# here rather than at import so the CSV is already the list you meant.
CHAIN_MARKERS = [
    "home depot", "lowe's", "lowes", "rona", "canadian tire", "costco",
    "best buy", "walmart", "ikea", "reliance home comfort", "enercare",
    "black & mcdonald", "black and mcdonald", "roto-rooter", "mr. rooter",
    "servicemaster", "1-800", "sears", "wayfair",
]

# Above this, it is a company with a marketing department, not an owner
# who answers their own phone. Not a hard rule - some genuinely good
# small operators have hundreds of reviews - but a reasonable default.
DEFAULT_MAX_REVIEWS = 400


def is_plausible_lead(row: dict[str, str], max_reviews: int,
                      require_type: bool = False,
                      wanted_types: list[str] | None = None) -> tuple[bool, str]:
    """Returns (keep, reason_if_dropped)."""
    name = (row.get("name") or "").lower()

    for marker in CHAIN_MARKERS:
        if marker in name:
            return False, f"chain or big-box ({marker})"

    phone = row.get("phone") or ""
    if "800" in phone[:6] or "888" in phone[:6] or "877" in phone[:6]:
        return False, "toll-free number - not an owner's phone"

    try:
        if max_reviews and int(row.get("review_count") or 0) > max_reviews:
            return False, f"{row['review_count']} reviews - too large"
    except (TypeError, ValueError):
        pass

    if require_type and wanted_types:
        primary = (row.get("primary_type") or "").lower().replace(" ", "_")
        if not any(w.lower() in primary for w in wanted_types):
            return False, f"primary type is {row.get('primary_type')!r}"

    return True, ""


# Google uses typographic punctuation in names and neighborhoods too, not
# just in opening hours - "Riley Park-Little Mountain" comes back with an
# en dash. It survives to the CSV, gets mangled by whatever opens it, and
# eventually reaches a prompt. Normalize every text field on the way out.
_PUNCT = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u202f": " ", "\u2009": " ", "\u00a0": " ",
    "\u2026": "...",
}


def _clean(value: Any) -> str:
    text = "" if value is None else str(value)
    for bad, good in _PUNCT.items():
        text = text.replace(bad, good)
    return text.strip()


def _neighborhood(place: dict[str, Any]) -> str:
    """Best-effort neighborhood from address components.

    Falls back to the city, then to empty. The hook agent handles a
    missing neighborhood gracefully - it just drops that angle - so
    guessing badly is worse than leaving it blank.
    """
    for wanted in ("sublocality_level_1", "sublocality", "neighborhood", "locality"):
        for comp in place.get("addressComponents", []):
            if wanted in comp.get("types", []):
                return comp.get("longText", "")
    return ""


def _opening_hours(place: dict[str, Any]) -> str:
    """Google's weekday descriptions -> the pipe format parse_row expects.

    'Monday: 9:00 AM - 5:00 PM' | 'Tuesday: ...'
    """
    descriptions = (place.get("regularOpeningHours") or {}).get("weekdayDescriptions", [])
    if not descriptions:
        return ""
    # Google uses a narrow no-break space and an en dash; the importer
    # normalizes both, but emitting ASCII here keeps the CSV readable.
    cleaned = [_clean(d) for d in descriptions]
    return " | ".join(cleaned)


def to_row(place: dict[str, Any]) -> dict[str, str]:
    return {
        "name": _clean((place.get("displayName") or {}).get("text", "")),
        "primary_type": _clean((place.get("primaryTypeDisplayName") or {}).get("text", "")),
        "neighborhood": _clean(_neighborhood(place)),
        "address": _clean(place.get("formattedAddress", "")),
        "phone": _clean(place.get("internationalPhoneNumber", "")),
        "rating": place.get("rating", ""),
        "review_count": place.get("userRatingCount", ""),
        "website": _clean(place.get("websiteUri", "")),
        "opening_hours": _opening_hours(place),
        "maps_url": _clean(place.get("googleMapsUri", "")),
    }


def nearby_search(api_key: str, lat: float, lng: float, radius_m: int,
                  included_types: list[str], max_pages: int) -> list[dict[str, Any]]:
    """Paginated Nearby Search. Returns raw place dicts."""
    results: list[dict[str, Any]] = []
    page_token: str | None = None

    for page in range(max_pages):
        body: dict[str, Any] = {
            "includedTypes": included_types,
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng},
                           "radius": radius_m},
            },
        }
        if page_token:
            body["pageToken"] = page_token

        response = requests.post(
            ENDPOINT, json=body, timeout=30,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
        )
        if response.status_code != 200:
            # Fail loudly. A silent empty page looks like "no businesses
            # here" and is usually a bad key or an unknown place type.
            print(f"Places API returned {response.status_code}:", file=sys.stderr)
            print(response.text[:500], file=sys.stderr)
            break

        data = response.json()
        found = data.get("places", [])
        results.extend(found)
        print(f"  page {page + 1}: {len(found)} results")

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(2)  # Google requires a pause before using the token

    return results


def run(lat: float, lng: float, radius_m: int, types: list[str],
        max_pages: int, limit: int, out_path: str,
        max_reviews: int = DEFAULT_MAX_REVIEWS, require_type: bool = False,
        no_filter: bool = False) -> None:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        sys.exit("GOOGLE_MAPS_API_KEY not set. Add it to your environment.")

    print(f"Searching {radius_m}m around ({lat}, {lng}) for {', '.join(types)}...")
    places = nearby_search(api_key, lat, lng, radius_m, types, max_pages)

    # Google can return the same business across pages and across types.
    by_id = {p.get("id"): p for p in places if p.get("id")}
    rows = [to_row(p) for p in by_id.values()]

    # A lead with no phone cannot be contacted; the importer would skip it
    # anyway, so drop it here rather than pad the CSV.
    with_phone = [r for r in rows if r["phone"]]
    no_phone = len(rows) - len(with_phone)

    kept, dropped_rows = [], []
    for row in with_phone:
        ok, reason = (True, "") if no_filter else is_plausible_lead(
            row, max_reviews, require_type, types)
        (kept if ok else dropped_rows).append((row, reason))
    with_phone = [r for r, _ in kept]

    if limit:
        with_phone = with_phone[:limit]

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(with_phone)

    rated = sum(1 for r in with_phone if r["rating"])
    hours = sum(1 for r in with_phone if r["opening_hours"])

    print(f"\n{len(places)} results -> {len(by_id)} unique -> {len(with_phone)} written")
    if no_phone:
        print(f"  {no_phone} dropped for having no phone number")
    if dropped_rows:
        print(f"  {len(dropped_rows)} filtered out:")
        for row, reason in dropped_rows[:10]:
            print(f"      {row['name'][:38]:40} {reason}")
    print(f"  {rated} have a rating, {hours} have opening hours")
    print(f"\nWrote {path}")
    print("\nRead it before importing. Then:")
    print(f"  python -m app.db.import_csv {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--area", choices=sorted(AREAS),
                        help="preset centre point; or use --lat/--lng")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lng", type=float)
    parser.add_argument("--radius", type=int, default=5000,
                        help="metres, not miles. 5000 is about 3 miles.")
    parser.add_argument("--type", action="append", dest="types",
                        help="Google place type, repeatable. e.g. plumber, electrician")
    parser.add_argument("--pages", type=int, default=3, help="~20 results per page")
    parser.add_argument("--limit", type=int, default=0, help="cap rows written")
    parser.add_argument("--out", default="data/leads.csv")
    parser.add_argument("--max-reviews", type=int, default=DEFAULT_MAX_REVIEWS,
                        help="drop businesses above this; 0 disables")
    parser.add_argument("--strict-type", action="store_true",
                        help="only keep results whose primary type matches --type")
    parser.add_argument("--no-filter", action="store_true",
                        help="keep everything, including chains. see what you are excluding")
    args = parser.parse_args()

    if args.area:
        lat, lng = AREAS[args.area]
    elif args.lat is not None and args.lng is not None:
        lat, lng = args.lat, args.lng
    else:
        parser.error("give --area, or both --lat and --lng")

    run(lat, lng, args.radius, args.types or ["plumber"],
        args.pages, args.limit, args.out,
        max_reviews=args.max_reviews, require_type=args.strict_type,
        no_filter=args.no_filter)
