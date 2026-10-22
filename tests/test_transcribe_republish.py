"""Tests for the republish that follows `atlas transcribe`: only the memos it just transcribed.

Regression: after any new transcription the command republished every note with
force=True, rewriting all of them (624 files for one new memo on 2026-10-21), which
erased anything typed into a note.
"""
import sys
import types

from typer.testing import CliRunner

from memoryatlas import cli, transcriber
from memoryatlas.models import Asset


def _pending(db, n, source):
    asset = Asset(
        id=f"TRX0000{n}-0000-0000-0000-00000000000{n}", source_type="voice_memo",
        source_path=str(source), filename=f"t{n}.m4a", title=f"Pending {n}",
        duration_sec=60.0 + n, recorded_at="2026-10-21T10:00:00Z", file_format="m4a",
    )
    db.upsert_asset(asset)
    return asset


def test_batch_returns_transcribed_ids(config, db, tmp_path, monkeypatch):
    fake_whisper = types.SimpleNamespace(
        transcribe=lambda path, **kw: {"text": "hello", "segments": [], "language": "en"})
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_whisper)
    monkeypatch.setattr(transcriber, "DEFAULT_TRANSCRIPTS_DIR", tmp_path / "transcripts")
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"audio")
    # Shortest first, so the batch order is 1, 2, 3; 2's audio is missing and gets skipped.
    first = _pending(db, 1, audio)
    _pending(db, 2, tmp_path / "gone.m4a")
    third = _pending(db, 3, audio)
    db.conn.commit()

    counts = transcriber.transcribe_batch(config, db)

    assert counts["asset_ids"] == [first.id, third.id]


class _FakeDB:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def init_schema(self):
        pass


def _run_transcribe(monkeypatch, batch_counts):
    """Run `atlas transcribe` with a canned batch result; return every publish call's kwargs."""
    calls = []

    def fake_publish(config, db, **kw):
        calls.append(kw)
        return {"created": 0, "updated": len(kw.get("asset_ids") or []), "skipped": 0, "error": 0}

    monkeypatch.setattr(cli, "_load", lambda path: ("config", _FakeDB()))
    monkeypatch.setattr(cli, "transcribe_batch", lambda config, db, **kw: batch_counts)
    monkeypatch.setattr(cli, "do_publish", fake_publish)
    monkeypatch.setattr(cli, "publish_index", lambda config, db: None)
    result = CliRunner().invoke(cli.app, ["transcribe"])
    assert result.exit_code == 0, result.output
    return calls


def test_transcribe_republishes_only_new(monkeypatch):
    calls = _run_transcribe(monkeypatch, {"done": 2, "failed": 0, "skipped": 0,
                                          "total_seconds": 123.0, "asset_ids": ["A1", "A3"]})
    assert len(calls) == 1
    assert calls[0].get("asset_ids") == ["A1", "A3"]
    assert not calls[0].get("force")


def test_transcribe_nothing_publishes_nothing(monkeypatch):
    calls = _run_transcribe(monkeypatch, {"done": 0, "failed": 0, "skipped": 0,
                                          "total_seconds": 0, "asset_ids": []})
    assert calls == []
