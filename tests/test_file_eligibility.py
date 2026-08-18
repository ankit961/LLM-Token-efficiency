"""B2.2 — edit-safety gate + offline measured file reduction. The safety-critical B2 logic."""
import sqlite3

from contextruntime.reducers.base import tokens
from contextruntime.reducers.fileeligibility import (edited_paths_from_journal, file_read_eligible)
from corpus.file_reduction_replay import measured_file_reduction

_BIG = ('"""doc."""\nimport os\n' + "\n".join(f"    v_{i} = {i}" for i in range(500)))   # big, structural head


def test_eligible_only_for_unedited_file_reads():
    assert file_read_eligible("Read", {"file_path": "a/b.py"})                       # un-edited ⇒ eligible
    assert not file_read_eligible("Read", {"file_path": "a/b.py"},
                                  edited_paths=frozenset({"a/b.py"}))                # edited ⇒ spared
    assert not file_read_eligible("Grep", {"pattern": "x"})                          # not a file read
    assert not file_read_eligible("Read", {})                                        # no file_path
    assert not file_read_eligible("Edit", {"file_path": "a/b.py"})                   # the edit itself


def test_edited_paths_from_journal_reads_mutations(tmp_path):
    db = str(tmp_path / "journal.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tool_events (session_id TEXT, kind TEXT, path_normalized TEXT)")
    conn.executemany("INSERT INTO tool_events VALUES (?,?,?)", [
        ("s1", "read", "a/read_only.py"),          # a read — NOT an edit target
        ("s1", "edit", "a/edited.py"),             # mutated ⇒ spare
        ("s1", "edit", "a/edited.py"),             # dup ⇒ DISTINCT
        ("s2", "edit", "other/session.py"),        # different session ⇒ excluded
    ])
    conn.commit(); conn.close()
    got = edited_paths_from_journal(db, "s1")
    assert got == frozenset({"a/edited.py"})
    assert edited_paths_from_journal(db, None) == frozenset()          # no session ⇒ empty
    assert edited_paths_from_journal(None, "s1") == frozenset()        # no journal ⇒ empty
    assert edited_paths_from_journal(str(tmp_path / "missing.db"), "s1") == frozenset()  # fail-open


def test_measured_file_reduction_reduces_unedited_big_file():
    red, elig = measured_file_reduction(_BIG, "Read", {"file_path": "a/big.py"})
    assert elig and red < tokens(_BIG)


def test_measured_file_reduction_spares_edit_target():
    red, elig = measured_file_reduction(_BIG, "Read", {"file_path": "a/big.py"},
                                        edited_paths=frozenset({"a/big.py"}))
    assert not elig and red == tokens(_BIG)        # active edit target ⇒ kept full


def test_measured_file_reduction_floors_small_files():
    tiny = "import os\nprint(1)\n"
    red, elig = measured_file_reduction(tiny, "Read", {"file_path": "a/tiny.py"}, floor=400)
    assert not elig and red == tokens(tiny)


def test_measured_file_reduction_ignores_non_file_reads():
    red, elig = measured_file_reduction(_BIG, "Grep", {"pattern": "v_"})
    assert not elig and red == tokens(_BIG)
