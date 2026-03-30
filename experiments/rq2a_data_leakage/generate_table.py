#!/usr/bin/env python3
"""Generate LaTeX data leakage table from results data.

Reads results for the data leakage experiment, computes macro-averaged precision,
recall, and F1 using evaluation_utils, and outputs a LaTeX table to
tables/table_data_leakage.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_data_leakage.tex"

# Approaches in display order: (file_prefix, display_name, is_ours)
APPROACHES = [
    ("baseline_bszz",    "SZZ",     False),
    ("baseline_agszz",   "AG-SZZ",  False),
    ("baseline_lszz",    "L-SZZ",   False),
    ("baseline_rszz",    "R-SZZ",   False),
    ("baseline_maszz",   "MA-SZZ",  False),
    ("baseline_raszz",   "RA-SZZ",  False),
    ("baseline_vszz",    "V-SZZ",   False),
    ("baseline_llm4szz", "LLM4SZZ", False),
    ("szz_agent_stage_02", "SZZ-Agent (Ours)", True),
]

# Hardcoded result file names per approach prefix
HARDCODED_FILES = {
    "baseline_bszz": "baseline_bszz_20260202_143005.json",
    "baseline_agszz": "baseline_agszz_20260202_143638.json",
    "baseline_lszz": "baseline_lszz_20260202_143840.json",
    "baseline_rszz": "baseline_rszz_20260202_171026.json",
    "baseline_maszz": "baseline_maszz_20260202_171303.json",
    "baseline_raszz": "baseline_raszz_20260202_171452.json",
    "baseline_vszz": "baseline_vszz_20260202_175926.json",
    "baseline_llm4szz": "baseline_llm4szz_20260202_184212.json",
    "szz_agent_stage_02": "szz_agent_stage_02_20260202_194137.json",
}


def find_latest_file(directory: Path, prefix: str) -> Path:
    """Find the latest JSON file matching a prefix by timestamp in filename."""
    matches = sorted(directory.glob(f"{prefix}*.json"))
    if not matches:
        raise FileNotFoundError(f"No files matching '{prefix}*' in {directory}")
    return matches[-1]


def load_and_evaluate(filepath: Path) -> dict:
    """Load a results JSON file and compute metrics."""
    with open(filepath) as f:
        data = json.load(f)

    if isinstance(data, dict) and "results" in data:
        results = data["results"]
    else:
        results = data

    return evaluate_results(results)


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

    # Collect metrics: metrics[prefix] = (precision, recall, f1)
    metrics = {}
    for prefix, name, _ in APPROACHES:
        if args.use_latest_results:
            fpath = find_latest_file(RESULTS_DIR, prefix)
        else:
            fpath = RESULTS_DIR / HARDCODED_FILES[prefix]
        summary = load_and_evaluate(fpath)
        metrics[prefix] = (
            summary["precision"],
            summary["recall"],
            summary["f1_score"],
        )

    # Determine best values
    best_p = max(m[0] for m in metrics.values())
    best_r = max(m[1] for m in metrics.values())
    best_f = max(m[2] for m in metrics.values())

    # Build LaTeX
    lines = []
    lines.append(r"\begin{table}[htbp]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Comparison of SZZ variants on 100 samples published after "
        r"the training data cutoff of Claude Opus 4.5 (August 2025). "
        r"Best results in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:results_leakage}")
    lines.append(r"\begin{tabular}{lccc}")
    lines.append(r"\toprule")
    lines.append(
        r"\textbf{Approach} & \textbf{Precision} & \textbf{Recall} & \textbf{F1-Score} \\"
    )
    lines.append(r"\midrule")

    for prefix, name, is_ours in APPROACHES:
        if is_ours:
            lines.append(r"\midrule")

        cells = []
        if is_ours:
            cells.append(r"\textbf{" + name + "}")
        else:
            cells.append(name)

        p, r, f = metrics[prefix]
        for val, bval in [(p, best_p), (r, best_r), (f, best_f)]:
            s = fmt(val)
            if abs(val - bval) < 1e-9:
                s = r"\textbf{" + s + "}"
            cells.append(s)

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
