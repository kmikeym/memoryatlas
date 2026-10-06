"""Utility functions."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .models import Asset


def write_jsonl(path: Path, command: str, action: str,
                asset_id: Optional[str] = None, detail: Optional[dict] = None):
    """Append a single line to the JSONL action log."""
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cmd": command,
        "act": action,
    }
    if asset_id:
        entry["id"] = asset_id
    if detail:
        entry["d"] = detail
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def row_to_asset(row: sqlite3.Row) -> Asset:
    """Convert a sqlite3.Row to an Asset dataclass."""
    return Asset(
        id=row["id"],
        source_type=row["source_type"],
        source_path=row["source_path"],
        filename=row["filename"],
        title=row["title"],
        duration_sec=row["duration_sec"],
        recorded_at=row["recorded_at"],
        file_format=row["file_format"],
        file_size_bytes=row["file_size_bytes"],
        apple_audio_digest=row["apple_audio_digest"],
        has_gps=row["has_gps"],
        lat=row["lat"],
        lon=row["lon"],
        place=row["place"],
        transcript_status=row["transcript_status"],
        transcript_model=row["transcript_model"],
        transcript_lang=row["transcript_lang"],
        transcript_at=row["transcript_at"],
        transcript_path=row["transcript_path"],
        summary=row["summary"],
        topics=row["topics"],
        people=row["people"],
        sentiment=row["sentiment"],
        enriched_at=row["enriched_at"],
        restored_text=row["restored_text"],
        translated_text=row["translated_text"],
        polished_at=row["polished_at"],
        visibility=row["visibility"] if "visibility" in row.keys() else None,
        note_path=row["note_path"],
        published_at=row["published_at"],
        note_hash=row["note_hash"],
        scanned_at=row["scanned_at"],
        updated_at=row["updated_at"],
    )


def format_count_line(counts: dict) -> str:
    """Format a counts dict as a single summary line."""
    parts = [f"{v} {k}" for k, v in counts.items() if v > 0]
    return ", ".join(parts) if parts else "no changes"
