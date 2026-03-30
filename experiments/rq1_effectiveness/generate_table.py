#!/usr/bin/env python3
"""Generate LaTeX effectiveness table from results data.

Reads results for DS_LINUX, DS_GITHUB-c, and DS_GITHUB-j, computes
macro-averaged precision, recall, and F1 using evaluation_utils, and
outputs a LaTeX table to tables/table_effectiveness.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_effectiveness.tex"

# Datasets in display order
DATASETS = ["DS_LINUX", "DS_GITHUB-c", "DS_GITHUB-j"]

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
    ("szz_agent_stage_02", "SZZ-Agent", True),
]

# Hardcoded result file names per dataset and approach prefix
HARDCODED_FILES = {
    "DS_LINUX": {
        "baseline_bszz": "baseline_bszz_20260130_000017.json",
        "baseline_agszz": "baseline_agszz_20260201_201304.json",
        "baseline_lszz": "baseline_lszz_20260202_031423.json",
        "baseline_rszz": "baseline_rszz_20260201_202132.json",
        "baseline_maszz": "baseline_maszz_20260202_023229.json",
        "baseline_raszz": "baseline_raszz_20260202_023407.json",
        "baseline_vszz": "baseline_vszz_20260129_195617.json",
        "baseline_llm4szz": "baseline_llm4szz_20260129_231224.json",
        "szz_agent_stage_02": "szz_agent_stage_02_20260130_125819.json",
    },
    "DS_GITHUB-c": {
        "baseline_bszz": "baseline_bszz_20260204_123509.json",
        "baseline_agszz": "baseline_agszz_20260204_132135.json",
        "baseline_lszz": "baseline_lszz_20260204_132645.json",
        "baseline_rszz": "baseline_rszz_20260204_132923.json",
        "baseline_maszz": "baseline_maszz_20260204_143538.json",
        "baseline_raszz": "baseline_raszz_20260204_142421.json",
        "baseline_vszz": "baseline_vszz_20260204_142653.json",
        "baseline_llm4szz": "baseline_llm4szz_20260204_153717.json",
        "szz_agent_stage_02": "szz_agent_stage_02_20260205_104113.json",
    },
    "DS_GITHUB-j": {
        "baseline_bszz": "baseline_bszz_20260203_102549.json",
        "baseline_agszz": "baseline_agszz_20260203_105927.json",
        "baseline_lszz": "baseline_lszz_20260203_112306.json",
        "baseline_rszz": "baseline_rszz_20260203_114505.json",
        "baseline_maszz": "baseline_maszz_20260203_121332.json",
        "baseline_raszz": "baseline_raszz_20260203_163206.json",
        "baseline_vszz": "baseline_vszz_20260203_150807.json",
        "baseline_llm4szz": "baseline_llm4szz_20260203_165831.json",
        "szz_agent_stage_02": "szz_agent_stage_02_20260203_211744.json",
    },
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

    # Collect metrics: metrics[dataset][approach_prefix] = (precision, recall, f1)
    metrics = {}
    for ds in DATASETS:
        ds_dir = RESULTS_DIR / ds
        metrics[ds] = {}
        for prefix, name, _ in APPROACHES:
            if args.use_latest_results:
                fpath = find_latest_file(ds_dir, prefix)
            else:
                fpath = ds_dir / HARDCODED_FILES[ds][prefix]
            summary = load_and_evaluate(fpath)
            metrics[ds][prefix] = (
                summary["precision"],
                summary["recall"],
                summary["f1_score"],
            )

    # Determine best per dataset (across all approaches)
    best = {}
    for ds in DATASETS:
        best_p = max(m[0] for m in metrics[ds].values())
        best_r = max(m[1] for m in metrics[ds].values())
        best_f = max(m[2] for m in metrics[ds].values())
        best[ds] = (best_p, best_r, best_f)

    # Build LaTeX
    n_ds = len(DATASETS)
    total_cols = 1 + 3 * n_ds  # approach + 3 metrics per dataset

    col_spec = "l" + "ccc" * n_ds

    lines = []
    lines.append(r"\begin{table*}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Comparison of SZZ variants for bug-inducing commit identification. "
        r"Best results per dataset in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:results}")
    lines.append(r"\begin{tabular}{" + col_spec + "}")
    lines.append(r"\toprule")

    # Header row 1: dataset names spanning 3 columns each
    header1_parts = [""]
    for i, ds in enumerate(DATASETS):
        ds_label = ds.replace("_", r"\_")
        header1_parts.append(
            r"\multicolumn{3}{c}{\textbf{" + ds_label + "}}"
        )
    lines.append(" & ".join(header1_parts) + r" \\")

    # cmidrule for each dataset
    cmidrules = []
    for i in range(n_ds):
        start = 2 + 3 * i
        end = start + 2
        cmidrules.append(f"\\cmidrule(lr){{{start}-{end}}}")
    lines.append(" ".join(cmidrules))

    # Header row 2: metric names
    header2_parts = [r"\textbf{Approach}"]
    for _ in DATASETS:
        header2_parts.extend([
            r"\textbf{Precision}",
            r"\textbf{Recall}",
            r"\textbf{F1-Score}",
        ])
    lines.append(" & ".join(header2_parts) + r" \\")
    lines.append(r"\midrule")

    # Data rows
    for prefix, name, is_ours in APPROACHES:
        if is_ours:
            lines.append(r"\midrule")

        cells = []
        if is_ours:
            cells.append(r"\textbf{" + name + "}")
        else:
            cells.append(name)

        for ds in DATASETS:
            p, r, f = metrics[ds][prefix]
            bp, br, bf = best[ds]

            vals = [(p, bp), (r, br), (f, bf)]
            for val, bval in vals:
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
    lines.append(r"\end{table*}")

    latex = "\n".join(lines) + "\n"

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(latex)
    print(f"Table written to {OUTPUT_FILE}")
    print()
    print(latex)


if __name__ == "__main__":
    main()
