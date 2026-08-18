"""B2.3 — edit-recall replay: a body edit misses the skeleton (forced re-read), a signature hits."""
import json

from corpus.edit_recall_replay import edit_recall, parse_reads_edits

_FILE = (
    '"""doc."""\n'
    "import os\n"
    "\n"
    "def foo():\n"
    "    return compute() + helper()\n"           # a BODY line — dropped from the skeleton
) + "\n".join(f"    pad_{i} = {i}" for i in range(300))     # pad so the read is big enough to compact


def _transcript(tmp_path, old_string):
    lines = [
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "foo.py"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "content": _FILE}]}},
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
            {"type": "tool_use", "id": "e1", "name": "Edit",
             "input": {"file_path": "foo.py", "old_string": old_string, "new_string": "x"}}]}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return str(p)


def test_body_edit_misses_skeleton_forcing_reread(tmp_path):
    tp = _transcript(tmp_path, "    pad_150 = 150")                   # a body line deep past the head
    r = edit_recall(tp)
    assert r["edits_scored"] == 1 and r["edit_recall_full"] == 0.0    # body dropped ⇒ miss
    assert r["forced_reread_edits"] == 1 and r["first_edit_share"] == 1.0


def test_signature_edit_hits_skeleton(tmp_path):
    tp = _transcript(tmp_path, "def foo():")                          # a structural line, kept
    r = edit_recall(tp)
    assert r["edits_scored"] == 1 and r["edit_recall_full"] == 1.0    # signature survives ⇒ hit
    assert r["forced_reread_edits"] == 0


def test_reads_and_edits_parsed(tmp_path):
    tp = _transcript(tmp_path, "def foo():")
    reads, edits = parse_reads_edits(tp)
    assert len(reads) == 1 and reads[0][1] == "foo.py"
    assert len(edits) == 1 and edits[0][1] == "foo.py"
