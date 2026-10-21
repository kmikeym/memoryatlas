"""Batch transcription engine using mlx-whisper (Apple Silicon optimized).

Designed for:
- Resumability: tracks status per-asset in DB, picks up where it left off
- Crash safety: commits after each transcript
- Non-destructive: never touches source audio files
- Transcript storage: JSON (with timestamps/segments) + plain text in data/transcripts/
"""
import json
import time
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .db import AtlasDB
from .constants import DEFAULT_TRANSCRIPTS_DIR


def transcribe_batch(
    config: Config,
    db: AtlasDB,
    limit: Optional[int] = None,
    model: str = "mlx-community/whisper-turbo",
    language: Optional[str] = None,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict:
    """Transcribe pending voice memos in batch.

    Returns dict with counts: done, failed, skipped, total_seconds.
    """
    import mlx_whisper

    transcripts_dir = DEFAULT_TRANSCRIPTS_DIR
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    # Get pending assets, ordered shortest first (quick wins build momentum)
    query = """
        SELECT * FROM asset
        WHERE transcript_status IN ('pending', 'failed')
          AND duration_sec > 5
        ORDER BY duration_sec ASC
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = db.conn.execute(query).fetchall()
    total = len(rows)

    if total == 0:
        print("No pending transcriptions.")
        return {"done": 0, "failed": 0, "skipped": 0, "total_seconds": 0}

    # Summary
    total_duration = sum(r["duration_sec"] for r in rows)
    print(f"Transcription batch: {total} files, {total_duration/3600:.1f} hours")
    print(f"Model: {model}")
    print(f"Est. time: {total_duration/18/3600:.1f}-{total_duration/10/3600:.1f} hours")
    print()

    if dry_run:
        print("DRY RUN — no transcriptions will be performed.")
        return {"done": 0, "failed": 0, "skipped": 0, "total_seconds": total_duration}

    counts = {"done": 0, "failed": 0, "skipped": 0, "total_seconds": 0}
    batch_start = time.time()

    for i, row in enumerate(rows, 1):
        asset_id = row["id"]
        source_path = row["source_path"]
        duration = row["duration_sec"]
        title = row["title"] or "Untitled"

        # Progress header
        elapsed = time.time() - batch_start
        if counts["done"] > 0:
            avg_speed = counts["total_seconds"] / elapsed if elapsed > 0 else 0
            remaining_audio = total_duration - counts["total_seconds"]
            eta_seconds = remaining_audio / avg_speed if avg_speed > 0 else 0
            eta_str = f"ETA: {eta_seconds/3600:.1f}h"
        else:
            eta_str = "ETA: calculating..."

        print(f"[{i}/{total}] {title} ({duration/60:.1f}min) — {eta_str}")

        # Check source file exists
        if not Path(source_path).exists():
            print(f"  SKIP: source file missing")
            db.conn.execute(
                "UPDATE asset SET transcript_status = 'skipped' WHERE id = ?",
                (asset_id,),
            )
            db.conn.commit()
            counts["skipped"] += 1
            continue

        # Mark as running
        db.conn.execute(
            "UPDATE asset SET transcript_status = 'running' WHERE id = ?",
            (asset_id,),
        )
        db.conn.commit()

        try:
            start = time.time()

            # Transcribe. Each 30s window is decoded on its own: conditioned on the previous
            # window, one "Thank you." primes the next and the loop runs over real speech.
            result = mlx_whisper.transcribe(
                source_path,
                path_or_hf_repo=model,
                language=language,
                condition_on_previous_text=False,
            )

            elapsed_transcribe = time.time() - start
            speed = duration / elapsed_transcribe if elapsed_transcribe > 0 else 0

            text = result.get("text", "").strip()
            segments = result.get("segments", [])
            lang = result.get("language", "unknown")

            # Save transcript files
            transcript_base = transcripts_dir / asset_id
            transcript_txt = transcript_base.with_suffix(".txt")
            transcript_json = transcript_base.with_suffix(".json")

            transcript_txt.write_text(text, encoding="utf-8")
            transcript_json.write_text(json.dumps({
                "text": text,
                "language": lang,
                "segments": segments,
                "model": model,
                "duration_sec": duration,
                "transcribe_time_sec": elapsed_transcribe,
                "speed_factor": speed,
            }, indent=2, default=str), encoding="utf-8")

            # Update DB
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.conn.execute("""
                UPDATE asset SET
                    transcript_status = 'done',
                    transcript_model = ?,
                    transcript_lang = ?,
                    transcript_at = ?,
                    transcript_path = ?
                WHERE id = ?
            """, (model, lang, now, str(transcript_txt), asset_id))
            db.conn.commit()

            db.log_action("transcribe", "done", asset_id=asset_id, detail={
                "model": model,
                "language": lang,
                "speed": f"{speed:.1f}x",
                "text_length": len(text),
                "segments": len(segments),
            })
            db.conn.commit()

            counts["done"] += 1
            counts["total_seconds"] += duration

            if verbose:
                print(f"  OK: {speed:.1f}x realtime, {len(text)} chars, {lang}")
                print(f"  Preview: {text[:100]}...")
            else:
                print(f"  OK ({speed:.1f}x, {len(segments)} segments)")

        except Exception as e:
            db.conn.execute(
                "UPDATE asset SET transcript_status = 'failed' WHERE id = ?",
                (asset_id,),
            )
            db.conn.commit()
            db.log_action("transcribe", "failed", asset_id=asset_id, detail={
                "error": str(e),
            })
            db.conn.commit()
            counts["failed"] += 1
            print(f"  FAILED: {e}")

    # Final summary
    total_elapsed = time.time() - batch_start
    print()
    print(f"Batch complete: {counts['done']} done, {counts['failed']} failed, {counts['skipped']} skipped")
    print(f"Audio processed: {counts['total_seconds']/3600:.1f} hours in {total_elapsed/3600:.1f} hours")
    if total_elapsed > 0 and counts["total_seconds"] > 0:
        print(f"Average speed: {counts['total_seconds']/total_elapsed:.1f}x realtime")

    return counts
