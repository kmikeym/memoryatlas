"""Read Apple Voice Memos CloudRecordings.db (read-only)."""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List

from .constants import APPLE_EPOCH_OFFSET, APPLE_VOICEMEMOS_DIR
from .models import Asset


def apple_date_to_iso(apple_timestamp: float) -> str:
    """Convert Apple epoch timestamp to ISO 8601 UTC string."""
    unix_ts = apple_timestamp + APPLE_EPOCH_OFFSET
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_voice_memos(db_path: Path) -> List[Asset]:
    """
    Read all recordings from Apple's CloudRecordings.db.

    Opens the database in read-only mode (immutable=1) to avoid
    any writes or locks that could interfere with Voice Memos.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT
                ZUNIQUEID,
                ZPATH,
                ZDURATION,
                ZDATE,
                ZENCRYPTEDTITLE,
                ZAUDIODIGEST
            FROM ZCLOUDRECORDING
            ORDER BY ZDATE ASC
        """).fetchall()

        assets = []
        for row in rows:
            filename = row["ZPATH"]
            if not filename:
                continue
            ext = Path(filename).suffix.lower().lstrip(".")
            source_path = str(APPLE_VOICEMEMOS_DIR / filename)
            recorded_at = apple_date_to_iso(row["ZDATE"]) if row["ZDATE"] is not None else None

            asset = Asset(
                id=row["ZUNIQUEID"],
                source_type="voice_memo",
                source_path=source_path,
                filename=filename,
                title=row["ZENCRYPTEDTITLE"],
                duration_sec=row["ZDURATION"],
                recorded_at=recorded_at,
                file_format=ext,
                apple_audio_digest=row["ZAUDIODIGEST"],
            )
            assets.append(asset)

        return assets
    finally:
        conn.close()
