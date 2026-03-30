#!/usr/bin/env python3
"""Generate LaTeX table for Commit Identifier Threshold experiment.

Reads stage_02 results, computes macro-averaged precision, recall, and F1
using evaluation_utils, and outputs a LaTeX table to
tables/table_commit_identifier_threshold.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_commit_identifier_threshold.tex"

INFINITY_THRESHOLD = 1000000

# Hardcoded result file names
HARDCODED_STAGE01 = "szz_agent_stage_01_20260202_111653.json"
HARDCODED_STAGE02_FILES = [
    "szz_agent_stage_02_20260202_194137.json",
    "szz_agent_stage_02_20260209_112058.json",
    "szz_agent_stage_02_20260209_131005.json",
    "szz_agent_stage_02_20260209_151746.json",
    "szz_agent_stage_02_20260209_202153.json",
]


def load_results(filepath: Path) -> tuple:
    """Load a results JSON file, return (metadata, evaluation_summary, num_results)."""
    with open(filepath) as f:
        data = json.load(f)

    summary = evaluate_results(data["results"])
    return data["metadata"], summary, len(data["results"])


def get_cost(metadata: dict) -> float:
    """Get cost from metadata, handling both 'cost' and 'costs' keys."""
    return metadata.get("cost", metadata.get("costs", 0.0))


def fmt(value: float) -> str:
    """Format a float to 2 decimal places."""
    return f"{value:.2f}"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-latest-results", action="store_true",
                        help="Use the latest results files by timestamp instead of hardcoded paths")
    return parser.parse_args()


def main():
    args = parse_args()

    # Load stage_01 for its cost
    if args.use_latest_results:
        stage_01_matches = sorted(RESULTS_DIR.glob("szz_agent_stage_01_*.json"))
        if not stage_01_matches:
            raise FileNotFoundError("No stage_01 files found")
        stage_01_path = stage_01_matches[-1]
    else:
        stage_01_path = RESULTS_DIR / HARDCODED_STAGE01
    with open(stage_01_path) as f:
        stage_01_data = json.load(f)
    stage_01_cost = get_cost(stage_01_data["metadata"])

    # Load all stage_02 files
    if args.use_latest_results:
        # Keep only the latest file per threshold
        latest_per_threshold: dict[int, Path] = {}
        for f in sorted(RESULTS_DIR.glob("szz_agent_stage_02_*.json")):
            with open(f) as fh:
                meta = json.load(fh).get("metadata", {})
            threshold = meta.get("candidate_selection_threshold")
            if threshold is not None:
                latest_per_threshold[threshold] = f
        stage_02_files = list(latest_per_threshold.values())
    else:
        stage_02_files = [RESULTS_DIR / fname for fname in HARDCODED_STAGE02_FILES]
    rows = []
    for fpath in stage_02_files:
        meta, summary, num_fix_commits = load_results(fpath)
        threshold = meta["candidate_selection_threshold"]
        stage_02_cost = get_cost(meta)
        total_cost = stage_01_cost + stage_02_cost
        cost_per_fix_commit = total_cost / num_fix_commits
        rows.append((
            threshold,
            summary["precision"],
            summary["recall"],
            summary["f1_score"],
            cost_per_fix_commit,
        ))

    # Sort by threshold ascending
    rows.sort(key=lambda r: r[0])

    # Determine best values
    best_p = max(r[1] for r in rows)
    best_r = max(r[2] for r in rows)
    best_f = max(r[3] for r in rows)
    best_cost = min(r[4] for r in rows)

    # Build LaTeX
    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Impact of the Commit Identifier threshold on SZZ-Agent performance. "
        r"Costs are average per fix commit. Best results in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:commit_identifier_threshold}")
    lines.append(r"\begin{tabular}{c ccc c}")
    lines.append(r"\toprule")
    lines.append(
        r"\makecell{\textbf{Commit} \\ \textbf{Identifier} \\ \textbf{Threshold}} "
        r"& \textbf{Precision} & \textbf{Recall} & \textbf{F1-Score} "
        r"& \textbf{Cost (USD)} \\"
    )
    lines.append(r"\midrule")

    for threshold, p, r, f, cost in rows:
        cells = []

        # Threshold display: subtract 1, use infinity for 1000000
        if threshold == INFINITY_THRESHOLD:
            cells.append(r"$\infty$")
        else:
            cells.append(str(threshold - 1))

        # Precision, Recall, F1
        for val, bval in [(p, best_p), (r, best_r), (f, best_f)]:
            s = fmt(val)
            if abs(val - bval) < 1e-9:
                s = r"\textbf{" + s + "}"
            cells.append(s)

        # Cost (lower is better)
        cost_s = f"\\${cost:.2f}"
        if abs(cost - best_cost) < 1e-9:
            cost_s = r"\textbf{" + cost_s + "}"
        cells.append(cost_s)

        row = " & ".join(cells) + r" \\"
        lines.append(row)

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")

    latex = "\n".join(lines) + "\n"

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(latex)
    print(f"Table written to {OUTPUT_FILE}")
    print()
    print(latex)


if __name__ == "__main__":
    main()
