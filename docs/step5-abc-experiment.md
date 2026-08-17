# Step 5 — the small live A/B/C experiment (protocol)

**Status: harness ready (`corpus/step5_experiment.py`), not yet run.** Justified by Step-4
(`R_direct(256,400)=12.1% ≫ 8%` — see `docs/reduction-replay-scope.md`). This is the FIRST run that
spends Claude quota, and it is deliberately small: **4 search-heavy tasks × the arms below**.

## Arms (same task prompt across all — only the reducer wiring differs)

| arm | wiring | isolates |
|---|---|---|
| **A_native** | native output + reducer hook in **observe** (CR_REDUCE_MODE unset) | baseline context, equal instrumentation |
| **B_shipped** | reducer enforce, simple (**graph off**), budget 256 / floor 400 | the deployed default |
| **B_tuned** | reducer enforce, simple (**graph off**), budget 64 / floor 400 | the budget lever (Step-4: +4.4pp) |
| **C_graph** | reducer enforce, **graph on**, budget 256 / floor 400 | graph vs simple at equal budget |

`A_native` is **not** a hook-free run — it carries the observation cr-hook AND the reducer hook (in
observe), so instrumentation overhead is equal across arms. For a pure *latency* baseline, add a
genuinely hook-free arm separately.

An arm is realized as an **arm-specific settings file** (`build_arm_settings`) whose PostToolUse
reducer command carries that arm's env **inline** — the reducer is its own process. **Isolation is
explicit** (a subprocess inherits the parent env): every arm sets `CR_REDUCE_MODE` (observe/enforce)
and `CR_GRAPH_MODE` (off/on) explicitly, and the command begins `env -u CR_LIVE_CLIENT_VERSION …` to
clear any inherited version override. **B cannot graph-rank** (`CR_GRAPH_MODE=off`, no `CR_GRAPH_DB`)
even if the parent env has them; C sets `CR_GRAPH_MODE=on` + `CR_GRAPH_DB`. The harness **never** sets
`CR_LIVE_CLIENT_VERSION` — enforcement stays on the real `claude --version` probe (B1.0.4 fail-safe).

`run_arm` **preflights** the live version (`preflight_or_raise`) and refuses to spend quota if an
enforcing arm couldn't reduce, and wires a **recovery MCP** (`context_expand` over the arm's live
CAS) so a `result://` handle is actually callable. `Step5Runner` owns the whole controlled run: a
fresh worktree, (for C) a graph **built from that exact worktree** and provenance-stamped to the
base commit, the run, and immutable artifacts (`agent.patch`, `agent-result.json`, `manifest.json`
with `budget_walltime` + graph provenance, `hashes.json`, `evaluation.json`).

## Tasks

The 4 tasks with the most B1-eligible search-bucket tokens, selected deterministically from the
Step-4 artifact: `select_search_heavy_tasks("corpus/analysis/reduction-replay-v1.2.json", k=4)`.
These are where a reducer has the most to move (the concentration is here, not spread evenly).

## Metrics (`run_metrics` per arm-run → `compare`)

- **Δtokens(B − A)** — the REDUCTION effect on **effective** model-visible tokens. HookJournal stamps
  `model_visible_tokens` at PostToolBatch time from the **model-visible** payload
  (`measure_model_visible_response`) — i.e. AFTER `updatedToolOutput` replacement — so the journal
  **already reflects the reduction**. Effective tokens are therefore the journal total **directly**;
  `effective_read_tokens = Σ journal model_visible_tokens`, `token_reduction = A_eff − B_eff`. The
  reducer's own `saved_tokens` is a **cross-check, never a second subtraction** (that double-counts).
  Retry-inclusive — a re-search's tokens are counted.
- **CANARY first:** before the real 4-task run, `verify_token_accounting` joins enforced reductions to
  the journal by `tool_use_id` and checks `journal model_visible ≈ reducer reduced`. If they diverge,
  PostToolBatch didn't see the replacement on this client and the accounting must be revisited.
- **Δquality(C − B)** — the GRAPH effect. **Total tokens are an OUTCOME, not an invariant**: graph
  ranking changes *which* evidence Claude sees, so C and B totals need not match (`effective_token_delta`
  is reported as an outcome). The signal is fewer **re-searches** at equal **task success** —
  `exact_search_repeat_delta` (same tool+pattern+scope fingerprint re-run; precise) alongside a broader
  `repeated_scope_delta`. Expansion/CED plugs in from `semantic_reads` once instrumented live.
- **C validity:** `compare` sets `c_valid = (reductions_enforced>0 AND graph_ranked>0)` — C must have
  actually TESTED graph-ranked reduction. Zero reductions, or a stale graph that never engaged, is an
  **invalid** C (with a warning), not a pass.
- Hygiene: the decision log records `reason=non_beneficial` passes and a privacy-safe `fingerprint`
  (+ `tool_use_id`), so `candidates_seen` distinguishes "seen but not worth it" from "never seen";
  wall-time (`budget_walltime`, from the manifest) feeds the ≤1.2× gate.

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
