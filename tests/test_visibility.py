"""Tests for note visibility: privacy lives in atlas.db, not in the note file.

Regression for operations#717: a `visibility: private` line added to a note by hand
was erased whenever publish regenerated the notes. The flag now comes from the
database, so every regeneration writes it back.
"""
import sqlite3

from memoryatlas.db import AtlasDB
from memoryatlas.models import Asset
from memoryatlas.publisher import generate_note_content, publish
from memoryatlas.util import row_to_asset


def _frontmatter(content: str) -> list:
    lines = content.split("\n")
    assert lines[0] == "---"
    return lines[1:lines.index("---", 1)]


def _asset(**kw) -> Asset:
    base = dict(id="VIS00001-0000-0000-0000-000000000001", source_type="voice_memo",
                source_path="/t.m4a", filename="t.m4a", title="Vis Test",
                recorded_at="2026-10-05T10:00:00Z", file_format="m4a")
    base.update(kw)
    return Asset(**base)


# --- note content ---------------------------------------------------------------

def test_private_note_carries_flag_on_line_two():
    content = generate_note_content(_asset(visibility="private"))
    assert content.split("\n")[1] == "visibility: private"


def test_public_note_has_no_flag():
    content = generate_note_content(_asset(visibility="public"))
    assert not any(l.startswith("visibility:") for l in _frontmatter(content))


def test_unreviewed_note_defaults_private():
    """No decision recorded means private: fail closed."""
    content = generate_note_content(_asset())
    assert "visibility: private" in _frontmatter(content)


# --- database -------------------------------------------------------------------

def test_new_insert_is_unreviewed(db):
    db.upsert_asset(_asset())
    db.conn.commit()
    asset = row_to_asset(db.get_asset("VIS00001-0000-0000-0000-000000000001"))
    assert asset.visibility is None
    assert asset.is_private


def test_rescan_does_not_touch_visibility(db):
    a = _asset()
    db.upsert_asset(a)
    db.set_visibility(a.id, "public")
    db.upsert_asset(_asset(title="Renamed in Voice Memos"))
    db.conn.commit()
    assert db.get_asset(a.id)["visibility"] == "public"


def test_set_visibility_rejects_other_values(db):
    db.upsert_asset(_asset())
    try:
        db.set_visibility("VIS00001-0000-0000-0000-000000000001", "secret")
    except ValueError:
        return
    raise AssertionError("set_visibility accepted an unknown value")


def test_resolve_prefix(db):
    db.upsert_asset(_asset())
    db.upsert_asset(_asset(id="VIS00002-0000-0000-0000-000000000002"))
    db.conn.commit()
    assert db.resolve_asset_id("VIS00002") == "VIS00002-0000-0000-0000-000000000002"
    assert db.resolve_asset_id("VIS0000") is None      # ambiguous
    assert db.resolve_asset_id("NOPE") is None         # missing


def test_migration_keeps_published_transcripts_public(tmp_path):
    """An existing DB gains the column. Transcribed and published rows keep their
    current state (public); anything not yet transcribed or published stays unreviewed."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE asset (id TEXT PRIMARY KEY, source_type TEXT NOT NULL,
            source_path TEXT NOT NULL, filename TEXT NOT NULL, title TEXT,
            recorded_at TEXT, transcript_status TEXT DEFAULT 'pending',
            note_path TEXT, updated_at TEXT);
        INSERT INTO asset VALUES ('DONE', 'voice_memo', '/a', 'a', 'A', NULL, 'done', 'MemoryAtlas/new/a.md', NULL);
        INSERT INTO asset VALUES ('STUB', 'voice_memo', '/b', 'b', 'B', NULL, 'skipped', 'MemoryAtlas/new/b.md', NULL);
        INSERT INTO asset VALUES ('FRESH', 'voice_memo', '/c', 'c', 'C', NULL, 'done', NULL, NULL);
    """)
    conn.commit()
    conn.close()

    with AtlasDB(path) as db:
        db.init_schema()
        got = dict(db.conn.execute("SELECT id, visibility FROM asset").fetchall())
        assert got == {"DONE": "public", "STUB": None, "FRESH": None}
        db.init_schema()  # idempotent: a second run changes nothing
        assert dict(db.conn.execute("SELECT id, visibility FROM asset").fetchall()) == got


# --- the #717 regression ----------------------------------------------------------

def test_force_republish_keeps_private_flag(config, db):
    a = _asset()
    db.upsert_asset(a)
    db.set_visibility(a.id, "private")
    db.conn.commit()

    publish(config, db, force=True)
    note = config.atlas_vault_dir / "new" / a.note_filename
    assert "visibility: private" in _frontmatter(note.read_text())

    # Removing the flag by hand is an edit, so a forced run leaves the note alone,
    # except that the flag must come back from the DB.
    note.write_text(note.read_text().replace("visibility: private\n", ""))
    publish(config, db, force=True)
    assert "visibility: private" in _frontmatter(note.read_text())


def test_publish_only_given_ids(config, db):
    a = _asset()
    b = _asset(id="VIS00002-0000-0000-0000-000000000002", title="Other")
    for x in (a, b):
        db.upsert_asset(x)
        db.set_visibility(x.id, "public")
    db.conn.commit()
    publish(config, db, force=True)

    db.set_visibility(a.id, "private")
    db.set_visibility(b.id, "private")
    db.conn.commit()
    counts = publish(config, db, asset_ids=[a.id])

    assert counts["updated"] == 1
    new_dir = config.atlas_vault_dir / "new"
    assert "visibility: private" in (new_dir / a.note_filename).read_text()
    assert "visibility: private" not in (new_dir / b.note_filename).read_text()
