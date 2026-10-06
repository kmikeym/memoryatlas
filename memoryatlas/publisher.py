"""Generate Obsidian markdown notes from atlas.db assets."""
import hashlib
import json
from pathlib import Path
from typing import List, Optional

from .config import Config
from .db import AtlasDB
from .models import Asset
from .util import write_jsonl, row_to_asset


def _format_transcript(transcript_path: str) -> str:
    """Format transcript with paragraph breaks using segment timestamps.

    Inserts a paragraph break when there's a gap > 1.5 seconds between
    segments, or every ~4 sentences if no segment data is available.
    """
    txt_path = Path(transcript_path)
    json_path = txt_path.with_suffix(".json")

    # Try segment-based formatting first
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
        if segments:
            paragraphs = []
            current = []
            current_len = 0
            for i, seg in enumerate(segments):
                seg_text = seg["text"].strip()
                current.append(seg_text)
                current_len += len(seg_text)
                # Break on: pause > 1.5s OR paragraph getting long (>500 chars)
                # at a sentence boundary
                should_break = False
                if i < len(segments) - 1:
                    gap = segments[i + 1]["start"] - seg["end"]
                    if gap > 1.5:
                        should_break = True
                    elif current_len > 500 and seg_text[-1:] in ".?!":
                        should_break = True
                if should_break:
                    paragraphs.append(" ".join(current))
                    current = []
                    current_len = 0
            if current:
                paragraphs.append(" ".join(current))
            return "\n\n".join(paragraphs)

    # Fallback: sentence-based paragraph breaks
    text = txt_path.read_text(encoding="utf-8").strip()
    if len(text) < 200:
        return text

    sentences = []
    for part in text.replace("? ", "?\n").replace(". ", ".\n").replace("! ", "!\n").split("\n"):
        part = part.strip()
        if part:
            sentences.append(part)

    paragraphs = []
    for i in range(0, len(sentences), 4):
        paragraphs.append(" ".join(sentences[i:i + 4]))
    return "\n\n".join(paragraphs)


def _is_3mr_episode(asset: Asset) -> bool:
    """Detect if an asset is a 3 Minute Review podcast episode."""
    title = (asset.title or "").lower()
    if "3mr" in title or "3 minute review" in title or "three minute review" in title:
        return True
    # Check transcript for 3MR references
    if asset.transcript_path:
        try:
            text = Path(asset.transcript_path).read_text(encoding="utf-8").lower()
            if "3 minute review" in text or "three minute review" in text:
                return True
        except Exception:
            pass
    return False


def generate_note_content(asset: Asset) -> str:
    """Generate markdown content for a single asset note."""
    lines = ["---"]
    # From atlas.db, so every regeneration writes it back (operations#717). Line 2 is
    # where the fleet's hand-added flags sat, so existing private notes come out unchanged.
    if asset.is_private:
        lines.append("visibility: private")
    lines.append(f"uuid: {asset.id}")
    lines.append(f"type: {asset.source_type}")

    title_escaped = (asset.title or "Untitled").replace('"', '\\"')
    lines.append(f'title: "{title_escaped}"')

    if asset.recorded_at:
        lines.append(f"recorded: {asset.recorded_at}")
        lines.append(f"date: {asset.recorded_date}")

    if asset.duration_sec is not None:
        lines.append(f"duration: {asset.duration_display}")
        lines.append(f"duration_sec: {asset.duration_sec:.1f}")

    lines.append(f"format: {asset.file_format or 'unknown'}")
    lines.append(f"transcribed: {str(asset.transcript_status == 'done').lower()}")
    lines.append(f"enriched: {str(asset.summary is not None).lower()}")
    lines.append(f"polished: {str(asset.polished_at is not None).lower()}")

    tags = ["memoryatlas", f"memoryatlas/{asset.source_type.replace('_', '-')}"]
    if asset.transcript_status == "done":
        tags.append("memoryatlas/transcribed")
    if _is_3mr_episode(asset):
        tags.append("3mr")
        lines.append(f"series: 3MR")
    lines.append(f"tags: [{', '.join(tags)}]")

    lines.append(f"atlas-id: {asset.short_id}")

    if asset.has_gps and asset.lat is not None:
        lines.append(f"location: [{asset.lat}, {asset.lon}]")
        if asset.place:
            lines.append(f"place: \"{asset.place}\"")

    lines.append("---")
    lines.append("")

    display_title = asset.title or "Untitled Recording"
    lines.append(f"# {display_title}")
    lines.append("")

    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    if asset.recorded_at:
        lines.append(f"| Recorded | {asset.recorded_at} |")
    lines.append(f"| Duration | {asset.duration_display} |")
    lines.append(f"| Format | .{asset.file_format or '?'} |")
    lines.append(f"| Source | Apple Voice Memos |")
    lines.append(f"| UUID | `{asset.short_id}` |")
    if asset.place:
        lines.append(f"| Location | {asset.place} |")
    lines.append("")

    if asset.summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(asset.summary)
        lines.append("")

        # Add enrichment metadata if available
        if asset.topics:
            lines.append(f"**Topics**: {asset.topics}")
            lines.append("")
        if asset.people and asset.people.lower() != "none":
            lines.append(f"**People**: {asset.people}")
            lines.append("")
        if asset.sentiment:
            lines.append(f"**Sentiment**: {asset.sentiment}")
            lines.append("")

    if asset.transcript_status == "done" and asset.transcript_path:
        lines.append("## Transcript")
        lines.append("")
        try:
            transcript_text = _format_transcript(asset.transcript_path)
            lines.append(transcript_text)
        except Exception:
            lines.append(f"[Open transcript](file://{asset.transcript_path})")
        lines.append("")
    else:
        lines.append("## Transcript")
        lines.append("")
        lines.append("*Pending transcription.*")
        lines.append("")

    # Philological enhancement (polished transcripts)
    if asset.translated_text:
        lines.append("## Translation")
        lines.append("")
        lines.append("*Literary English translation — faithful and elevated.*")
        lines.append("")
        lines.append(asset.translated_text)
        lines.append("")

    if asset.restored_text:
        lines.append("## Restored Transcript")
        lines.append("")
        lines.append("*Source language restored from Whisper output — cleaned, punctuated, voice preserved.*")
        lines.append("")
        lines.append(asset.restored_text)
        lines.append("")

    return "\n".join(lines)


def publish(config: Config, db: AtlasDB, verbose: bool = False,
            force: bool = False, asset_ids: Optional[List[str]] = None) -> dict:
    """Generate Obsidian notes for assets. With asset_ids, only those notes."""
    counts = {"created": 0, "updated": 0, "skipped": 0, "error": 0}

    db.log_action("publish", "start")

    voice_dir = config.atlas_vault_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    tmr_dir = config.atlas_vault_dir / "3MR"
    tmr_dir.mkdir(parents=True, exist_ok=True)
    new_dir = config.atlas_vault_dir / "new"
    new_dir.mkdir(parents=True, exist_ok=True)

    if asset_ids:
        rows = [r for r in (db.get_asset(i) for i in asset_ids) if r is not None]
    else:
        rows = db.get_all_assets() if force else db.get_unpublished_assets()

    for row in rows:
        try:
            asset = row_to_asset(row)
            content = generate_note_content(asset)
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            # Route: already published → keep location, new → new/
            # 3MR is a closed archive (no new episodes) — folder placement for
            # existing 3MR notes is preserved via the "already published" branch
            # below, same as any other note. No content-based re-routing here:
            # that was the mechanism that produced duplicate files (old copy left
            # behind, new copy written to a freshly-detected folder) whenever
            # _is_3mr_episode()'s classification of an asset flipped between runs.
            if asset.note_path:
                # Already published — keep in current folder
                existing_subfolder = str(Path(asset.note_path).parent.name)
                target_dir = config.atlas_vault_dir / existing_subfolder
                target_dir.mkdir(parents=True, exist_ok=True)
                subfolder = existing_subfolder
            else:
                # Brand new — goes to triage inbox
                target_dir = new_dir
                subfolder = "new"

            note_filename = asset.note_filename
            note_path = target_dir / note_filename
            relative_note_path = f"MemoryAtlas/{subfolder}/{note_filename}"

            if note_path.exists() and asset.note_hash == content_hash and not force:
                counts["skipped"] += 1
                continue

            is_new = not note_path.exists()
            note_path.write_text(content, encoding="utf-8")

            db.mark_published(asset.id, relative_note_path, content_hash)
            action = "create" if is_new else "update"
            db.log_action("publish", action, asset_id=asset.id, detail={
                "note_path": relative_note_path,
            })
            write_jsonl(config.jsonl_path, "publish", action, asset.id, {
                "note_path": relative_note_path,
            })

            if is_new:
                counts["created"] += 1
            else:
                counts["updated"] += 1

            if verbose:
                print(f"  {action}: {note_filename}")

        except Exception as e:
            counts["error"] += 1
            db.log_action("publish", "error",
                         asset_id=row["id"] if row else None,
                         detail={"error": str(e)})
            if verbose:
                print(f"  ERROR: {e}")

    db.conn.commit()
    db.log_action("publish", "complete", detail=counts)
    db.conn.commit()

    return counts


def publish_index(config: Config, db: AtlasDB):
    """Write/update the _Index.md Dataview dashboard."""
    stats = db.get_stats()

    content = f"""---
tags: [memoryatlas, memoryatlas/index]
---

# MemoryAtlas

> {stats['total']} recordings | {stats['total_hours']:.0f} hours | {stats['published']} published | {stats['transcribed']} transcribed

## Recent Recordings

```dataview
TABLE duration, recorded, transcribed
FROM "MemoryAtlas/voice"
WHERE type = "voice_memo"
SORT recorded DESC
LIMIT 20
```

## Longest Recordings

```dataview
TABLE duration, duration_sec, recorded, title
FROM "MemoryAtlas/voice"
SORT duration_sec DESC
LIMIT 10
```

## By Year

```dataview
TABLE length(rows) AS Count, round(sum(rows.duration_sec) / 3600, 1) AS Hours
FROM "MemoryAtlas/voice"
GROUP BY dateformat(date(recorded), "yyyy") AS Year
SORT Year DESC
```

## Pending Transcription

```dataview
TABLE duration, recorded
FROM "MemoryAtlas/voice"
WHERE transcribed = false
SORT duration_sec ASC
LIMIT 20
```
"""
    index_path = config.atlas_vault_dir / "_Index.md"
    config.atlas_vault_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content, encoding="utf-8")


def publish_about(config: Config):
    """Write the _About.md system documentation note."""
    content = """---
tags: [memoryatlas, memoryatlas/system]
---

# About MemoryAtlas

MemoryAtlas is a local-first pipeline that ingests voice memos (and later videos, photos, location data), transcribes them with Whisper, and publishes lightweight notes into this Obsidian vault. Everything runs on your Mac Studio — no cloud, no data leaves your machine.

## How It Works

1. **Scan**: Reads Apple Voice Memos metadata from CloudRecordings.db (read-only, never modifies originals)
2. **Publish**: Creates one markdown note per recording with frontmatter for Dataview queries
3. **Transcribe** (Phase 2): Runs OpenAI Whisper locally for EN/RU bilingual transcription
4. **Enrich** (Phase 4): Joins Google/Apple location history, LLM summaries via Ollama

## Where Things Live

| What | Where | Why |
|------|-------|-----|
| These notes | `MemoryAtlas/` in PracticeLife vault | Lightweight, searchable |
| Heavy data | `~/tools/memoryatlas/data/` | Keeps Obsidian fast |
| Transcripts | `~/tools/memoryatlas/data/transcripts/` | Too large for vault |
| Source audio | Apple Voice Memos (untouched) | Read-only, never modified |
| Database | `~/tools/memoryatlas/data/atlas.db` | Source of truth |
| Action log | `~/tools/memoryatlas/data/atlas.jsonl` | Append-only audit trail |

## Privacy Model

- All processing is local (Mac Studio M2 Max)
- Whisper runs on-device, no API calls
- Ollama enrichment runs on-device
- No audio files are copied into the vault
- Source Voice Memos database is opened read-only (immutable mode)

## Commands

```bash
source ~/tools/memoryatlas/.venv/bin/activate
atlas init       # Initialize database and vault folders
atlas scan       # Import voice memo metadata
atlas publish    # Generate Obsidian notes
atlas status     # Show statistics
atlas doctor     # Check system health
atlas info UUID  # Show details for one asset
```

## Frontmatter Fields (Dataview)

- `uuid` / `atlas-id`: Recording identifier
- `type`: voice_memo (later: video, audio_import)
- `title`, `date`, `recorded`: Metadata from Voice Memos
- `duration` / `duration_sec`: Human-readable and raw seconds
- `transcribed` / `enriched`: Boolean status flags
- `location` / `place`: GPS coordinates and place name (Phase 4)
- `visibility`: `private` unless the recording is marked public in atlas.db. New recordings start private. Change it with `atlas visibility public|private ID`, never by editing the note: publish rewrites notes from the database.
- `tags`: Always includes `memoryatlas`
"""
    about_path = config.atlas_vault_dir / "_About.md"
    config.atlas_vault_dir.mkdir(parents=True, exist_ok=True)
    about_path.write_text(content, encoding="utf-8")
