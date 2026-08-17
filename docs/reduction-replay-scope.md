# Step 4 — Offline Reduction Replay: scope & method

**Status: harness built + fixture-tested (`corpus/reduction_replay.py`), awaiting a run over the
frozen journals.** Zero LLM cost. This is the deterministic gate before any live experiment
(step 5): estimate — and, given raw payloads, measure — how much the B1 transparent reducer
captures, before spending quota.

## The question

The opportunity-ceiling analysis (`docs/FINDINGS.md` §4) found the `search_listing_reducible`
bucket is **73,360 tok = 29.7%** of fully-measured read tokens — *candidate mass*. Step 4 asks the
capture question: what fraction of that bucket becomes real token savings — estimated from token
counts (cap model), or measured on raw payloads when they're available?

    R_search = saved_tokens / search_bucket_tokens
    R_direct = saved_tokens / all_fully_measured_read_tokens   ( = R_search × search_bucket_share )

Note: `search_bucket_share` is the **B1-eligible** share (search + path_listing, `derived` excluded)
and is computed by the replay — it is SMALLER than the ceiling's 29.7%, which included `derived`.
Do not multiply by 0.297.

## Two modes: a metadata-only estimate, and a true replay

The frozen journals are **metadata-only** — per-read `model_visible_tokens`, `representation`,
`path_normalized`, `session_id`; **no raw grep/find output**. So there are two ways to get a number,
and the harness supports both:

**(a) Metadata-only ESTIMATE (default, no raw text).** Above the `MIN_REDUCE` floor, `reduce_search`
compacts a search/listing output to *roughly* a constant CAP(budget) — calibrated from the real
reducer (flat ~244 tok at budget 256 across 800-tok..100k-tok *uniform* inputs; `cap_calibration`
records CAP per budget, e.g. 64→62, 128→128, 256→244). Modeled:

    saved_i = raw_i − CAP(budget)   for raw_i ≥ threshold ;   0 otherwise
    threshold = max(floor, CAP(budget))     # you only save on reads LARGER than the cap

> ⚠️ **This is an ESTIMATE, not a measured replay, and its bias direction is NOT guaranteed.** CAP is
> *not* a content-independent contract. The real `reduce_search` output is content-dependent
> (preserved diagnostics `must_keep = [header] + diags`, the rollup's real filenames/counts, retained
> line lengths) **and it breaks on the first line that doesn't fit** — so a single huge match yields
> only header+rollup+handle (T_real **< CAP**), while long diagnostics/paths push T_real **> CAP**.
> The estimate can therefore err in either direction. The output is labeled
> `method: metadata_only_calibrated_cap_estimate`; never quote it as "actual"/"realized" savings, and
> prefer the measured true-replay path whenever raw payloads exist.

**(b) True MEASURED replay (preferred, when raw text is available).** If the original tool outputs
can be reconstructed (e.g. from transcripts), `measured_reduction()` / `true_replay_search()` run
the **real gate + real `reduce_search`** on each payload and return the exact reduction —

    R_search = Σ (T_i_raw − T_i_reduced) / Σ T_i_raw     # measured, no cap model

This mirrors the hook's decision path exactly (gate pass-through and the MIN_REDUCE floor included),
so it is a genuine reducer result, not an approximation. Use this whenever the raw payloads exist.

## Only what B1 actually reduces is counted (derived excluded)

The ceiling's `search_listing_reducible` bucket groups `search` + `path_listing` + **`derived`**
(all `non_file_materialization_role_unresolved`). But B1's gate reduces **only** `search` and
`path_listing` (`gate.REDUCIBLE_REPRESENTATIONS`) — it deliberately leaves `derived` (a `| wc -l`
summary) untouched. The replay filters to B1's eligible set per-read via the journal's
`representation` column, and reports the excluded `derived` reads/tokens separately
(`derived_excluded`). Counting them would systematically **overestimate** B1 capture.

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
mid-size reads (passed through by the default `floor=400`) get captured. The `--budgets`/`--floors`
grid quantifies this: e.g. the default `(256, 400)` vs a tuned `(64, ~62)`.

**Every grid point is DEPLOYABLE**, not hypothetical: the live hook reads the budget from
`CR_REDUCE_BUDGET` and the floor from `CR_REDUCE_FLOOR`, both defaulting to the shipped values
(256 / 400). Each grid row is tagged `deployable: "default"` (the shipped `floor=400`) or
`"requires CR_REDUCE_FLOOR"`. Any budget/floor change is still subject to the B1 recovery invariant
— reduction only fires when the CAS confirms exact recovery, regardless of budget or floor.

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
- **Needs the frozen journals** (author's machine): the estimated `R_search`/`R_direct` numbers and
  the real concentration — the harness is ready; only the data is remote.

## Decision this feeds

If the concentration is heavy (real `R_search` materially > 0) → the transparent reducer earns the
tiny step-5 live A/B/C, and the grid says which budget to ship. If the bucket is uniformly small
(`R_search ≈ 0`) → transparent search compaction has a low ceiling *on this workload*, and the
evidence points back to the roadmap's prefix-size question rather than more reducer tuning. Either
way it's a real answer, bought with zero quota.
