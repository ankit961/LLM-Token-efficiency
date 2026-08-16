# Semantic Admission Experiment v1 — PREREGISTRATION (paired A/B)

**Status: preregistered, FROZEN before Arm B collection begins.** This document is written before any
Arm B run so the eventual quality/token comparison is defensible — task set, order, model, budget,
outcome analysis, and token accounting are all fixed here, in advance, not chosen after seeing results.

This is a **different artifact** from the observation corpus. It does not touch, rerun, or reopen it.

## 0. Relationship to Observation Corpus v2.1 — untouched, preserved

The 50-run collection at `obs-runtime-3a-v2.1` (hook_schema 0.4.1, `corpus-plan-v2.1`, runtime SHA
`59d1904204e2afbdc8a05941a27d7ef690f8827e`) is renamed for reference purposes to **Observation Corpus
v2.1**. It is **preserved exactly as collected** — 50/50 runs, 49 engaged, 3 non-canonical-admissible
(retained, not deleted), rich read/edit/token-attribution signal. It answered *"can we trust what we
observe about context behavior?"*. It is **not modified, not rerun, and not re-admitted** by anything
in this document. Its raw journals and label-reports remain the authority for that question.

**Semantic Admission Experiment v1** is a new, separate artifact that reuses the *same 50 task
identities* (same specs, same order) for a different question — *"does substituting semantic context
for native reads preserve task success while reducing tokens?"* — and adds a NEW measurement Arm A
lacks: objective SWE-bench task-success grading, which the observation corpus never computed
(evaluation was `eval_deferred` there by design).

## 1. Research question

> Does ContextRuntime save substantial context without making Claude worse?

Operationalized as a paired comparison of task success and retry-inclusive token cost between native
Claude and Claude steered toward SemanticFS, on the same 50 tasks.

## 2. Arms

| arm | code | what changes vs. native |
|---|---|---|
| **A — native** | `arm="native"` | baseline. No MCP, no steering. **Not rerun** — reuses the 50 patches already produced under Observation Corpus v2.1 (verified: the `native` argv path in `ClaudeBackend` is unchanged from what actually ran — same flags, same prompt construction). |
| **B — semantic_directive** | `arm="semantic_directive"` | SemanticFS MCP enabled (`--mcp-config`) + a directive steering brief (`--append-system-prompt`, "call `read_symbol` first"). **Native tools remain fully available** — a resolver miss or agent choice to ignore the brief falls back to native Read/Bash exactly as in Arm A. Fail-open, not a gate. |
| **C — semantic_enforced** | `arm="semantic_enforced"` | **RESERVED. Not run.** `ClaudeBackend.run()` raises `NotImplementedError` unconditionally — it is not possible to accidentally execute this arm. Gate to unlock: Arm B's success and token results have been reviewed (§8). |

Sequence is **A → B → failure analysis → C**, never skipped. If B shows a large token reduction with a
material success loss, the next work is bundle completeness / expansion policy — not enforcement.

## 3. What must stay IDENTICAL between A and B (the pairing depends on this)

- **Task set and order** — the same locked `corpus/specs/run-01.md .. run-50.md` (`corpus-plan-v2.1`,
  `plan_sha256` unchanged), same `run_order`, same `base_commit_sha` per task, fresh isolated worktree
  per run (`TaskSetup`, unchanged code path for both arms).
- **Model** — `sonnet`, same as Arm A's collection.
- **Budget** — `walltime_limit_s = 900`, same as Arm A's collection (`run_all.sh`'s `WALLTIME=900`).
- **Task prompt** — the literal `-p` argument text. Verified byte-identical between arms for the same
  spec by `tests/test_corpusrunner_arms.py::test_semantic_directive_task_prompt_identical_to_native`.
  Arm B only *adds* `--mcp-config` / `--append-system-prompt`; it never edits the task prompt itself.
- **Observation instrumentation** — the frozen `obs-runtime-3a-v2.1` / hook_schema 0.4.1 cr-hook,
  unchanged. The arm changes what the *agent* can call; it never changes what or how ContextRuntime
  observes. Both arms' journals are directly comparable on that basis.
- **Grading method** — whatever official SWE-bench harness configuration grades Arm A's patches
  (§6) must grade Arm B's patches identically (same harness version, same `--dataset_name`, same
  `--namespace`/image source, same `FAIL_TO_PASS`/`PASS_TO_PASS` definitions per instance).

What is **allowed** to differ for B: MCP config presence, the one additional system-prompt brief, and
everything downstream of the agent's own tool choices (native reads, semantic reads, tokens, retries).

## 4. Prerequisite for Arm B — index the TARGET repo, not this repo

SemanticFS must be indexed against the **django mirror** the corpus tasks run against (the same
mirror `TaskSetup` checks out worktrees from), producing a `codegraph.db` keyed to `repo_id=django/
django`. This is a **different** code-graph than the one this project uses on itself — Arm B's
`--codegraph-db`/`--repo-id` must point at the django index, never at ContextRuntime's own.

```bash
contextruntime index-code <path-to-django-mirror> --db <arm-b-codegraph.db> --repo django/django
```

## 5. Paired outcome analysis (preregistered before B runs)

Do **not** compare `S_A = 60%` vs `S_B = 62%` as two independent samples — same 50 tasks, so pair them:

|              | B resolved | B unresolved |
|---|---|---|
| **A resolved**   | n₁₁ | n₁₀ |
| **A unresolved** | n₀₁ | n₀₀ |

- Primary quality statistic: the **discordant pairs** n₁₀ (A passes, B fails — a semantic-context
  regression) and n₀₁ (A fails, B passes). Report both counts explicitly, not just their difference.
- Significance: exact binomial / McNemar's test on (n₁₀, n₀₁). **State the power caveat explicitly** —
  n=50 has limited power to detect small quality shifts; a null result does not prove equivalence, and
  a positive result should be read as suggestive, not confirmatory, at this sample size.
- Report raw `S_A = resolved_A / 50` and `S_B = resolved_B / 50` alongside the paired statistic, never
  instead of it.
- `grader_error` outcomes are reported as a separate category, not folded into "unresolved" — a grading
  infrastructure failure is not a task failure.
- **Stratify by the 5 frozen fix-shape strata** (`fs1_oneline_1f_le3` … `fs5_multi_ge2f` from
  `corpus-plan-v2.1`) — both the success contingency and the token delta, per stratum. This is the
  view most likely to show *where* semantic context works (e.g. single-file) vs. struggles (e.g.
  multi-file).

## 6. Grading (Arm A first, reused for Arm B)

The 50 Arm-A patches are graded via the official SWE-bench harness (linux/amd64, GitHub Actions —
see the grading pipeline built alongside this document) against `princeton-nlp/SWE-bench_Verified`,
producing per task: `instance_id`, `FAIL_TO_PASS` pass/fail, `PASS_TO_PASS` pass/fail, `resolved`,
`grader_error`. Only public patch content and `task_id` are sent to the grading environment — no
HookJournal databases, prompts, local paths, or other telemetry.

## 7. Token accounting — retry-inclusive, mechanically-observed-first

```
T_net = T_input + T_cache_read + T_cache_write + T_output + T_recovery
```

- Use the **strongest mechanically observed** denominator for each component. Today that means:
  native reads → `model_visible_tokens` from the frozen HookJournal; semantic reads →
  `transport_content_tokens` from `semantic_reads` telemetry (includes `expansion_tokens`); retries →
  recovery-turn reads/edits after a failed attempt, counted the same way, not estimated separately.
- Any component **not** mechanically observed for a given run (e.g. provider-level cache-read/write
  token counts, if unavailable from headless `claude -p` text output) must be clearly labeled
  **ESTIMATE** in the report and never silently blended into a "measured" figure.
- Per-run fields to capture (this is exactly what `policydash.Dashboard` already computes, extended
  with grading + retries):

```
native file reads              semantic reads_used
semantic materialized tokens   expansion tokens
native fallbacks after         recovery turns
  a semantic opportunity       test executions
wall time                      final task success (from §6)
```

## 8. The result this experiment is aiming to establish

Given `S_A`, `S_B`, and `T_A`, `T_B` (context materialized, retry-inclusive):

- **Compelling**: B shows 30–45%+ less context materialized with a paired quality difference that is
  small and not clearly one-directional (n₁₀ ≈ n₀₁, or both small).
- **Also informative, differently actioned**: B shows large token savings alongside a material,
  one-directional success loss (n₁₀ ≫ n₀₁). This does **not** motivate enforcement (Arm C) — it
  motivates bundle-completeness and expansion-policy work, revisited via failure analysis on the n₁₀
  cases specifically (what did the agent need that the semantic bundle didn't have?).
- Arm C is **earned**, not scheduled — only after this review, explicitly, by the user.

## 9. Non-goals right now

- No Arm C run, under any framing.
- No enforced/blocking-admission claim.
- No cross-agent (Codex) claim — this experiment is Claude-only.
- No claim beyond these 50 tasks — this is a paired study on a fixed sample, not a population estimate.

## 10. Freeze condition

This protocol is frozen once committed. Any change to arm definitions, task set, model, budget, or
grading method **after** any Arm B result has been seen invalidates the paired design. Such a change
requires a new versioned document (`semantic-admission-experiment-v2.md`), never a silent edit here.

## 11. Daily-drive telemetry — excluded from this result

Interactive daily-driving of the `semantic_directive`-style advisory brief (`cr-policy` installed via
`contextruntime install claude`) is encouraged in parallel and is valuable for finding resolver gaps,
bad bundle boundaries, expansion loops, latency issues, and language-specific failures — but that data
is exploratory/product telemetry. It is never mixed into the confirmatory 50-task paired result above.
