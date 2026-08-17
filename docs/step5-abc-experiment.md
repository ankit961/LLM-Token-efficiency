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
reducer hook command carries that arm's env **inline** — the reducer runs as its own process, so its
env must come from the hook command, not the outer `claude -p` env. This is also what makes B/C
isolation real: **B's reducer command omits `CR_GRAPH_DB` entirely**, so graph ranking cannot engage
(the installer's default command always embeds the graph vars — omitting them from the outer env
alone would not disable graph). A run goes through `run_arm(arm, backend, …)` → `ClaudeBackend`.
The harness **never** sets `CR_LIVE_CLIENT_VERSION`: enforcement stays gated on the real
`claude --version` probe (B1.0.4 fail-safe) — the experiment must not enforce on an unverified binary.

## Tasks

The 4 tasks with the most B1-eligible search-bucket tokens, selected deterministically from the
Step-4 artifact: `select_search_heavy_tasks("corpus/analysis/reduction-replay-v1.2.json", k=4)`.
These are where a reducer has the most to move (the concentration is here, not spread evenly).

## Metrics (`run_metrics` per arm-run → `compare`)

- **Δtokens(B − A)** — the REDUCTION effect on **effective** model-visible tokens. The observation
  journal records the **raw** event (the reducer replaces output independently — `install.py`), so we
  do NOT read reduced sizes from the journal. Instead:
  `effective_read_tokens = journal_raw_read_tokens − reducer_saved_tokens` (saved comes from the
  decision log, the authoritative source of reduced sizes); `token_reduction = A_eff − B_eff`.
  Retry-inclusive — a re-search's tokens are counted, so savings aren't faked by pushing work later.
- **Δquality(C − B)** — the GRAPH effect. **Total tokens are an OUTCOME, not an invariant**: graph
  ranking changes *which* evidence Claude sees, which can change the whole trajectory, so C and B
  total tokens need not be equal (the harness reports `effective_token_delta` as an outcome). The
  retention signal is fewer **re-searches** at equal **task success** — `re_search_fingerprint_delta`
  (same tool+pattern+scope hash re-run; the precise signal) alongside a broader `re_search_scope`
  proxy. Expansion/CED plugs in from `semantic_reads` once the MCP recovery path is instrumented live.
- **C validity:** `compare` flags `c_graph_engaged=false` (with a warning) if C enforced reductions
  but `graph_ranked==0` — a broken/stale graph silently collapses C into B, and any C−B signal would
  be meaningless.
- Hygiene: the decision log records `reason=non_beneficial` passes (raw≥reduced) and a privacy-safe
  search `fingerprint`, so `candidates_seen` distinguishes "seen but not worth it" from "never seen",
  and wall-time (`budget_walltime`) is read for the ≤1.2× gate.

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
