"""Data models."""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Asset:
    id: str
    source_type: str  # voice_memo | video | audio_import
    source_path: str
    filename: str
    title: Optional[str] = None
    duration_sec: Optional[float] = None
    recorded_at: Optional[str] = None  # ISO 8601 UTC
    file_format: Optional[str] = None
    file_size_bytes: Optional[int] = None
    apple_audio_digest: Optional[bytes] = None
    has_gps: int = 0
    lat: Optional[float] = None
    lon: Optional[float] = None
    place: Optional[str] = None
    transcript_status: str = "pending"
    transcript_model: Optional[str] = None
    transcript_lang: Optional[str] = None
    transcript_at: Optional[str] = None
    transcript_path: Optional[str] = None
    summary: Optional[str] = None
    topics: Optional[str] = None
    people: Optional[str] = None
    sentiment: Optional[str] = None
    enriched_at: Optional[str] = None
    restored_text: Optional[str] = None
    translated_text: Optional[str] = None
    polished_at: Optional[str] = None
    visibility: Optional[str] = None  # 'private' | 'public' | None (unreviewed, treated as private)
    note_path: Optional[str] = None
    published_at: Optional[str] = None
    note_hash: Optional[str] = None
    scanned_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def is_private(self) -> bool:
        """Fail closed: anything not explicitly marked public is private."""
        return self.visibility != "public"

    @property
    def short_id(self) -> str:
        return self.id.split("-")[0] if "-" in self.id else self.id[:8]

    @property
    def duration_display(self) -> str:
        if self.duration_sec is None:
            return "unknown"
        total = int(self.duration_sec)
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def recorded_date(self) -> Optional[str]:
        if self.recorded_at:
            return self.recorded_at[:10]
        return None

    @property
    def slug_title(self) -> str:
        title = self.title or "untitled"
        slug = title.lower().strip()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"[\s]+", "-", slug)
        slug = slug.strip("-")
        return slug[:60] or "untitled"

    @property
    def note_filename(self) -> str:
        date = self.recorded_date or "undated"
        return f"{date}_{self.slug_title}_{self.short_id}.md"
