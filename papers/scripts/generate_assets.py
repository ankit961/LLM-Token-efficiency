#!/usr/bin/env python3
"""Generate publication figures, tables, and a machine-readable result summary.

All headline values are recomputed from frozen artifacts already committed under
``corpus/analysis``.  The only hard-coded numbers are labels and the frozen B8
prediction recorded in ``docs/b8-protocol.md``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "corpus" / "analysis"
PAPERS = ROOT / "papers"
FIGURES = PAPERS / "figures"
GENERATED = PAPERS / "generated"
SEED = 20260903

BLUE = "#176B87"
TEAL = "#2A9D8F"
GOLD = "#E9C46A"
ORANGE = "#F4A261"
RED = "#E76F51"
INK = "#23313D"
MUTED = "#6B7785"
GRID = "#D9E1E8"


def load(name: str):
    return json.loads((ANALYSIS / name).read_text())


def pct(x: float) -> str:
    return f"{100 * x:.1f}"


def bootstrap_opportunity(runs: list[dict], n_boot: int = 100_000):
    rng = random.Random(SEED)
    safe, upper = [], []
    for _ in range(n_boot):
        sample = [rng.choice(runs) for _ in runs]
        denominator = sum(sum(row["tokens"].values()) for row in sample)
        safe.append(
            sum(row["tokens"]["exploration_reducible"] for row in sample)
            / denominator
        )
        upper.append(
            sum(
                row["tokens"]["exploration_reducible"]
                + row["tokens"]["search_listing_reducible"]
                for row in sample
            )
            / denominator
        )
    safe.sort()
    upper.sort()
    lo = int(0.025 * n_boot)
    hi = int(0.975 * n_boot) - 1
    return [safe[lo], safe[hi]], [upper[lo], upper[hi]]


def b6_summary(data: dict):
    tasks = data["tasks"]
    rows = []
    for task, runs in tasks.items():
        for rep in range(3):
            native = runs[f"N{rep}"]
            treatment = runs[f"T{rep}"]
            rows.append(
                {
                    "task": task,
                    "rep": rep,
                    "native_input": native["metrics"]["sum_input"],
                    "treatment_input": treatment["metrics"]["sum_input"],
                    "native_calls": native["metrics"]["calls"],
                    "treatment_calls": treatment["metrics"]["calls"],
                    "native_cache_read": native["metrics"]["cache_read"],
                    "treatment_cache_read": treatment["metrics"]["cache_read"],
                    "native_cache_creation": native["metrics"]["cache_creation"],
                    "treatment_cache_creation": treatment["metrics"]["cache_creation"],
                    "native_output": native["metrics"]["output"],
                    "treatment_output": treatment["metrics"]["output"],
                    "native_success": bool(native["grade"]["success"]),
                    "treatment_success": bool(treatment["grade"]["success"]),
                    "retired": treatment.get("gateway", {}).get(
                        "tool_results_retired", 0
                    ),
                    "thinking_stripped": treatment.get("gateway", {}).get(
                        "thinking_blocks_stripped", 0
                    ),
                    "fallback": treatment.get("gateway", {}).get(
                        "fallback_original", 0
                    ),
                }
            )

    per_task = []
    for task in tasks:
        selected = [row for row in rows if row["task"] == task]
        native = sum(row["native_input"] for row in selected)
        treatment = sum(row["treatment_input"] for row in selected)
        per_task.append(
            {
                "task": task,
                "native_input": native,
                "treatment_input": treatment,
                "ratio": treatment / native,
                "reduction": 1 - treatment / native,
                "native_success": sum(row["native_success"] for row in selected),
                "treatment_success": sum(
                    row["treatment_success"] for row in selected
                ),
            }
        )

    native_input = sum(row["native_input"] for row in rows)
    treatment_input = sum(row["treatment_input"] for row in rows)

    # Cluster bootstrap: the four tasks are the independent resampling units.
    rng = random.Random(SEED)
    task_names = list(tasks)
    boot = []
    for _ in range(200_000):
        native = treatment = 0
        for task in [rng.choice(task_names) for _ in task_names]:
            for rep in range(3):
                native += tasks[task][f"N{rep}"]["metrics"]["sum_input"]
                treatment += tasks[task][f"T{rep}"]["metrics"]["sum_input"]
        boot.append(1 - treatment / native)
    boot.sort()
    ci = [boot[5_000], boot[194_999]]

    return {
        "rows": rows,
        "per_task": per_task,
        "native_input": native_input,
        "treatment_input": treatment_input,
        "ratio": treatment_input / native_input,
        "reduction": 1 - treatment_input / native_input,
        "task_cluster_bootstrap_95": ci,
        "pair_median_reduction": median(
            1 - row["treatment_input"] / row["native_input"] for row in rows
        ),
        "pairs_reduced": sum(
            row["treatment_input"] < row["native_input"] for row in rows
        ),
        "native_success": sum(row["native_success"] for row in rows),
        "treatment_success": sum(row["treatment_success"] for row in rows),
        "native_calls": sum(row["native_calls"] for row in rows),
        "treatment_calls": sum(row["treatment_calls"] for row in rows),
        "native_cache_read": sum(row["native_cache_read"] for row in rows),
        "treatment_cache_read": sum(row["treatment_cache_read"] for row in rows),
        "native_cache_creation": sum(
            row["native_cache_creation"] for row in rows
        ),
        "treatment_cache_creation": sum(
            row["treatment_cache_creation"] for row in rows
        ),
        "native_output": sum(row["native_output"] for row in rows),
        "treatment_output": sum(row["treatment_output"] for row in rows),
        "retired": sum(row["retired"] for row in rows),
        "thinking_stripped": sum(row["thinking_stripped"] for row in rows),
        "fallback": sum(row["fallback"] for row in rows),
    }


def b8_summary(native_data: dict, treatment_data: dict):
    pairs = []
    for rep in range(3):
        native = native_data["pairs"][f"N{rep}"]
        treatment = treatment_data["pairs"][f"T{rep}"]
        native_cli = sum(native[f"chunk{i}"]["cost_usd"] for i in range(3))
        treatment_cli = sum(
            treatment[f"chunk{i}"]["cost_usd"] for i in range(3)
        )
        pairs.append(
            {
                "pair": rep,
                "native_bite": native["bite"]["bite"],
                "treatment_bite": treatment["bite"]["bite"],
                "bite_reduction": 1
                - treatment["bite"]["bite"] / native["bite"]["bite"],
                "native_input": native["metrics"]["sum_input"],
                "treatment_input": treatment["metrics"]["sum_input"],
                "residency_reduction": 1
                - treatment["metrics"]["sum_input"]
                / native["metrics"]["sum_input"],
                "native_cli": native_cli,
                "treatment_cli": treatment_cli,
                "gateway": treatment["gateway"],
                "native_success": sum(
                    native[f"chunk{i}"]["grade"]["success"] for i in range(3)
                ),
                "treatment_success": sum(
                    treatment[f"chunk{i}"]["grade"]["success"]
                    for i in range(3)
                ),
            }
        )
    n_bite = sum(row["native_bite"] for row in pairs)
    t_bite = sum(row["treatment_bite"] for row in pairs)
    n_input = sum(row["native_input"] for row in pairs)
    t_input = sum(row["treatment_input"] for row in pairs)
    n_cli = sum(row["native_cli"] for row in pairs)
    t_cli = sum(row["treatment_cli"] for row in pairs)
    return {
        "pairs": pairs,
        "native_bite": n_bite,
        "treatment_bite": t_bite,
        "bite_reduction": 1 - t_bite / n_bite,
        "predicted_bite_reduction": 0.295,
        "prediction_error_percentage_points": 100
        * ((1 - t_bite / n_bite) - 0.295),
        "native_input": n_input,
        "treatment_input": t_input,
        "residency_reduction": 1 - t_input / n_input,
        "native_cli": n_cli,
        "treatment_cli": t_cli,
        "cli_reduction": 1 - t_cli / n_cli,
        "native_success": sum(row["native_success"] for row in pairs),
        "treatment_success": sum(row["treatment_success"] for row in pairs),
        "fires": sum(row["gateway"]["fires"] for row in pairs),
        "retired": sum(row["gateway"]["retired"] for row in pairs),
        "thinking_stripped": sum(
            row["gateway"]["thinking_stripped"] for row in pairs
        ),
        "fallback": sum(row["gateway"]["fallback_original"] for row in pairs),
    }


def write_tables(b6: dict, b8: dict):
    task_lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Task & Native input & Treatment input & Reduction & Success N/T \\",
        r"\midrule",
    ]
    for row in b6["per_task"]:
        label = row["task"].split("-")[-1]
        task_lines.append(
            f"{label} & {row['native_input']:,} & {row['treatment_input']:,} & "
            f"{pct(row['reduction'])}\\% & {row['native_success']}/"
            f"{row['treatment_success']} \\\\"
        )
    task_lines.extend(
        [
            r"\midrule",
            f"Pooled & {b6['native_input']:,} & {b6['treatment_input']:,} & "
            f"{pct(b6['reduction'])}\\% & {b6['native_success']}/"
            f"{b6['treatment_success']} " + r"\\",
            r"\bottomrule",
            r"\end{tabular}",
        ]
    )
    (GENERATED / "b6_task_table.tex").write_text("\n".join(task_lines) + "\n")

    pair_lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Pair & Native BITE & Treatment BITE & Dollar reduction & Residency reduction \\",
        r"\midrule",
    ]
    for row in b8["pairs"]:
        pair_lines.append(
            f"{row['pair']} & {row['native_bite']:,.0f} & "
            f"{row['treatment_bite']:,.0f} & {pct(row['bite_reduction'])}\\% & "
            f"{pct(row['residency_reduction'])}\\% \\\\"
        )
    pair_lines.extend(
        [
            r"\midrule",
            f"Pooled & {b8['native_bite']:,.0f} & {b8['treatment_bite']:,.0f} & "
            f"{pct(b8['bite_reduction'])}\\% & "
            f"{pct(b8['residency_reduction'])}\\% " + r"\\",
            r"\bottomrule",
            r"\end{tabular}",
        ]
    )
    (GENERATED / "b8_pair_table.tex").write_text("\n".join(pair_lines) + "\n")


def measurement_figure(prefix: dict, opportunity: dict, opportunity_ci: list):
    shares = prefix["share_of_sum_P"]
    labels = [
        "Fixed prefix",
        "Retained thinking",
        "Read results",
        "Bash results",
        "Tool-use inputs",
        "Other",
    ]
    values = [
        shares["fixed(system+tools+injected)"],
        shares["thinking(est,invisible)"],
        shares["result_read"],
        shares["result_bash"],
        shares["tool_use_edit"]
        + shares["tool_use_bash"]
        + shares["tool_use_other"]
        + shares["tool_use_write"],
    ]
    values.append(max(0, 100 - sum(values)))

    strata = list(opportunity["by_stratum"])
    pretty = ["1-line", "small", "medium", "large", "multi-file", "overall"]
    safe = [opportunity["by_stratum"][key]["c_safe"] for key in strata]
    upper = [opportunity["by_stratum"][key]["c_upper"] for key in strata]
    safe.append(opportunity["c_safe"]["ratio"])
    upper.append(opportunity["c_upper"]["ratio"])

    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.8), constrained_layout=True)
    ax = axes[0]
    colors = [BLUE, TEAL, GOLD, ORANGE, RED, MUTED]
    left = 0
    for label, value, color in zip(labels, values, colors):
        ax.barh([0], [value], left=left, color=color, height=0.46, label=label)
        if value >= 6:
            ax.text(
                left + value / 2,
                0,
                f"{value:.1f}%",
                ha="center",
                va="center",
                color="white" if color not in [GOLD, ORANGE] else INK,
                fontsize=7.2,
                fontweight="bold",
            )
        left += value
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.set_xlabel("Share of resident input token-turns (%)")
    ax.set_title("(a) What compounds across turns", loc="left", fontweight="bold")
    ax.legend(
        ncol=2,
        frameon=False,
        fontsize=6.4,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.31),
    )
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)

    ax = axes[1]
    y = np.arange(len(pretty))
    ax.barh(y + 0.17, 100 * np.array(upper), height=0.32, color=GOLD, label="Broad ceiling")
    ax.barh(y - 0.17, 100 * np.array(safe), height=0.32, color=BLUE, label="Conservative")
    # Run-bootstrap uncertainty is available only for the overall micro estimate.
    ax.errorbar(
        100 * safe[-1],
        y[-1] - 0.17,
        xerr=np.array(
            [
                [100 * (safe[-1] - opportunity_ci[0][0])],
                [100 * (opportunity_ci[0][1] - safe[-1])],
            ]
        ),
        fmt="none",
        color=INK,
        capsize=2,
        linewidth=0.8,
    )
    ax.errorbar(
        100 * upper[-1],
        y[-1] + 0.17,
        xerr=np.array(
            [
                [100 * (upper[-1] - opportunity_ci[1][0])],
                [100 * (opportunity_ci[1][1] - upper[-1])],
            ]
        ),
        fmt="none",
        color=INK,
        capsize=2,
        linewidth=0.8,
    )
    ax.set_yticks(y, pretty)
    ax.invert_yaxis()
    ax.set_xlim(0, 70)
    ax.set_xlabel("Candidate share of measured read tokens (%)")
    ax.set_title("(b) Retrospective opportunity", loc="left", fontweight="bold")
    ax.legend(frameon=False, fontsize=6.8, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for a in axes:
        a.tick_params(labelsize=7.2)
        a.xaxis.label.set_size(7.4)
        a.title.set_size(8.4)
    for suffix in ("pdf", "png"):
        fig.savefig(FIGURES / f"measurement_overview.{suffix}", dpi=240)
    plt.close(fig)


def runtime_figure(b6: dict, b7: dict, b8: dict):
    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.65), constrained_layout=True)

    ax = axes[0]
    labels = [row["task"].split("-")[-1] for row in b6["per_task"]] + ["pooled"]
    vals = [100 * row["reduction"] for row in b6["per_task"]] + [100 * b6["reduction"]]
    colors = [TEAL] * 4 + [BLUE]
    bars = ax.barh(np.arange(5), vals, color=colors, height=0.62)
    ax.invert_yaxis()
    ax.set_yticks(np.arange(5), labels)
    ax.axvline(0, color=INK, linewidth=0.7)
    ax.set_xlim(0, 70)
    ax.set_xlabel("Input reduction (%)")
    ax.set_title("(a) B6 live, 3 pairs/task", loc="left", fontweight="bold")
    for bar, value in zip(bars, vals):
        ax.text(value + 1.1, bar.get_y() + bar.get_height() / 2, f"{value:.1f}", va="center", fontsize=6.8)
    ci = b6["task_cluster_bootstrap_95"]
    ax.errorbar(
        vals[-1],
        4,
        xerr=np.array([[vals[-1] - 100 * ci[0]], [100 * ci[1] - vals[-1]]]),
        fmt="none",
        color=INK,
        capsize=2,
        linewidth=0.9,
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)

    ax = axes[1]
    pair_vals = [100 * row["bite_reduction"] for row in b8["pairs"]] + [100 * b8["bite_reduction"]]
    labels = ["pair 0", "pair 1", "pair 2", "pooled"]
    bars = ax.barh(np.arange(4), pair_vals, color=[TEAL, TEAL, TEAL, BLUE], height=0.62)
    ax.invert_yaxis()
    ax.set_yticks(np.arange(4), labels)
    ax.axvline(29.5, color=RED, linestyle="--", linewidth=1, label="frozen prediction")
    ax.set_xlim(0, 55)
    ax.set_xlabel("List-price dollar reduction (%)")
    ax.set_title("(b) B8 live, chained tasks", loc="left", fontweight="bold")
    for bar, value in zip(bars, pair_vals):
        ax.text(value + 0.8, bar.get_y() + bar.get_height() / 2, f"{value:.1f}", va="center", fontsize=6.8)
    ax.legend(frameon=False, fontsize=6.2, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)

    ax = axes[2]
    policies = ["unaligned", "cold gap", "gated", "oracle"]
    keys = ["unaligned", "cold_gap", "gated", "oracle"]
    block = b7["aggregate"]["policies_well_calibrated"]
    pooled = [-block[key]["usd_delta_pct"] for key in keys]
    med = [-block[key]["usd_delta_median_pct"] for key in keys]
    x = np.arange(4)
    ax.bar(x - 0.18, pooled, width=0.36, color=BLUE, label="pooled")
    ax.bar(x + 0.18, med, width=0.36, color=GOLD, label="median session")
    ax.axhline(0, color=INK, linewidth=0.7)
    ax.set_ylim(-8, 70)
    ax.set_xticks(x, policies, rotation=24, ha="right")
    ax.set_ylabel("Modeled dollar reduction (%)")
    ax.set_title("(c) B7 replay, calibrated n=36", loc="left", fontweight="bold")
    ax.legend(frameon=False, fontsize=6.4, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)

    for a in axes:
        a.tick_params(labelsize=7)
        a.xaxis.label.set_size(7.2)
        a.yaxis.label.set_size(7.2)
        a.title.set_size(8.2)
    for suffix in ("pdf", "png"):
        fig.savefig(FIGURES / f"runtime_results.{suffix}", dpi=240)
    plt.close(fig)


def main():
    FIGURES.mkdir(parents=True, exist_ok=True)
    GENERATED.mkdir(parents=True, exist_ok=True)

    prefix = load("prefix-decomposition-v2.json")
    opportunity = load("opportunity-ceiling-v1.json")
    opportunity_ci = bootstrap_opportunity(opportunity["per_run"])
    b6 = b6_summary(load("b6-live-results.json"))
    b7 = load("b7-cache-replay-interactive.json")
    b8 = b8_summary(
        load("b8v2-live-results-N.json"), load("b8v2-live-results-T.json")
    )

    summary = {
        "source_commit_expected": "4656e8c10a6064b5c4a86b001b3dd8608ba32b3d",
        "seed": SEED,
        "prefix_decomposition": prefix,
        "opportunity": {
            "n_runs": opportunity["n_runs"],
            "measured_tokens": opportunity["total_fully_measured_tokens"],
            "safe": opportunity["c_safe"],
            "upper": opportunity["c_upper"],
            "safe_run_bootstrap_95": opportunity_ci[0],
            "upper_run_bootstrap_95": opportunity_ci[1],
            "robustness": opportunity["robustness"],
        },
        "b6": b6,
        "b7": b7["aggregate"],
        "b8": b8,
    }
    (GENERATED / "results_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    write_tables(b6, b8)
    measurement_figure(prefix, opportunity, opportunity_ci)
    runtime_figure(b6, b7, b8)


if __name__ == "__main__":
    main()
