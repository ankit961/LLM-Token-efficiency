# Phase 1 — ContextReduce

> **Status: implementation complete; validation gate pending.** The reducer engine,
> retention, handles, and invariant tests are done. The real Exp-B gate — *reduced vs
> raw preserves task success at material savings* — needs a same-task agent trial and
> **folds into the Phase-2 A/B/C/D experiment** (arm B). Phase 1 is not "gate-passed."

PostToolUse output reduction — the cheapest safe win (design v1.2 §7). Replace a fat
tool result the model sees with a decision-relevant summary plus a handle to the full
raw payload, **without proxying the model request**. Ships on a plain subscription.

## Modules ([`contextruntime/reducers/`](../contextruntime/reducers/))

| Module | Role |
|---|---|
| `base.py` | `ReducedOutput`, the retention heuristic (`should_reduce`), handle helper |
| `library.py` | Reducers (`tests` · `grep` · `git` · `logs` · `generic`) + `classify` dispatch |
| `planner.py` | `scan_graph` — the **Experiment-B** measurement over an ingested graph; writes `REDUCES` edges |
| `hook.py` | The PostToolUse hook handler (version-gated, schema-perfect, fail-open) |

## The reducer contract

Each reducer **preserves** the decision-relevant invariants and **drops** verbosity:

| Reducer | Preserves | Drops |
|---|---|---|
| `tests` | FAILED lines, assertion diffs, tracebacks, the pass/fail tally | the passing run (dots / PASSED lines) |
| `grep` | first *K* matches + total + omitted count | the long tail |
| `logs` | ERROR/WARN lines + the tail | the INFO/DEBUG bulk |
| `git` | file + hunk headers, +/- stat | unchanged context lines |
| `generic` | head + tail | the middle |

Every reduction returns a `result://<hash>` **handle** to the full payload (resolved
against the CAS) and an `invariants_ok` flag — a reducer that would drop a must-keep
line (e.g. a `FAILED`) reports the failure instead of silently losing it.

## Retention (design C10)

`should_reduce` **never reduces source reads** (`source_slice`) or messages/rules:
a read may be an edit precondition, and reducing it forces an under-context re-read
next turn. Logs, tests, and large greps are reduced aggressively; anything below
`MIN_REDUCE_TOKENS` (400) is left alone (envelope overhead would dominate). Full
`exploration_read` vs `edit_precondition_read` detection lands in Phase 2.

## Honest posture

- **Version-gated.** Built-in-tool output replacement is a recent, client-dependent
  capability. The hook only replaces output when the ContextRuntime Doctor confirms it
  (or the operator asserts `CR_OUTPUT_REPLACEMENT=1`); otherwise it **no-ops loudly**.
- **Observe by default.** The hook reports the would-be saving on stderr and emits `{}`
  unless `CR_REDUCE_MODE=enforce`. `reduce-scan` is pure measurement (no live effect).
- **Schema-perfect.** A `dict` tool_response keeps its shape (only `stdout`/`content`
  is reduced, `exitCode` preserved); a malformed replacement would abort the turn.
- **Fail-open.** Any error, or bad stdin, prints `{}` and exits 0 — the agent never freezes.

## Run it

```bash
# measure what ContextReduce would save over an ingested graph (Experiment B)
python3 -m contextruntime.cli ingest ~/.claude/projects/*/*.jsonl --db graph.db
python3 -m contextruntime.cli reduce-scan --db graph.db

# the PostToolUse hook (observe unless enforced)
echo '{"tool_name":"Bash","tool_input":{"command":"pytest"},"tool_response":{"stdout":"...","exitCode":1}}' \
  | python3 -m contextruntime.reducers.hook
```

Wire in `settings.json` (Phase 2 hardens this — absolute path, tight deadline, `exit 0`):

```json
"PostToolUse": [{ "matcher": "Read|Grep|Bash",
  "hooks": [{ "type": "command", "command": "python3 -m contextruntime.reducers.hook" }] }]
```

## Measured (design-partner sample, Grade C, observe mode)

On a real session: **~53% reduction on reducible tool results** (tests reduced ~93%,
logs ~53%), 0 invariant failures, with source reads and messages correctly left native.
This is the Experiment-B dataset the Phase-1 ship gate is judged on.

## Next

**Phase 2 — SemanticFS + Graph-Lite:** the `CodeSymbol`/`DEPENDS_ON` graph, the
`exploration_read` vs `edit_precondition_read` split (so the retention heuristic and the
bypass denominator are exact), and bundle precision/recall by language (C3).
