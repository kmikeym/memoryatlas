"""MemoryAtlas SQLite database operations."""
import json
import sqlite3
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timezone

from .models import Asset

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS asset (
    id              TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK(source_type IN ('voice_memo', 'video', 'audio_import')),
    source_path     TEXT NOT NULL,
    filename        TEXT NOT NULL,
    title           TEXT,
    duration_sec    REAL,
    recorded_at     TEXT,
    file_format     TEXT,
    file_size_bytes INTEGER,
    apple_audio_digest BLOB,
    has_gps         INTEGER DEFAULT 0,
    lat             REAL,
    lon             REAL,
    place           TEXT,
    transcript_status TEXT DEFAULT 'pending'
                    CHECK(transcript_status IN ('pending','running','done','failed','skipped')),
    transcript_model  TEXT,
    transcript_lang   TEXT,
    transcript_at     TEXT,
    transcript_path   TEXT,
    summary         TEXT,
    topics          TEXT,
    people          TEXT,
    sentiment       TEXT,
    enriched_at     TEXT,
    note_path       TEXT,
    published_at    TEXT,
    note_hash       TEXT,
    visibility      TEXT CHECK(visibility IN ('private', 'public')),
    scanned_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_asset_source_type ON asset(source_type);
CREATE INDEX IF NOT EXISTS idx_asset_recorded_at ON asset(recorded_at);
CREATE INDEX IF NOT EXISTS idx_asset_transcript_status ON asset(transcript_status);
CREATE INDEX IF NOT EXISTS idx_asset_title ON asset(title);

CREATE TRIGGER IF NOT EXISTS trg_asset_updated_at
    AFTER UPDATE ON asset
    FOR EACH ROW
BEGIN
    UPDATE asset SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS action_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    command     TEXT NOT NULL,
    asset_id    TEXT,
    action      TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_log_command ON action_log(command);
CREATE INDEX IF NOT EXISTS idx_action_log_asset_id ON action_log(asset_id);
"""


class AtlasDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        return self

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    def init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        self.conn.commit()
        self._migrate_visibility()

    def _migrate_visibility(self):
        """Add asset.visibility to a database created before it existed (operations#717).

        Privacy has to live here, not in the note: publish rewrites notes from this table,
        so a flag added to a note by hand was erased on the next run. NULL means
        unreviewed and publishes as private. Rows already transcribed and published keep
        their current state, which is public; everything else stays unreviewed.
        """
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(asset)")]
        if "visibility" in cols:
            return
        self.conn.execute(
            "ALTER TABLE asset ADD COLUMN visibility TEXT "
            "CHECK(visibility IN ('private', 'public'))"
        )
        cur = self.conn.execute(
            "UPDATE asset SET visibility = 'public' "
            "WHERE note_path IS NOT NULL AND transcript_status = 'done'"
        )
        self.log_action("migrate", "add_visibility", detail={"public": cur.rowcount})
        self.conn.commit()

    def upsert_asset(self, asset: Asset) -> str:
        """Insert or update an asset. Returns 'insert', 'update', or 'skip'."""
        existing = self.conn.execute(
            "SELECT id, title, duration_sec, recorded_at FROM asset WHERE id = ?",
            (asset.id,),
        ).fetchone()

        if existing is None:
            self.conn.execute("""
                INSERT INTO asset (
                    id, source_type, source_path, filename, title,
                    duration_sec, recorded_at, file_format, file_size_bytes,
                    apple_audio_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asset.id, asset.source_type, asset.source_path, asset.filename,
                asset.title, asset.duration_sec, asset.recorded_at,
                asset.file_format, asset.file_size_bytes, asset.apple_audio_digest,
            ))
            return "insert"

        if (existing["title"] == asset.title
                and existing["duration_sec"] == asset.duration_sec
                and existing["recorded_at"] == asset.recorded_at):
            return "skip"

        self.conn.execute("""
            UPDATE asset SET
                source_path = ?, filename = ?, title = ?,
                duration_sec = ?, recorded_at = ?, file_format = ?,
                file_size_bytes = ?, apple_audio_digest = ?
            WHERE id = ?
        """, (
            asset.source_path, asset.filename, asset.title,
            asset.duration_sec, asset.recorded_at, asset.file_format,
            asset.file_size_bytes, asset.apple_audio_digest, asset.id,
        ))
        return "update"

    def log_action(self, command: str, action: str,
                   asset_id: Optional[str] = None, detail: Optional[dict] = None):
        self.conn.execute(
            "INSERT INTO action_log (command, asset_id, action, detail) VALUES (?, ?, ?, ?)",
            (command, asset_id, action, json.dumps(detail) if detail else None),
        )

    def get_unpublished_assets(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            # Never published, or published before its transcript landed (a republish
            # right after transcribing can fail, and nothing else would retry it).
            "SELECT * FROM asset WHERE note_path IS NULL"
            " OR (transcript_at IS NOT NULL AND (published_at IS NULL OR transcript_at > published_at))"
            " ORDER BY recorded_at ASC"
        ).fetchall()

    def get_all_assets(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM asset ORDER BY recorded_at ASC"
        ).fetchall()

    def resolve_asset_id(self, given: str) -> Optional[str]:
        """Full id for an exact id or a unique prefix (e.g. the atlas-id); else None."""
        if self.get_asset(given) is not None:
            return given
        rows = self.conn.execute(
            "SELECT id FROM asset WHERE id LIKE ? LIMIT 2", (f"{given}%",)
        ).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def set_visibility(self, asset_id: str, value: str):
        if value not in ("private", "public"):
            raise ValueError(f"visibility must be 'private' or 'public', not {value!r}")
        self.conn.execute(
            "UPDATE asset SET visibility = ? WHERE id = ?", (value, asset_id)
        )
        self.log_action("visibility", value, asset_id=asset_id)

    def get_asset(self, asset_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM asset WHERE id = ?", (asset_id,)
        ).fetchone()

    def mark_published(self, asset_id: str, note_path: str, note_hash: str):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.conn.execute(
            "UPDATE asset SET note_path = ?, published_at = ?, note_hash = ? WHERE id = ?",
            (note_path, now, note_hash, asset_id),
        )

    def set_note_path(self, asset_id: str, note_path: str):
        """Record where a note now lives, without marking it republished."""
        self.conn.execute("UPDATE asset SET note_path = ? WHERE id = ?", (note_path, asset_id))

    def get_stats(self) -> dict:
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN note_path IS NOT NULL THEN 1 END) as published,
                COUNT(CASE WHEN transcript_status = 'done' THEN 1 END) as transcribed,
                COUNT(CASE WHEN summary IS NOT NULL THEN 1 END) as enriched,
                COALESCE(SUM(duration_sec), 0) / 3600.0 as total_hours
            FROM asset
        """).fetchone()
        return dict(row)
