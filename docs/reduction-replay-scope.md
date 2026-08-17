# Step 4 — Offline Reduction Replay: scope & method

**Status: harness built + fixture-tested (`corpus/reduction_replay.py`), awaiting a run over the
frozen journals.** Zero LLM cost. This is the deterministic gate before any live experiment
(step 5): measure what the B1 transparent reducer *actually captures* before spending quota.

## The question

The opportunity-ceiling analysis (`docs/FINDINGS.md` §4) found the `search_listing_reducible`
bucket is **73,360 tok = 29.7%** of fully-measured read tokens — *candidate mass*. Step 4 asks
the realized-savings question: under the reducer's **actual** behavior, what fraction of that
bucket becomes real token savings?

    R_search = saved_tokens / search_bucket_tokens
    R_direct = saved_tokens / all_fully_measured_read_tokens   ( = R_search × 0.297 )

## Why no raw payload text is needed (and none exists)

The frozen journals are **metadata-only** — per-read `model_visible_tokens`, `representation`,
`path_normalized`, `session_id`; no raw grep/find output. That's fine, because the reducer's
output size is a *contract*, not content-dependent:

> **Calibrated fact:** above the `MIN_REDUCE` floor, `reduce_search` compacts any search/listing
> output to a **near-constant CAP(budget)** — measured flat at ~244 tok (budget 256) from 800-tok
> to 100k-tok inputs. `cap_calibration` in the output records CAP per budget (e.g. 64→62,
> 128→128, 256→244), measured from the real reducer, not assumed.

So `reduced_i` is a function of `raw_i` alone:

    saved_i = raw_i − CAP(budget)   for raw_i ≥ threshold ;   0 otherwise
    threshold = max(floor, CAP(budget))     # you only save on reads LARGER than the cap

## ⚠️ The caveat that governs the result

The bucket is **73,360 tok across 306 reads — mean 240 tok/read**, which is *below* both the
400-tok floor and the ~244 cap. Most search/listing reads in this corpus are **small**; the
reducer only bites on *large* outputs. Therefore:

- The naive "29.7% × 70% ≈ 20.8%" illustration almost certainly **does not hold here**.
- Realized `R_search` is governed entirely by **token concentration** — how much of the 73,360
  lives in the few big reads. The harness's `concentration` block (histogram + `mass_share_above
  _ref_floor`) is the single most decisive output: a heavy tail → real savings; a uniformly small
  bucket → ~none. **Do not quote R_search without the concentration figure next to it.**

## Graph ranking is token-neutral — by design, not omission

Simple and graph reducers **both** cap at CAP(budget), so `R_search` is identical for both arms;
the harness reports **one** token number and says so (`graph_note`). B1.2's value — keeping the
*relevant* matches within that cap — is a **retention-quality** question (does graph keep matches
in files the session later touched?) that needs raw text + a relevance ground truth. That is the
job of the **live step-5 A/B/C** (A native, B simple reducer, C graph reducer), not this offline
pass.

## The tuning lever the harness also answers (free, offline)

Because CAP is the binding constraint, **budget is the real lever**: a smaller budget lowers the
cap, so more reads clear it. The `floor` only blocks reads beneath it — set `floor ≈ CAP` and
mid-size reads (currently passed through by the shipped `floor=400`) get captured. The
`--budgets`/`--floors` grid quantifies this: e.g. the shipped `(256, 400)` vs a tuned `(64, ~62)`.
Any budget change is subject to the B1 recovery invariant — reduction still only fires when the
CAS confirms complete recovery (a smaller budget does not change that).

## Micro vs macro (read the robustness, not just the headline)

- **micro** (`R_search_micro`): pooled over all reads — token-weighted; dominated by whale runs.
- **macro** (`R_search_macro`): mean of per-run `R_search` — task-weighted; the median task.

A large micro/macro gap means a few heavy runs carry the headline (the same concern
`opportunity_ceiling`'s robustness block raised for the ceiling). Report both.

## How to run it

Over the frozen corpus (the journals live on the collection machine, gitignored — real paths):

```bash
python -m corpus.reduction_replay <runs_dir> corpus/analysis/reduction-replay-v1.json \
    --budgets 64,128,256 --floors 244,400
```

`<runs_dir>` = the same `run-*/` layout `opportunity_ceiling` consumes (`journal.sqlite` +
`manifest.json`). Read-only; never mutates a journal. Reuses the frozen classifier + bucketing
verbatim, so a search/listing read is defined identically to the ceiling analysis.

## What is verified here vs. what needs the corpus

- **Verified now** (`tests/test_reduction_replay.py`, synthetic journals): cap calibration flat &
  budget-scaled; the cap savings model (only reads above threshold reduce, never negative); the
  floor-tuning behavior; search-bucket isolation via the frozen classifier; the concentration
  report; empty-bucket → `None`, no crash.
- **Needs the frozen journals** (author's machine): the actual `R_search`/`R_direct` numbers and
  the real concentration — the harness is ready; only the data is remote.

## Decision this feeds

If the concentration is heavy (real `R_search` materially > 0) → the transparent reducer earns the
tiny step-5 live A/B/C, and the grid says which budget to ship. If the bucket is uniformly small
(`R_search ≈ 0`) → transparent search compaction has a low ceiling *on this workload*, and the
evidence points back to the roadmap's prefix-size question rather than more reducer tuning. Either
way it's a real answer, bought with zero quota.
