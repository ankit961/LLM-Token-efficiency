#!/usr/bin/env python3
"""Deterministically split predictions.jsonl into N shards for parallel GitHub Actions grading.

Shard membership is by sorted instance_id modulo N -- stable regardless of file order, so re-running
a single shard (e.g. after a transient CI failure) never touches another shard's tasks.

Writes the shard's predictions to out_path (official SWE-bench predictions.jsonl format, unchanged)
and prints its instance_ids SPACE-SEPARATED on a single stdout line, for direct use as
`--instance_ids $(...)` in the harness CLI.

Usage: python3 corpus/shard_predictions.py <predictions.jsonl> <shard_index> <n_shards> <out.jsonl>
"""
from __future__ import annotations

import json
import sys


def shard(preds_path: str, shard_index: int, n_shards: int, out_path: str) -> list:
    rows = [json.loads(line) for line in open(preds_path) if line.strip()]
    rows.sort(key=lambda r: r["instance_id"])
    mine = [r for i, r in enumerate(rows) if i % n_shards == shard_index]
    with open(out_path, "w") as f:
        for r in mine:
            f.write(json.dumps(r) + "\n")
    return [r["instance_id"] for r in mine]


if __name__ == "__main__":
    preds_path, shard_index, n_shards, out_path = sys.argv[1:5]
    ids = shard(preds_path, int(shard_index), int(n_shards), out_path)
    print(" ".join(ids))
