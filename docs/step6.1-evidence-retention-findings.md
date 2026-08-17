# Step 6.1 — line-level evidence-retention replay (findings)

**Run 2026-08-18, ZERO Claude quota.** A reviewer correctly flagged that Step 6's headline —
"100% path-recall ⇒ no room for graph ranking to improve" — does **not** follow from the metric.
`path_recall` asks only whether a subsequently-touched file's path *string* still appears in the
compact output. But when the reducer truncates it emits a per-file rollup that **names up to 12
matched files even when their actual match lines were dropped**. So `named(path) ≠ useful line
retained`, and graph ranking changes exactly the thing name-recall is blind to: *which match lines*
survive the budget. Step 6.1 measures the right thing.

## Method

Same 16 pilot transcripts and same paired discipline as Step 6, but per reducible-and-firing search
event we now run **both** reducers on the identical raw output:

- **graph arm** uses a REAL per-task django code graph (the pilot's own `C_graph/codegraph.db`,
  indexed at that task's `base_commit`, 2.6–2.8k files, ~145k working-set edges) and a working set
  built **only from events preceding the search** (`touched` = files Read/Edit/Written earlier in
  the trajectory) — exactly what the live hook would have had.
- For each subsequently-needed path we record **Named** (path string present) vs **LineRetained**
  (≥1 real `path:lineno:` match line survived, not merely the rollup name), under each arm.

Faithfulness: reuses the reducer's own constants, `graphrank.path_scores` collapse,
`_kept_match_lines`, and the real `reduce_search`. Only `_proximity` is reimplemented in-memory
(django's degree-6857 hub makes the sqlite-per-node walk too slow to sweep); it is unit-tested to
match `graphrank._proximity` byte-for-byte on a fake store over identical edges
(`tests/test_graph_evidence_replay.py`). Reproduce:
`python -m corpus.graph_evidence_replay '<transcript-glob>' <pilot_dir>`.

## Results

| config (b,f) | needed | graph active events | kept match-lines S/G | graph reordered | **name-recall** | **LINE-recall S/G** | inline-evidence deficit | promoted-needed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| (256,**400**) shipped | 5 | 0 | 7/7 | 0 | 1.00 | **0.40** | 3/5 | 0 |
| (256,244) | 16 | 7 | 74/73 | 1 | 1.00 | **0.56** | 7/16 | 0 |
| (256,125) | 16 | 7 | 74/73 | 1 | 1.00 | **0.56** | 7/16 | 0 |
| (64,244) | 16 | 7 | 2/2 | 0 | 1.00 | **0.125** | 14/16 | 0 |
| (64,**125**) aggressive | 31 | 12 | 3/3 | 0 | 1.00 | **0.097** | 28/31 | 0 |

## 1. "Lossless" was wrong — name-recall hid a large line-level loss

Name-recall is 100% everywhere (Step 6's number), but **LINE-recall is 0.40 at the shipped setting
and falls to ~0.10 at the aggressive floor**. At `(64,125)`, **28 of 31** subsequently-needed paths
survive as a rollup *name only* — their actual match line is absent from the compact output. I call
that the **inline-evidence deficit** (`named − line-retained`), *not* "expansion pressure": it is
**not a measured recovery cost**. If that omitted match text were needed, the agent has several
routes to it — a native `Read`, another search, or an exact `result://` expansion — and, crucially,
`needed` is *defined* as files the original trajectory went on to open anyway, so many of these
deficits would incur **no extra operation** in the counterfactual. What is established is
representational: the compression is achieved largely by **replacing match lines with file names +
a recovery handle**, not by keeping the useful lines — so "lossless" is wrong. Whether the deficit
turns into real work is precisely what the live experiment must measure.

## 2. Budget — not floor — is the line-retention lever

Kept match-lines collapse from **74** at budget 256 to **2–3** at budget 64. So the very move that
buys the big token capture (dropping budget 256→64 to push `R_paired` from ~18% to ~54%) is what
strips inline evidence: line-recall 0.56 → 0.10. The floor decides *how often* reduction fires; the
**budget decides how much real evidence survives inside each reduced result**. This reframes Step
6's "lower the floor to 125 for 54%" as a genuine trade: more token capture ⇒ fewer inline lines ⇒
more subsequently-needed lines reachable only via a recovery route (native `Read`, re-search, or
`result://`) rather than sitting inline.

## 3. Graph ranking — negligible realized treatment intensity, no observed benefit

Correcting Step 6's inference with a direct test. The right frame is not "high-powered negative"
but **realized treatment intensity** — how much graph ranking *actually changed* the kept set:

- At budget 256, graph scoring was active on 7 future-touch-scored events and the budget left
  enough lines for reordering, yet graph **changed only 1 retained line out of ~74** and rescued
  **0** future-touch-associated paths; line-recall was identical (0.56 vs 0.56), mean rank-gain
  0.22 positions on lines both arms already kept. So the current graph ranker produced an ordering
  **nearly indistinguishable from simple order** on these traces.
- At budget 64 only 2–3 lines survive under *either* ordering, so there is nothing to reorder
  (`graph_reordered_any = 0`) — the comparison is uninformative there, not evidence against graph.
- On the **shipped** `(256,400)` config graph never engaged (0 active events) — consistent with the
  live pilot's `graph_ranked = 0`.

Across **every** config graph rescued **zero** needed lines (`promoted_needed = 0`). The defensible
statement is therefore: **the current graph ranker exhibited negligible realized treatment intensity
and no observed future-touch-line-retention benefit on this workload** — *not* a strongly-powered
refutation (7 active events with one line substitution is thin), and *not* "graph disproven". It is
enough to keep graph **deprioritized** for B1 search-output compaction; it says nothing about graph
value for symbol bundles, task-state reasoning, or cross-file retrieval in later phases.

**Scope note.** `compare_event` yields an outcome only for firing searches that have ≥1
subsequently-touched matched path, so the "graph active events", kept-line totals and "1/74"
reordering count are **within the future-touch-scored subset**, not across every reducible search in
all 16 traces.

## 4. Budget–floor Pareto frontier — picking the live arms (`corpus/pareto_frontier.py`)

Crossing token capture `R_paired(B,F)` with inline line-recall over the full B×F grid gives the
decision surface. **Floor 125 is on the frontier at every budget** — lowering the floor is nearly
free (it only controls how *often* reduction fires). The **budget** is where the evidence cost
lives. Three non-dominated settings:

| setting | R_paired (capture) | line-recall | note |
|---|---:|---:|---|
| **(256,125)** | 0.183 | **0.563** | **dominates the shipped (256,400) on BOTH axes** (more capture *and* more inline evidence) — a free improvement |
| **(128,125)** | 0.427 | **0.484** | the **knee**: 256→128 nearly doubles capture at almost no line-recall cost |
| **(64,125)** | 0.539 | 0.097 | max capture, but 128→64 collapses line-recall 0.48→0.10 for only +0.11 capture |

Everything at floor 244/400 is dominated. The knee is decisive: the marginal capture from dropping
the budget 128→64 (+0.11) costs ~0.38 of inline line-recall — a bad trade unless `result://`
expansions turn out to be nearly free live. **Recommended live arms:** `A` (native) vs
**`D1=(256,125)`** (the strictly-dominant "lower the floor, keep the budget" setting) vs
**`D2=(128,125)`** (the knee). `(64,125)` is held as an optional downside-bound, not a default.

## Revised verdict (supersedes Step 6 §2–§3)

- **Token compression is real and measured:** `R_paired(256,400)=12.1%`, `(64,125)=53.9%` (Step 6).
- **Not "lossless".** Aggressive settings keep the file *names* but drop ~90% of subsequently-needed
  match *lines* to the `result://` handle. The floor/budget choice trades token-capture against
  inline-evidence, and the right operating point depends on the **live cost of `result://`
  expansions** (extra turns, cache re-reads, retries) — which only the deferred replicated live
  experiment can price (measure `T_total`, turns-to-resolution, retry burden, task success).
- **Graph ranking:** no line-level benefit where the test has power; untestable at low budget;
  `promoted_needed = 0` throughout. Deprioritize on the evidence; do not claim it disproven.

## Caveats

- **`LineRetained` is a proxy.** It means ≥1 search-result line **from a file the agent touched
  within the next 8 events** survived. It does **not** establish that the retained line was the
  exact semantically-useful one, and it does **not** score grep evidence the agent consumed
  *directly* — read the matching line in place and never opened the file (Step 6's direct-use
  caveat carries forward). So LINE-recall is a floor-level indicator of inline evidence, not a
  usefulness measurement.
- **Small sample / thin treatment.** 16 sonnet sessions, django only, 5–31 needed paths per config;
  the graph arm engaged on only 7 scored events and moved a single line — negligible realized
  treatment intensity, not a strongly-powered negative.
- **Provenance is enforced, not assumed.** `load_task_graphs(verify=True)` refuses to rank against a
  graph whose provenance `base_commit` / stored DB hash do not match the task's independently
  manifest-recorded `base_commit`.
- A broader task/model mix, and a **live** measurement of whether the inline-evidence deficit turns
  into extra operations, remain necessary before fixing a default budget/floor or removing graph.
