"""In-memory schema cache (per running server process).

Holds introspection results so repeat reads within the server's lifetime make no
network call. Mixed granularity: one ``table_list`` entry per logical database
plus one ``descriptions`` entry per ``schema.table``, each with its own
``fetched_at`` so they expire independently. Lost on restart; never written to
disk (decision 2026-06-19, option B — no cache file, no corrupt-file handling).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# {db_name: {"table_list": entry, "descriptions": {"schema.table": entry}}}
_cache: dict[str, dict] = {}


def is_fresh(fetched_at: str, ttl_hours: int) -> bool:
    """True when ``fetched_at`` (ISO-8601 UTC) is within the TTL window."""
    fetched = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return datetime.now(timezone.utc) - fetched < timedelta(hours=ttl_hours)


def get_table_list(db: str, ttl_hours: int) -> dict | None:
    """Return the cached table-list entry, or None on a miss or stale entry."""
    entry = _cache.get(db, {}).get("table_list")
    if entry is None or not is_fresh(entry["fetched_at"], ttl_hours):
        return None
    return entry


def put_table_list(db: str, entry: dict) -> None:
    """Write through the table-list entry for a database."""
    _cache.setdefault(db, {})["table_list"] = entry


def get_description(db: str, key: str, ttl_hours: int) -> dict | None:
    """Return the cached description entry for ``schema.table``, or None on miss/stale."""
    entry = _cache.get(db, {}).get("descriptions", {}).get(key)
    if entry is None or not is_fresh(entry["fetched_at"], ttl_hours):
        return None
    return entry


def put_description(db: str, key: str, entry: dict) -> None:
    """Write through one ``schema.table`` description entry."""
    _cache.setdefault(db, {}).setdefault("descriptions", {})[key] = entry


def invalidate(db: str) -> None:
    """Drop all cached entries for a database (internal helper, not wired to discover)."""
    _cache.pop(db, None)


def clear() -> None:
    """Drop the entire cache (test helper)."""
    _cache.clear()
