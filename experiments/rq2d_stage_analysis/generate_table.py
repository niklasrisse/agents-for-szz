#!/usr/bin/env python3
"""Generate LaTeX table comparing Stage 1 Only, Stage 2 Only, and SZZ-Agent.

Reads results, computes macro-averaged precision, recall, and F1 using
evaluation_utils, and outputs a LaTeX table to
tables/table_stage_01_vs_stage_02.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_stage_01_vs_stage_02.tex"

# Files
STAGE_01_FILE = RESULTS_DIR / "szz_agent_stage_01_20260202_111653.json"
STAGE_02_ONLY_FILE = RESULTS_DIR / "szz_agent_stage_02_20260208_202211.json"
SZZ_AGENT_FILE = RESULTS_DIR / "szz_agent_stage_02_20260202_194137.json"


def load_stage_01(filepath: Path) -> tuple:
    """Load stage_01 results and convert to evaluate_results format."""
    with open(filepath) as f:
        data = json.load(f)

    results = data["results"]
    num_fix_commits = len(results)
    costs = data["metadata"].get("costs")
    converted = []
    for entry in results:
        gt = entry.get("bug_commit_hash", [])
        selected = entry.get("llm_selected_commit")
        predicted = [selected] if selected else []
        converted.append({
            "ground_truth_bics": gt,
            "predicted_bics": predicted,
        })

    return evaluate_results(converted), num_fix_commits, costs


def load_stage_02(filepath: Path) -> tuple:
    """Load stage_02 results and compute metrics."""
    with open(filepath) as f:
        data = json.load(f)

    results = data["results"]
    costs = data["metadata"].get("costs")
    return evaluate_results(results), costs


def fmt(value: float) -> str:
    """Format a float to 2 decimal places."""
    return f"{value:.2f}"


def find_latest_file(directory: Path, prefix: str) -> Path:
    """Find the latest JSON file matching a prefix by timestamp in filename."""
    matches = sorted(directory.glob(f"{prefix}*.json"))
    if not matches:
        raise FileNotFoundError(f"No files matching '{prefix}*' in {directory}")
    return matches[-1]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-latest-results", action="store_true",
                        help="Use the latest results files by timestamp instead of hardcoded paths")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.use_latest_results:
        stage_01_file = find_latest_file(RESULTS_DIR, "szz_agent_stage_01")
        # Classify stage_02 files by whether they were run with stage 1 input or directly on the dataset
        # "stage-2-only" has dataset_file pointing to sampled_datasets/ (no stage 1 input)
        # "combined" has dataset_file pointing to a stage_01 results file
        szz_agent_file = None
        stage_02_only_file = None
        for f in sorted(RESULTS_DIR.glob("szz_agent_stage_02_*.json")):
            with open(f) as fh:
                meta = json.load(fh).get("metadata", {})
            dataset_file = meta.get("dataset_file", "")
            if "szz_agent_stage_01" in dataset_file:
                szz_agent_file = f  # combined (latest wins)
            else:
                stage_02_only_file = f  # stage-2-only (latest wins)
        if szz_agent_file is None or stage_02_only_file is None:
            raise FileNotFoundError("Expected both a combined and stage-2-only stage_02 file")
    else:
        stage_01_file = STAGE_01_FILE
        stage_02_only_file = STAGE_02_ONLY_FILE
        szz_agent_file = SZZ_AGENT_FILE

    stage_01_summary, num_fix_commits, stage_01_costs = load_stage_01(stage_01_file)
    stage_02_only_summary, stage_02_only_costs = load_stage_02(stage_02_only_file)
    szz_agent_summary, szz_agent_stage_02_costs = load_stage_02(szz_agent_file)

    # Rows: (name, precision, recall, f1, cost_per_fix_commit, is_ours)
    has_costs = all(c is not None for c in [stage_01_costs, stage_02_only_costs, szz_agent_stage_02_costs])
    rows = [
        (
            "Stage 1 Only",
            stage_01_summary["precision"],
            stage_01_summary["recall"],
            stage_01_summary["f1_score"],
            stage_01_costs / num_fix_commits if has_costs else None,
            False,
        ),
        (
            "Stage 2 Only",
            stage_02_only_summary["precision"],
            stage_02_only_summary["recall"],
            stage_02_only_summary["f1_score"],
            stage_02_only_costs / num_fix_commits if has_costs else None,
            False,
        ),
        (
            "SZZ-Agent",
            szz_agent_summary["precision"],
            szz_agent_summary["recall"],
            szz_agent_summary["f1_score"],
            (stage_01_costs + szz_agent_stage_02_costs) / num_fix_commits if has_costs else None,
            True,
        ),
    ]

    # Determine best values
    best_p = max(r[1] for r in rows)
    best_r = max(r[2] for r in rows)
    best_f = max(r[3] for r in rows)
    best_cost = min(r[4] for r in rows) if has_costs else None

    # Build LaTeX
    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Contribution of Stage 1 and Stage 2. "
        r"Costs are average per fix commit. Best results in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:stage_01_vs_stage_02}")
    lines.append(r"\begin{tabular}{l ccc c}")
    lines.append(r"\toprule")
    lines.append(
        r"\textbf{Approach} & \textbf{Precision} & \textbf{Recall} & \textbf{F1-Score} "
        r"& \textbf{Cost (USD)} \\"
    )
    lines.append(r"\midrule")

    for name, p, r, f, cost, is_ours in rows:
        if is_ours:
            lines.append(r"\midrule")

        cells = []

        # Approach name
        if is_ours:
            cells.append(r"\textbf{" + name + "}")
        else:
            cells.append(name)

        # Precision, Recall, F1
        for val, bval in [(p, best_p), (r, best_r), (f, best_f)]:
            s = fmt(val)
            if abs(val - bval) < 1e-9:
                s = r"\textbf{" + s + "}"
            cells.append(s)

        # Cost (lower is better)
        if cost is not None:
            cost_s = f"\\${cost:.2f}"
            if best_cost is not None and abs(cost - best_cost) < 1e-9:
                cost_s = r"\textbf{" + cost_s + "}"
        else:
            cost_s = "--"
        cells.append(cost_s)

        row = " & ".join(cells) + r" \\"
        if is_ours:
            row = r"\rowcolor{gray!20} " + row
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
