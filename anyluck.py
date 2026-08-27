"""anyluck - answers "any luck with the job search?"."""
import json
import os
import re
import tomllib
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

NEW_MARKER = "\U0001F195"

# Labels Workday interleaves with the values when you read a row's inner_text.
_ROW_LABELS = {"locations", "location", "posted on", "remote type"}

_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")      # "... (FTC03)"
_LEADING_CODE = re.compile(r"^[A-Z0-9]{3,}\s*-\s*")    # "FTC03 - ..."
_TRAILING_BUILDING = re.compile(r"\s+[A-Z]{1,2}-\d+$")  # "... B-3"
_MULTI_LOCATION = re.compile(r"^\d+\s+locations?$")     # "2 Locations"


def load_config(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run /jobsetup in Claude Code to create it."
        )
    with path.open("rb") as handle:
        return tomllib.load(handle)


def normalize_location(text):
    """Clean a location string without ever discarding one.

    Workday hides real place names behind internal facility codes, and uses
    "2 Locations" for multi-city postings. Both must survive - dropping the
    latter silently loses every multi-city job.
    """
    if not text:
        return ""
    lines = [line.strip() for line in str(text).splitlines()]
    lines = [line for line in lines if line and line.lower() not in _ROW_LABELS]
    cleaned = " ".join(lines).strip()
    cleaned = _TRAILING_PAREN.sub("", cleaned)
    cleaned = _LEADING_CODE.sub("", cleaned)
    cleaned = _TRAILING_BUILDING.sub("", cleaned)
    return cleaned.strip()


def job_key(company, href):
    """Stable identity for a posting: bank plus the slug from its own URL.

    The requisition ID is not usable here - some tenants put a location string
    in that field and some omit it entirely. The URL slug is always present,
    because it is what addresses the job.
    """
    path = urllib.parse.urlsplit(str(href)).path.rstrip("/")
    return f"{company}:{path.rsplit('/', 1)[-1]}"


def merge_seen(seen, jobs, now):
    """Fold this cycle's postings into the seen-set. Returns (seen, new_keys).

    Anything absent from the set is new, so with a 4-hour poll "new" means
    "appeared in the last 4 hours" by construction - more precise than any date
    the boards actually publish.

    The one branch that matters: an empty set means this is a seed run, and
    nothing is reported. Otherwise the first run - or any run after state loss -
    would alert on every posting at every bank at once.
    """
    seeding = not seen
    seen = dict(seen)
    new_keys = []
    stamp = now.isoformat()

    for job in jobs:
        key = job["key"]
        previous = seen.get(key)
        record = dict(job)
        if previous is None:
            record["first_seen"] = stamp
            if not seeding:
                new_keys.append(key)
        else:
            # Refresh the mutable fields, but first_seen is written once.
            record["first_seen"] = previous["first_seen"]
        seen[key] = record

    return seen, new_keys


def prune(seen, now, days=30):
    """Drop keys older than the TTL.

    Longer than any realistic posting lifetime, so expiry never resurrects a
    live job as "new".
    """
    cutoff = now - timedelta(days=days)
    return {
        key: record
        for key, record in seen.items()
        if datetime.fromisoformat(record["first_seen"]) > cutoff
    }


def humanize_age(first_seen, now):
    """Render an age the way every other UI does: floored, coarsest unit."""
    minutes = int((now - datetime.fromisoformat(first_seen)).total_seconds() // 60)
    if minutes < 10:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def render_markdown(seen, new_keys, now):
    """Render the whole seen-set: grouped by bank, newest first within each.

    Two ages appear per posting. The first-seen age is ours and is accurate to
    the poll interval; the site's own prose is shown beside it as a weaker
    second opinion, because "Posted 30+ Days Ago" is a floor, not a value.
    """
    new_keys = set(new_keys)
    by_company = {}
    for record in seen.values():
        by_company.setdefault(record.get("company") or "Unknown", []).append(record)

    lines = ["# anyluck", "", f"_Updated {now:%Y-%m-%d %H:%M}._", ""]
    for company in sorted(by_company):
        lines += [f"## {company}", ""]
        rows = sorted(by_company[company], key=lambda r: r["first_seen"], reverse=True)
        for record in rows:
            meta = " · ".join(
                part for part in (record.get("location"), record.get("remote")) if part
            )
            headline = f"- **{record['title']}**"
            if meta:
                headline += f" — {meta}"
            if record["key"] in new_keys:
                headline += f" {NEW_MARKER}"
            lines.append(headline)

            age = f"  first seen {humanize_age(record['first_seen'], now)}"
            posted = record.get("posted_text")
            if posted:
                age += f' · site says "{posted}"'
            lines += [age, f"  {record['url']}", ""]

    return "\n".join(lines)


def load_seen(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        # Falling back to {} here would re-report every job at every bank, which
        # looks exactly like a real flood of new postings. Fail loudly instead.
        raise ValueError(
            f"{path} is corrupted ({exc}). Starting from empty state would "
            f"re-report the entire back catalogue. Move it aside and re-run to "
            f"re-seed:  mv {path} {path}.broken"
        ) from exc


def save_atomic(path, text):
    """Write via a temp file and rename, so a crash can't leave a torn file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def filter_locations(jobs, locations):
    """Keep postings matching any wanted location. Empty list keeps everything.

    Multi-location placeholders carry no place name to match on, so they are
    always kept rather than dropped - see normalize_location.
    """
    if not locations:
        return jobs
    wanted = [item.lower() for item in locations]
    kept = []
    for job in jobs:
        where = (job.get("location") or "").lower()
        if _MULTI_LOCATION.match(where) or any(item in where for item in wanted):
            kept.append(job)
    return kept
