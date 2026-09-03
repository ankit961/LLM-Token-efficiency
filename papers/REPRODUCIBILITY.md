# Reproducing the papers

The publication assets are derived from the repository snapshot `4656e8c10a6064b5c4a86b001b3dd8608ba32b3d`. The paper commit adds prose and build outputs but does not rewrite the frozen experimental inputs.

## Inputs and provenance

| Claim family | Frozen source | Evidence grade |
|---|---|---|
| Exploratory occupancy and cache lifecycle | Existing aggregate reports and findings under `docs/` and `corpus/analysis/` | Observational, private raw traces; aggregates public |
| 50-task read opportunity | `corpus/analysis/opportunity-ceiling-v1.json` plus its embedded robustness output | Retrospective opportunity ceiling |
| 60-session prefix decomposition | `corpus/analysis/prefix-decomposition-v2.json` | Observed decomposition |
| B6 integrated runtime A/B | `corpus/analysis/b6-live-results.json` | Live, 24 sessions, objective grading |
| B7 cache model and replay | `corpus/analysis/b7-cache-replay-interactive.json` and neighboring B7 calibration artifacts | Live-calibrated model; savings replay is modeled |
| B8 clean prediction test | `corpus/analysis/b8v2-live-results-N.json` and `b8v2-live-results-T.json` | Live, 18 task chunks across 6 chained sessions |

The generator fails if required files or expected fields are absent. Its fixed random seed is `20260903`. It recomputes the opportunity run bootstrap with 100,000 samples and the B6 task-cluster bootstrap with 200,000 samples.

## One-command build

Requirements: Python 3.11+, NumPy, Matplotlib, a TeX distribution with `latexmk`, and the standard LaTeX packages imported by `paperstyle.sty`.

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
B8 CLI-billed reduction = 1 - 4.6381914 / 6.553372 = 29.224%
B8 prediction error = 29.3448% - 29.5% = -0.155 percentage points
```

The B6 95% interval resamples four task clusters, not 12 pairs as if repetitions were independent tasks. It is post-hoc uncertainty attached to a preregistered pooled endpoint. The quality result is reported as an operational gate (9/12 treatment versus 10/12 native), not as a statistically powered non-inferiority conclusion.

## Evidence boundaries

- The exploratory archive is a design-partner sample; raw transcripts are intentionally not public because they can contain code, prompts, and local paths.
- The 21.3% and 51.0% read-opportunity results are retrospective ceilings, not achieved savings.
- The B7 approximately 60% giant-session cost reduction is a replay result. Its median is zero and its value is concentrated in a small tail.
- B8 v1 is retained as a confounded experiment: the custom base URL disabled native MCP schema deferral. B8 v2 is the clean test.
- Only the Claude Code to Anthropic path is live-validated. Other provider profiles are sensitivity analyses until adapters and calibration experiments exist.
- No claim should be generalized to all repositories, agents, or providers without replication.

## Updating a number safely

Do not edit generated tables or figures by hand. Add or freeze a new analysis artifact, update `scripts/generate_assets.py`, rebuild, and review the JSON diff first. Then update both manuscripts wherever the claim appears and preserve its evidence label.
