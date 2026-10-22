"""Tests for publish keeping notes that were moved, renamed or edited by hand.

Regression: publish worked out each note's path from atlas.db alone and, when forced,
rewrote it unconditionally. A note moved in Obsidian got a fresh copy written back into
new/, a renamed note got a twin under its generated name, and typed-in text was erased.
"""
import sqlite3

import pytest

from memoryatlas import publisher
from memoryatlas.models import Asset
from memoryatlas.publisher import generate_note_content, publish
from memoryatlas.util import row_to_asset

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


# --- review follow-ups ---------------------------------------------------------------


def _make_private(db):
    db.set_visibility(ID, "private")
    db.conn.commit()


def _has_flag(path):
    text = path.read_text()
    return "visibility: private" in text.split("\n---", 1)[0]


def test_conflict_private_flags_every_copy(config, db):
    _, note = _publish_one(config, db)  # public, so its copies carry no flag
    vault = config.atlas_vault_dir
    copies = [vault / "voice" / note.name, vault / "3MR" / "A renamed copy.md"]
    for copy in copies:
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(note.read_text())
    note.unlink()
    _make_private(db)

    counts = publish(config, db, force=True)

    assert counts["conflict"] == 1
    assert all(_has_flag(copy) for copy in copies)


def test_flag_private_copies_reaches_renamed_copy(config, db):
    _, note = _publish_one(config, db)
    renamed = config.atlas_vault_dir / "voice" / "My own title.md"
    renamed.parent.mkdir(parents=True, exist_ok=True)
    renamed.write_text(note.read_text() + "\nMy notes.\n")
    _make_private(db)
    publish(config, db, asset_ids=[ID])  # writes the recorded note only

    assert publisher.flag_private_copies(config, db, [ID]) == 1
    assert _has_flag(renamed)
    assert renamed.read_text().endswith("\nMy notes.\n")


def test_file_already_holding_new_text_is_not_protected(config, db):
    _, note = _publish_one(config, db)
    _change_data(db, "Written by an interrupted run.")
    # An interrupted run wrote the new text but its hash was never recorded.
    note.write_text(generate_note_content(row_to_asset(db.get_asset(ID))))

    counts = publish(config, db, force=True)
    _change_data(db, "And changed again.")
    publish(config, db, force=True)

    assert counts["protected"] == 0
    assert "And changed again." in note.read_text()


def test_each_note_hash_is_committed_as_it_is_written(config, db, monkeypatch):
    second = Asset(id="SAFE0002-0000-0000-0000-000000000002", source_type="voice_memo",
                   source_path="/t2.m4a", filename="t2.m4a", title="Second",
                   recorded_at="2026-10-21T11:00:00Z", file_format="m4a")
    _, note = _publish_one(config, db)
    db.upsert_asset(second)
    db.conn.commit()
    _change_data(db, "Written before the interrupt.")
    real = publisher.generate_note_content

    def interrupt_on_second(asset):
        if asset.id == second.id:
            raise KeyboardInterrupt
        return real(asset)

    monkeypatch.setattr(publisher, "generate_note_content", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        publish(config, db, force=True)

    committed = sqlite3.connect(config.db_path).execute(
        "SELECT note_hash FROM asset WHERE id = ?", (ID,)).fetchone()[0]
    assert committed == publisher._content_hash(note.read_text())


def test_failed_write_leaves_note_intact(config, db, monkeypatch):
    _, note = _publish_one(config, db)
    before = note.read_bytes()
    _change_data(db, "Never lands.")

    def fail(*args):
        raise OSError("disk went away")

    monkeypatch.setattr(publisher.os, "replace", fail)
    counts = publish(config, db, force=True)

    assert counts["error"] == 1
    assert note.read_bytes() == before
    assert [p.name for p in note.parent.iterdir()] == [note.name]


@pytest.mark.parametrize("uuid_line, bom", [('uuid: "{id}"', ""), ("uuid: {id}", "﻿")])
def test_moved_note_found_despite_quotes_or_bom(config, db, uuid_line, bom):
    asset, note = _publish_one(config, db)
    text = note.read_text().replace(f"uuid: {ID}", uuid_line.format(id=ID))
    moved = config.atlas_vault_dir / "voice" / note.name
    moved.write_text(bom + text, encoding="utf-8")
    note.unlink()

    publish(config, db, force=True)

    assert _copies(config.atlas_vault_dir, asset.note_filename) == [f"voice/{asset.note_filename}"]


def test_restored_db_finds_existing_note(config, db):
    asset, note = _publish_one(config, db)
    moved = config.atlas_vault_dir / "voice" / note.name
    note.rename(moved)
    # atlas.db restored from before this note was published.
    db.conn.execute("UPDATE asset SET note_path = NULL, note_hash = NULL WHERE id = ?", (ID,))
    db.conn.commit()

    publish(config, db)

    assert _copies(config.atlas_vault_dir, asset.note_filename) == [f"voice/{asset.note_filename}"]
    assert db.get_asset(ID)["note_path"].endswith(f"/voice/{asset.note_filename}")


def test_flag_elsewhere_in_frontmatter_left_in_place(config, db):
    _, note = _publish_one(config, db)
    _make_private(db)
    publish(config, db, asset_ids=[ID])
    lines = note.read_text().split("\n")
    # By hand: the flag moved down to line four.
    hand = "\n".join([lines[0], lines[2], lines[3], lines[1]] + lines[4:])
    note.write_text(hand)

    publish(config, db, force=True)

    assert note.read_text() == hand


def test_closing_line_with_trailing_space_gets_no_second_frontmatter(config, db):
    _, note = _publish_one(config, db)
    _make_private(db)
    lines = note.read_text().split("\n")
    end = lines.index("---", 1)
    lines[end] = "--- "
    note.write_text("\n".join(lines))

    publish(config, db, force=True)

    got = note.read_text().split("\n")
    assert got[:2] == ["---", "visibility: private"]
    assert got.count("---") == 1 and "--- " in got


def test_protected_and_conflict_paths_are_printed(config, db, capsys):
    _, note = _publish_one(config, db)
    note.write_text(note.read_text() + "\nMine.\n")

    publish(config, db, force=True)

    assert note.name in capsys.readouterr().out


def test_unforced_publish_picks_up_late_transcript(config, db):
    _, note = _publish_one(config, db)
    _change_data(db, "Transcript that landed after the note.")
    db.conn.execute("UPDATE asset SET transcript_at = '2999-01-01T00:00:00Z' WHERE id = ?", (ID,))
    db.conn.commit()

    publish(config, db)

    assert "Transcript that landed after the note." in note.read_text()


def test_moved_and_edited_note_protected_where_it_is(config, db):
    asset, note = _publish_one(config, db)
    moved = config.atlas_vault_dir / "voice" / note.name
    moved.write_text(note.read_text() + "\nMine.\n")
    note.unlink()
    before = moved.read_bytes()

    counts = publish(config, db, force=True)

    assert counts["protected"] == 1 and moved.read_bytes() == before
    assert db.get_asset(ID)["note_path"].endswith(f"/voice/{asset.note_filename}")
    assert _copies(config.atlas_vault_dir, asset.note_filename) == [f"voice/{asset.note_filename}"]


def test_unforced_publish_records_a_move(config, db):
    asset, note = _publish_one(config, db)
    moved = config.atlas_vault_dir / "voice" / note.name
    note.rename(moved)

    counts = publish(config, db, asset_ids=[ID])

    assert counts["skipped"] == 1
    assert db.get_asset(ID)["note_path"].endswith(f"/voice/{asset.note_filename}")


def test_unknown_hash_with_existing_file_is_protected(config, db):
    _, note = _publish_one(config, db)
    db.conn.execute("UPDATE asset SET note_hash = NULL WHERE id = ?", (ID,))
    db.conn.commit()
    _change_data(db, "Would overwrite.")
    before = note.read_bytes()

    counts = publish(config, db, force=True)

    assert counts["protected"] == 1 and note.read_bytes() == before


def test_title_change_keeps_filename(config, db):
    asset, note = _publish_one(config, db)
    db.conn.execute("UPDATE asset SET title = 'A New Title' WHERE id = ?", (ID,))
    db.conn.commit()

    publish(config, db, force=True)

    assert 'title: "A New Title"' in note.read_text()
    assert [p.name for p in config.atlas_vault_dir.rglob("*.md")] == [note.name]


def test_same_id_twice_publishes_once(config, db):
    _publish_one(config, db)
    _change_data(db, "Once.")

    counts = publish(config, db, asset_ids=[ID, ID])

    assert (counts["updated"], counts["protected"]) == (1, 0)
