"""Generate Obsidian markdown notes from atlas.db assets."""
import hashlib
import json
import shutil
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


# note_path in atlas.db starts with this fixed label, which stands for config.atlas_vault_dir.
NOTE_PATH_ROOT = "MemoryAtlas"
# Files in the vault folder that are not recordings' notes.
NOT_NOTES = {"_Index.md", "_About.md"}
PRIVATE_FLAG = "visibility: private"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _vault_path(config: Config, note_path: str) -> Path:
    return config.atlas_vault_dir.joinpath(*Path(note_path).parts[1:])


def _note_path(config: Config, path: Path) -> str:
    return "/".join((NOTE_PATH_ROOT,) + path.relative_to(config.atlas_vault_dir).parts)


def _write_note(path: Path, text: str):
    """Write a note in place, restoring the old text if the write fails partway.

    In place, not via a temp file and a rename: a rename would give the note a new
    created time (Obsidian's sort, Dataview's file.ctime) and drop its Finder tags.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    backup = path.with_name(f".{path.name}.atlas-bak")
    shutil.copyfile(path, backup)
    try:
        path.write_text(text, encoding="utf-8")
    except BaseException:
        shutil.copyfile(backup, path)  # if this fails too, the backup stays for a person
        backup.unlink()
        raise
    backup.unlink()


def _frontmatter_uuid(path: Path) -> Optional[str]:
    """The uuid in a note's frontmatter, read without loading the rest of the file."""
    try:
        # errors="replace": a bad byte further down must not hide a note's uuid.
        with path.open(encoding="utf-8-sig", errors="replace") as f:
            if f.readline().rstrip() != "---":
                return None
            for line in f:
                line = line.rstrip()
                if line == "---":
                    return None
                if line.startswith("uuid:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _notes_by_uuid(config: Config) -> dict:
    """Every note file under the vault folder, keyed by the uuid in its frontmatter."""
    found = {}
    for path in config.atlas_vault_dir.rglob("*.md"):
        if path.name not in NOT_NOTES:
            uuid = _frontmatter_uuid(path)
            if uuid:
                found.setdefault(uuid, []).append(path)
    return found


def _with_private_flag(text: str) -> str:
    """A hand-edited note's text with `visibility: private` in its frontmatter.

    Unchanged when the flag is already anywhere in the frontmatter. Otherwise any other
    visibility line goes and the flag is put back as line two.
    """
    lines = text.split("\n")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() == "---"), None)
    if lines[0].lstrip("﻿").rstrip() != "---" or end is None:
        return "\n".join(["---", PRIVATE_FLAG, "---"] + lines)
    front = lines[1:end]
    if any(line.strip() == PRIVATE_FLAG for line in front):
        return text
    front = [line for line in front if not line.startswith("visibility:")]
    return "\n".join([lines[0], PRIVATE_FLAG] + front + lines[end:])


def _flag_copies(copies: List[Path]) -> int:
    """Add the privacy flag to every copy lacking it; return how many files changed."""
    changed = 0
    for path in copies:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"  could not read, so not flagged private: {path} ({e})")
            continue
        if _with_private_flag(text) != text:
            _write_note(path, _with_private_flag(text))
            changed += 1
    return changed


def flag_private_copies(config: Config, db: AtlasDB, asset_ids: List[str]) -> int:
    """Flag every note file of these recordings that are private, wherever it sits.

    publish writes only the note at a recording's recorded path, so a copy renamed by hand
    elsewhere in the vault would otherwise stay unflagged. Returns files changed.
    """
    vault_notes = _notes_by_uuid(config)
    changed = 0
    for asset_id in dict.fromkeys(asset_ids):
        row = db.get_asset(asset_id)
        if row is not None and row_to_asset(row).is_private:
            changed += _flag_copies(vault_notes.get(asset_id, []))
    return changed


def publish(config: Config, db: AtlasDB, verbose: bool = False,
            force: bool = False, asset_ids: Optional[List[str]] = None) -> dict:
    """Generate Obsidian notes for assets. With asset_ids, only those notes.

    A note is written where it actually is: a note moved or renamed in the vault, or one
    atlas.db has lost track of, is found by the uuid in its frontmatter. A note whose text
    matches neither what publish last wrote nor what it would write now was edited by hand
    and is never overwritten, forced or not; only its privacy flag comes back from atlas.db
    (operations#717). A recording with two notes and neither at its recorded path is a
    conflict: its copies get the privacy flag if they need it, and nothing else is written.
    Each note's result is committed as it is written.
    """
    counts = {"created": 0, "updated": 0, "skipped": 0, "protected": 0, "conflict": 0, "error": 0}
    vault_notes = None  # read from the vault the first time a note is not where atlas.db says

    db.log_action("publish", "start")

    voice_dir = config.atlas_vault_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    tmr_dir = config.atlas_vault_dir / "3MR"
    tmr_dir.mkdir(parents=True, exist_ok=True)
    new_dir = config.atlas_vault_dir / "new"
    new_dir.mkdir(parents=True, exist_ok=True)

    if asset_ids:
        rows = [r for r in (db.get_asset(i) for i in dict.fromkeys(asset_ids)) if r is not None]
    else:
        rows = db.get_all_assets() if force else db.get_unpublished_assets()

    for row in rows:
        try:
            asset = row_to_asset(row)
            content = generate_note_content(asset)
            content_hash = _content_hash(content)

            note_path = _vault_path(config, asset.note_path) if asset.note_path else None
            if note_path is None or not note_path.exists():
                if vault_notes is None:
                    vault_notes = _notes_by_uuid(config)
                copies = vault_notes.get(asset.id, [])
                if len(copies) > 1:
                    counts["conflict"] += 1
                    if asset.is_private:
                        _flag_copies(copies)
                    where = [_note_path(config, c) for c in copies]
                    db.log_action("publish", "conflict", asset_id=asset.id, detail={"copies": where})
                    print(f"  conflict, nothing written: {len(copies)} notes for {asset.short_id}: "
                          + "; ".join(where))
                    continue
                if copies:
                    note_path = copies[0]  # moved or renamed in the vault
                elif note_path is None:
                    # Brand new: goes to the triage inbox. No content-based routing: that
                    # was the mechanism that produced duplicate 3MR files when a
                    # classification flipped between runs.
                    note_path = new_dir / asset.note_filename
                # Otherwise the note was deleted: write it again where it was.

            note_filename = note_path.name
            relative_note_path = _note_path(config, note_path)

            if note_path.exists():
                on_disk = note_path.read_text(encoding="utf-8")
                if _content_hash(on_disk) == content_hash:
                    # Already holds exactly this text, so there is nothing to write. Record
                    # it anyway: a run may have written it and stopped before recording.
                    db.mark_published(asset.id, relative_note_path, content_hash)
                    counts["skipped"] += 1
                    continue
                if _content_hash(on_disk) != asset.note_hash:
                    # Edited by hand since publish last wrote it.
                    if asset.is_private:
                        _flag_copies([note_path])
                    # Where it is now, and that it was handled, so an unforced run stops
                    # picking it up. The hash stays what publish last wrote.
                    db.mark_published(asset.id, relative_note_path, asset.note_hash)
                    counts["protected"] += 1
                    db.log_action("publish", "protected", asset_id=asset.id, detail={
                        "note_path": relative_note_path,
                    })
                    print(f"  protected, edited by hand so not overwritten: {relative_note_path}")
                    continue

            is_new = not note_path.exists()
            _write_note(note_path, content)

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
        finally:
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

## Editing Notes

Move, rename or write in any note. Publish finds a moved or renamed note by the `uuid` in its frontmatter and updates it where it is, and it never overwrites a note you have written in. To get a freshly generated note back, delete every copy of the note and run `atlas publish --force`.

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
- `visibility`: `private` unless the recording is marked public in atlas.db. New recordings start private. Change it with `atlas visibility public|private ID`, not by editing the note: publish puts `visibility: private` back into any note of a private recording, even one you have edited.
- `tags`: Always includes `memoryatlas`
"""
    about_path = config.atlas_vault_dir / "_About.md"
    config.atlas_vault_dir.mkdir(parents=True, exist_ok=True)
    about_path.write_text(content, encoding="utf-8")
