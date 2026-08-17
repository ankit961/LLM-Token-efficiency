# Step 5 — the small live A/B/C experiment (protocol)

**Status: harness ready (`corpus/step5_experiment.py`), not yet run.** Justified by Step-4
(`R_direct(256,400)=12.1% ≫ 8%` — see `docs/reduction-replay-scope.md`). This is the FIRST run that
spends Claude quota, and it is deliberately small: **4 search-heavy tasks × the arms below**.

## Arms (same task prompt across all — only the reducer wiring differs)

| arm | wiring | isolates |
|---|---|---|
| **A_native** | no reduction (observe only) | baseline context |
| **B_shipped** | reducer enforce, simple, budget 256 / floor 400 | the deployed default |
| **B_tuned** | reducer enforce, simple, budget 64 / floor 400 | the budget lever (Step-4: +4.4pp) |
| **C_graph** | reducer enforce, **graph-ranked**, budget 256 / floor 400 | graph vs simple at equal budget |

Each arm is a reducer-hook env (`ARMS[...].env(...)`); a run is `claude -p --settings <arm-settings>`
via `corpusrunner.ClaudeBackend` (arm-agnostic). The observation cr-hook + journal are wired in all
arms, so tokens and reads are captured identically.

## Tasks

The 4 tasks with the most B1-eligible search-bucket tokens, selected deterministically from the
Step-4 artifact: `select_search_heavy_tasks("corpus/analysis/reduction-replay-v1.2.json", k=4)`.
These are where a reducer has the most to move (the concentration is here, not spread evenly).

## Metrics (`run_metrics` per arm-run → `compare`)

- **Δtokens(B − A)** — the REDUCTION effect. The journal records the *model-visible* text, so under
  an enforcing arm `total_read_tokens` already reflects the reduction; `token_reduction = A − B`.
  Report retry-inclusive (a re-search's tokens are counted, so savings aren't faked by dropping
  work onto later turns).
- **Δquality(C − B)** — the GRAPH effect. NOT tokens (C and B share a budget → token-neutral by
  construction; the harness surfaces `token_delta ≈ 0` as a check). It is retention QUALITY:
  `re_search_delta` (fewer = graph kept the matches the agent needed) at equal **task success**.
  Expansion/CED plugs in from the `semantic_reads` telemetry once the MCP recovery path is
  instrumented in the live run.
- Hygiene: the decision log now records `reason=non_beneficial` passes (raw≥reduced), so
  `candidates_seen` distinguishes "seen but not worth it" from "never seen" — admission and
  benefit rates are both measurable.

## Interpretation

- **B − A materially < 0** (reducer saved context) **at equal task success + wall-time ≤ ~1.2×**
  → transparent reduction earns its place; ship the winning (budget, floor).
- **C − B**: fewer re-searches / expansions at equal success → graph ranking earns its complexity;
  otherwise ship simple (B) and keep graph observe-only.
- Any arm with **worse task success** than A is a red flag regardless of token savings — the point
  is cheaper context, not degraded outcomes.

## Safety (unchanged — all B1 invariants hold live)

Every arm still: reduces only prospectively-recognizable search/listing; requires a confirmed live
version; replaces only on exact CAS recovery; never replaces unless smaller; and fails open. The
experiment cannot degrade safety — only measure whether the (safe) reduction is worth it.
