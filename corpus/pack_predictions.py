#!/usr/bin/env python3
"""Package the 50 Arm-A (native) agent patches into the official SWE-bench predictions format.

Reads ONLY agent.patch + manifest.json from each frozen run-NN directory. Never touches
journal.sqlite, cr-hook-settings.json, or any prompt/telemetry file — those stay local. Output
is exactly what the official `swebench` harness consumes:

    predictions.jsonl   one JSON object per line: {instance_id, model_patch, model_name_or_path}

and a side index (run_index.json, NOT sent anywhere — used locally to join grading results back
to fix-shape strata) mapping instance_id -> {run, stratum, termination_reason}.

Usage: python3 corpus/pack_predictions.py <runs_dir> <out_dir>
"""
from __future__ import annotations

import glob
import json
import os
import sys

MODEL_NAME = "contextruntime-arm-a-native-scaffold-v1"


def pack(runs_dir: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    preds_path = os.path.join(out_dir, "predictions.jsonl")
    index = {}
    n = 0
    with open(preds_path, "w") as pf:
        for run_dir in sorted(glob.glob(os.path.join(runs_dir, "run-*"))):
            man_path = os.path.join(run_dir, "manifest.json")
            patch_path = os.path.join(run_dir, "agent.patch")
            if not (os.path.exists(man_path) and os.path.exists(patch_path)):
                continue
            man = json.load(open(man_path))
            patch = open(patch_path).read()
            tid = man["task_id"]
            pf.write(json.dumps({
                "instance_id": tid,
                "model_patch": patch,
                "model_name_or_path": MODEL_NAME,
            }) + "\n")
            index[tid] = {
                "run": os.path.basename(run_dir),
                "stratum": man.get("category"),
                "termination_reason": man.get("termination_reason"),
                "empty_patch": patch.strip() == "",
                "patch_sha256": man.get("patch_sha256"),
            }
            n += 1
    json.dump(index, open(os.path.join(out_dir, "run_index.json"), "w"), indent=2)
    return {"n_packed": n, "predictions_path": preds_path,
           "index_path": os.path.join(out_dir, "run_index.json")}


if __name__ == "__main__":
    runs_dir, out_dir = sys.argv[1], sys.argv[2]
    result = pack(runs_dir, out_dir)
    print(json.dumps(result, indent=2))
