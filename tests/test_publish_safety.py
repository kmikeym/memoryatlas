"""Tests for publish keeping notes that were moved, renamed or edited by hand.

Regression: publish worked out each note's path from atlas.db alone and, when forced,
rewrote it unconditionally. A note moved in Obsidian got a fresh copy written back into
new/, a renamed note got a twin under its generated name, and typed-in text was erased.
"""
from memoryatlas.models import Asset
from memoryatlas.publisher import publish

ID = "SAFE0001-0000-0000-0000-000000000001"


def _publish_one(config, db):
    """Publish one public recording; return it and its note path in new/."""
    asset = Asset(id=ID, source_type="voice_memo", source_path="/t.m4a", filename="t.m4a",
                  title="Safety Test", recorded_at="2026-10-21T10:00:00Z", file_format="m4a")
    db.upsert_asset(asset)
    db.set_visibility(ID, "public")
    db.conn.commit()
    publish(config, db)
    note = config.atlas_vault_dir / "new" / asset.note_filename
    assert note.exists()
    return asset, note


def _change_data(db, text):
    """New data for the recording, so its regenerated note differs from the one on disk."""
    db.conn.execute("UPDATE asset SET restored_text = ? WHERE id = ?", (text, ID))
    db.conn.commit()


def _copies(vault, name):
    return sorted(p.relative_to(vault).as_posix() for p in vault.rglob(name))


def test_force_rewrites_unedited_note(config, db):
    _, note = _publish_one(config, db)
    _change_data(db, "Cleaned transcript.")
    publish(config, db, force=True)
    assert "Cleaned transcript." in note.read_text()


def test_moved_note_updated_in_place(config, db):
    asset, _ = _publish_one(config, db)
    vault = config.atlas_vault_dir
    for folder in ("voice", "voice/2024"):
        current = vault / _copies(vault, asset.note_filename)[0]
        moved = vault / folder / asset.note_filename
        moved.parent.mkdir(parents=True, exist_ok=True)
        current.rename(moved)
        _change_data(db, f"Cleaned in {folder}.")

        publish(config, db, force=True)

        assert f"Cleaned in {folder}." in moved.read_text()
        assert _copies(vault, asset.note_filename) == [f"{folder}/{asset.note_filename}"]
        assert db.get_asset(ID)["note_path"].endswith(f"/{folder}/{asset.note_filename}")


def test_renamed_note_updated_in_place(config, db):
    asset, note = _publish_one(config, db)
    renamed = note.with_name("2026-10-21 A Better Title.md")
    note.rename(renamed)
    _change_data(db, "Cleaned after rename.")

    publish(config, db, force=True)

    assert "Cleaned after rename." in renamed.read_text()
    assert _copies(config.atlas_vault_dir, asset.note_filename) == []


def test_edited_note_left_alone(config, db):
    _, note = _publish_one(config, db)
    note.write_text(note.read_text() + "\nMy own notes about this memo.\n")
    before = note.read_bytes()
    _change_data(db, "Cleaned after the edit.")

    counts = publish(config, db, force=True)

    assert note.read_bytes() == before
    assert counts["protected"] == 1


def test_edited_private_note_gets_flag_back(config, db):
    _, note = _publish_one(config, db)
    db.set_visibility(ID, "private")
    db.conn.commit()
    publish(config, db, asset_ids=[ID])
    lines = note.read_text().split("\n")
    assert lines[1] == "visibility: private"
    # By hand: the flag line removed and a line of notes added.
    hand = [lines[0]] + lines[2:] + ["My own notes."]
    note.write_text("\n".join(hand))

    publish(config, db, force=True)

    got = note.read_text().split("\n")
    assert got[1] == "visibility: private"
    assert [got[0]] + got[2:] == hand


def test_two_copies_is_conflict(config, db):
    _, note = _publish_one(config, db)
    vault = config.atlas_vault_dir
    one = vault / "voice" / note.name
    two = vault / "3MR" / "A renamed copy.md"
    for copy in (one, two):
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(note.read_text())
    note.unlink()
    before = {copy: copy.read_bytes() for copy in (one, two)}
    files_before = len(list(vault.rglob("*.md")))
    _change_data(db, "Cleaned with two copies.")

    counts = publish(config, db, force=True)

    assert {copy: copy.read_bytes() for copy in (one, two)} == before
    assert len(list(vault.rglob("*.md"))) == files_before
    assert counts["conflict"] == 1


def test_deleted_note_recreated(config, db):
    _, note = _publish_one(config, db)
    note.unlink()
    _change_data(db, "Cleaned after delete.")

    publish(config, db, force=True)

    assert "Cleaned after delete." in note.read_text()
