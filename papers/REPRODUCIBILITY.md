# Reproducing the papers

The publication assets are derived from the repository snapshot `4656e8c10a6064b5c4a86b001b3dd8608ba32b3d`. The paper commit adds prose and build outputs but does not rewrite the frozen experimental inputs; the later privacy scrub (below) changed strings in those inputs, not values.

## Inputs and provenance

| Claim family | Frozen source | Evidence grade |
|---|---|---|
| Exploratory occupancy and cache lifecycle (D1) | **None committed.** No aggregate artifact exists for D1; its figures survive only as prose values and cannot be re-derived from the repository | Observational, unarchived; raw traces private |
| 50-task read opportunity | `corpus/analysis/opportunity-ceiling-v1.json` plus its embedded robustness output (all 50 runs). The 30-run token-share-eligible subset (20.9% / 48.5%) uses eligibility flags from gitignored per-run label reports | Retrospective opportunity ceiling, in raw read tokens |
| 60-session prefix decomposition | `corpus/analysis/prefix-decomposition-v2.json` (the Step-7 sessions: 20 native runs plus 40 search-reducer runs on four tasks) | Observed decomposition; thinking share estimated |
| B6 integrated runtime A/B | `corpus/analysis/b6-live-results.json` | Live, 24 sessions, preregistered; graded by a local test runner, not the official SWE-bench harness |
| B7 cache model and replay | `corpus/analysis/b7-cache-replay-b6.json` and `b7-cache-replay-interactive.json` | Append-only branch calibrated in-sample (exact on 11/12 B6 native sessions); edit-branch calibration not committed; savings replay is modeled |
| B8 v2 live run and post-hoc model check | `corpus/analysis/b8v2-live-results-N.json` and `b8v2-live-results-T.json` | Live, 18 task chunks across 6 chained sessions; the 29.5% prediction was first committed together with the results, so it is a post-hoc check |

The generator fails if required files or expected fields are absent. Its fixed random seed is `20260903`. It recomputes the opportunity run bootstrap with 100,000 samples and the B6 task-cluster bootstrap with 200,000 samples.

## What is not in the repository

- **Raw transcripts.** The session transcripts behind B6, B7, and B8, and behind the Step 5–7 studies (including the 60-session prefix decomposition), are not committed. The committed artifacts are their summaries. As a result, the B7 replay and the B8 v2 prediction cannot be re-run from committed data, the B6 native-arm sums cannot be re-derived (the treatment arm is cross-checked by the committed gateway logs), and the Step-6 paired replay has no committed output.
- **D1 aggregates.** No aggregate artifact for the exploratory archive is committed (see the table above).
- **Eligibility flags.** The 30-run token-share-eligible subset of the 50-task corpus depends on gitignored per-run label reports; the all-50 values and their bootstrap intervals are reproducible from the committed JSON.

## Pseudonymized paths

Committed artifacts were privacy-scrubbed on 2026-10-05; see [`docs/privacy-scrub-2026-10-05.md`](../docs/privacy-scrub-2026-10-05.md). Machine paths are pseudonymized as `/Users/<user>` (and `-Users-<user>-` in project slugs), interactive-session labels in the B7 replay are replaced by stable placeholders, and one company MCP server name is replaced. No measured value changed.

## One-command build

Requirements: Python 3.12+ for a byte-exact `generated/results_summary.json`, NumPy, Matplotlib, a TeX distribution with `latexmk`, and the standard LaTeX packages imported by `paperstyle.sty`. Older interpreters run the generator, but Python 3.9 and 3.11 produce `b8.pairs[1].treatment_cli` = `1.5595434` instead of the committed `1.5595433999999997`, a one-unit-in-the-last-place difference caused by the more precise float summation in `sum()` from Python 3.12 on. The TeX tables and every other JSON value match. Regenerated figure files can differ in bytes (PDF metadata, PNG compression) while rendering identically.

```bash
make -C papers all
```

The command performs these stages:

1. Run `scripts/generate_assets.py` against the committed analysis JSON.
2. Compile `measurement/main.tex` and `runtime/main.tex`, including BibTeX passes.
3. Copy stable PDFs to `papers/dist/`.

## Independent verification

```bash
python3 papers/scripts/generate_assets.py
python3 -m json.tool papers/generated/results_summary.json >/dev/null
latexmk -pdf -interaction=nonstopmode -halt-on-error -cd papers/measurement/main.tex
latexmk -pdf -interaction=nonstopmode -halt-on-error -cd papers/runtime/main.tex
pytest -q
git diff --check
```

For strict artifact comparison, build twice from a clean checkout and compare the generated JSON and TeX tables. PDF bytes may differ because TeX can embed build metadata; compare extracted text and rendered pages rather than requiring byte-identical PDFs.

## Headline arithmetic

The machine-readable output is [`generated/results_summary.json`](generated/results_summary.json). Key checks are:

```text
B6 pooled reduction = 1 - 8,699,786 / 14,866,113 = 41.5%
B8 modeled-list-price reduction = 1 - 1,542,873 / 2,183,665 = 29.3448%
B8 client-reported (CLI) cost reduction = 1 - 4.6381914 / 6.553372 = 29.224%
B8 prediction error = 29.3448% - 29.5% = -0.155 percentage points (post-hoc check)
```

The B8 v2 prediction and its band first appear in version control in commit `af822b7` (2026-09-01 12:47 IST), the same commit as the results; the v2 treatment sessions ran 11:33–12:41 IST that day according to the gateway logs, and the v1 protocol commit `623b612` (2026-08-31) has no v2 section. The agreement is therefore a post-hoc check of the calibrated model, not a preregistered test, and pair-level reductions (31.0%, 5.9%, 44.9%) are far wider than the 0.155-point agreement. B6, by contrast, is preregistered: protocol commit `7e4c7b1` precedes results commit `9f7522f`, and the post-run protocol diff touches only the status header.

The B6 95% interval resamples four task clusters, not 12 pairs as if repetitions were independent tasks. It is post-hoc uncertainty attached to a preregistered pooled endpoint. The quality result is reported as an operational gate (9/12 treatment versus 10/12 native), not as a statistically powered non-inferiority conclusion. B6 grading uses a local test runner that skips 6 of the 25 PASS_TO_PASS tests as unresolvable; the grader was repaired mid-run (test-file reset), and the 16485-N0 and 16485-T0 grades were repaired post hoc by replaying the transcripts' Edit/Write operations.

## Evidence boundaries

- The exploratory archive is a design-partner sample; raw transcripts are intentionally not public because they can contain code, prompts, and local paths, and its aggregates were never committed.
- The read-opportunity results (20.9% and 48.5% on the 30 token-share-eligible runs; 21.3% and 51.0% on all 50) are retrospective ceilings of raw read tokens, not token-turns and not achieved savings.
- The B7 giant-session cost reduction (61.5% on the calibration-filtered subset; 49.7% over all 54 sessions) is a replay result. Its median is zero, all of it comes from 8 interactive sessions (the other 28 well-calibrated sessions are short headless runs that the gated policy leaves unchanged), and the replay's gated rule differs from the shipped scheduler (whose break-even branch could not fire on the Anthropic profiles in the evaluated version; corrected 2026-10-06, not yet run live).
- B8 v1 is retained as a confounded experiment: the custom base URL disabled native MCP schema deferral. B8 v2 is the clean run; its saving comes from admission (`--disallowedTools`), since the gateway applied no mutation, and its tasks exclude the one B6 task where treatment failed.
- Only the Claude Code to Anthropic path has been run live. Other provider profiles are sensitivity analyses until adapters and calibration experiments exist.
- No claim should be generalized to all repositories, agents, or providers without replication.

## Updating a number safely

Do not edit generated tables or figures by hand. Add or freeze a new analysis artifact, update `scripts/generate_assets.py`, rebuild, and review the JSON diff first. Then update both manuscripts wherever the claim appears and preserve its evidence label.
