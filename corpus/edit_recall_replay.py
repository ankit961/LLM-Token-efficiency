#!/usr/bin/env python3
"""B2.3 — offline EDIT-RECALL replay for file-read residency. ZERO Claude quota.

The file analog of Step 6.1's line-recall, but the outcome is EDITS — what actually breaks. Over the
retained native transcripts (agent saw FULL file content, then edited): if B2 had compacted the read
the agent used, would the edit's `old_string` still be present in the skeleton? A HIT means the agent
could have constructed the edit from residency (no extra work). A MISS means it would have re-read
first (a forced re-read — the efficiency cost that eats the residency saving). It is NEVER a
correctness break: the exact raw is in the CAS and Edit matches `old_string` against the file on disk.

    edit_recall = fraction of edits whose old_string lines ALL survive the compacted skeleton
"""
from __future__ import annotations

import glob
import json
import os

from contextruntime.reducers.library import FILE_BUDGET_TOKENS, _LINENO_PREFIX, reduce_file

_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _strip_ln(line: str) -> str:
    return _LINENO_PREFIX.sub("", line, count=1).rstrip()


def parse_reads_edits(transcript_path: str):
    """(reads, edits): reads = [(turn, file_path, content)]; edits = [(turn, file_path, old_string)]
    over Edit/MultiEdit (Write is a full overwrite — no prior-content match needed, excluded)."""
    reads, edits, uses, turn = [], [], {}, 0
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            if msg.get("usage"):
                turn += 1
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                name, inp = b.get("name"), (b.get("input") or {})
                uses[b.get("id")] = name
                fp = inp.get("file_path")
                if name in _EDIT_TOOLS and fp:
                    if name == "MultiEdit":
                        for e in (inp.get("edits") or []):
                            if e.get("old_string"):
                                edits.append((turn, fp, e["old_string"]))
                    elif inp.get("old_string"):
                        edits.append((turn, fp, inp["old_string"]))
        elif rec.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and uses.get(b.get("tool_use_id")) in ("Read", "NotebookRead"):
                    # the read's path is on the paired tool_use; recover it lazily below
                    reads.append([turn, b.get("tool_use_id"), _text(b.get("content"))])
    # join read path from the Read tool_use inputs
    rp = {}
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        for b in ((rec.get("message") or {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Read", "NotebookRead"):
                rp[b.get("id")] = (b.get("input") or {}).get("file_path")
    reads = [(t, rp.get(tid), c) for (t, tid, c) in reads if rp.get(tid)]
    return reads, edits


def edit_recall(transcript_path: str, *, budget: int = FILE_BUDGET_TOKENS, gated: bool = True) -> dict:
    """For each edit, take the most recent prior read of that file. Under the B2.2 gate, that read was
    COMPACTED only if the file had not yet been edited at read time (else it was SPARED → full content
    → the edit trivially hits). `gated=False` compacts every read (worst case). A HIT means the edit's
    old_string fully survives the skeleton (no re-read); a MISS forces a re-read (not a break).
    Also reports the FIRST-edit share — the first edit of a file whose only prior read was compacted
    is structurally a miss (the agent never saw the body), the core prospective-compaction trap."""
    reads, edits = parse_reads_edits(transcript_path)
    first_edit = {}
    for (te, path, _old) in sorted(edits):
        first_edit.setdefault(path, te)
    scored = hits = first_edits = 0
    for (te, path, old) in edits:
        prior = [(tr, c) for (tr, p, c) in reads if p == path and tr <= te]
        if not prior:
            continue                                    # no prior read (Write-created) — not scored
        tr, content = max(prior, key=lambda x: x[0])
        old_lines = [l.rstrip() for l in old.splitlines() if l.strip()]
        if not old_lines:
            continue
        scored += 1
        is_first = te <= first_edit.get(path, te)
        first_edits += 1 if is_first else 0
        compacted = (not gated) or (tr <= first_edit.get(path, tr))    # gate spares reads after 1st edit
        if not compacted:
            hits += 1                                   # spared read ⇒ full content ⇒ hit
            continue
        skeleton = {_strip_ln(l) for l in reduce_file(content, {}, budget_tokens=budget).reduced_text.splitlines()}
        hits += 1 if all(ol in skeleton for ol in old_lines) else 0
    return {"edits_scored": scored, "first_edit_share": round(first_edits / scored, 4) if scored else None,
            "edit_recall_full": round(hits / scored, 4) if scored else None,
            "forced_reread_edits": scored - hits}


def edit_recall_over_runs(results_json: str, *, arm: str = "A_native",
                          budget: int = FILE_BUDGET_TOKENS, gated: bool = True) -> dict:
    res = json.load(open(results_json))
    scored = hits = first = 0
    for key, m in res.items():
        if f"|{arm}|" not in key or not isinstance(m, dict) or "error" in m:
            continue
        tp = m.get("transcript")
        if not (tp and os.path.exists(tp)):
            continue
        r = edit_recall(tp, budget=budget, gated=gated)
        s = r["edits_scored"]
        scored += s; hits += s - r["forced_reread_edits"]
        first += round((r["first_edit_share"] or 0) * s)
    return {"edits_scored": scored, "gated": gated,
            "edit_recall_full_pooled": round(hits / scored, 4) if scored else None,
            "first_edit_share_pooled": round(first / scored, 4) if scored else None,
            "forced_reread_edits": scored - hits}


def _main(argv) -> None:
    """python -m corpus.edit_recall_replay <step7_run_dir>/step7-results.json"""
    for gated in (True, False):
        r = edit_recall_over_runs(argv[1], gated=gated)
        tag = "B2.2-gated (spare edited files)" if gated else "ungated (compact every read)"
        print(f"EDIT-RECALL {tag}: {r['edits_scored']} edits  "
              f"recall={r['edit_recall_full_pooled']}  forced_rereads={r['forced_reread_edits']}  "
              f"first_edit_share={r['first_edit_share_pooled']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
