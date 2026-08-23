"""Transcript helpers shared by the replay harnesses.

Claude Code writes ONE API call as SEVERAL assistant records (thinking / text / tool_use blocks split
across lines), each repeating the SAME `usage`. Counting records as turns inflates turn counts ~1.9×
and double-counts usage (134 records = 71 real calls on django-10554; Σ usage 10.9M vs the
CLI-reported 5.69M). `merged_records` collapses assistant records by `requestId` into one record per
API call — content blocks concatenated in order, usage kept once — so a loop over it counts REAL turns
and REAL tokens. Records without a requestId are passed through unchanged.
"""
from __future__ import annotations

import json


def merged_records(transcript_path: str):
    """Yield records with assistant records merged per requestId (one per real API call)."""
    pending = None                                  # (requestId, merged record)
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        rid = rec.get("requestId") if rec.get("type") == "assistant" else None
        if rid is not None:
            if pending is not None and pending[0] == rid:
                pc = (pending[1].get("message") or {}).get("content")
                c = (rec.get("message") or {}).get("content")
                if isinstance(pc, list) and isinstance(c, list):
                    pc.extend(c)
                continue
            if pending is not None:
                yield pending[1]
            rec = json.loads(json.dumps(rec))       # own copy — we mutate content
            pending = (rid, rec)
            continue
        if pending is not None:
            yield pending[1]
            pending = None
        yield rec
    if pending is not None:
        yield pending[1]


def real_turns(transcript_path: str) -> int:
    return sum(1 for r in merged_records(transcript_path)
               if r.get("type") == "assistant" and (r.get("message") or {}).get("usage"))
