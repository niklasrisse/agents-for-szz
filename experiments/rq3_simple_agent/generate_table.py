#!/usr/bin/env python3
"""Generate LaTeX agent comparison table from results data.

Reads results for DS_LINUX, DS_GITHUB-c, and DS_GITHUB-j, computes
macro-averaged precision, recall, and F1 using evaluation_utils, and
outputs a LaTeX table to tables/table_agent_comparison.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_agent_comparison.tex"

# Hardcoded result file names per dataset and approach prefix
HARDCODED_FILES = {
    "DS_LINUX": {
        "baseline_bszz": "baseline_bszz_20260130_000017.json",
        "baseline_llm4szz": "baseline_llm4szz_20260129_231224.json",
        "szz_agent_stage_02": "szz_agent_stage_02_20260130_125819.json",
        "simple_szz_agent": "simple_szz_agent_20260320_161712.json",
    },
    "DS_GITHUB-c": {
        "baseline_bszz": "baseline_bszz_20260204_123509.json",
        "baseline_llm4szz": "baseline_llm4szz_20260204_153717.json",
        "szz_agent_stage_02": "szz_agent_stage_02_20260205_104113.json",
        "simple_szz_agent": "simple_szz_agent_20260212_201433.json",
    },
    "DS_GITHUB-j": {
        "baseline_bszz": "baseline_bszz_20260203_102549.json",
        "baseline_llm4szz": "baseline_llm4szz_20260203_165831.json",
        "szz_agent_stage_02": "szz_agent_stage_02_20260203_211744.json",
        "simple_szz_agent": "simple_szz_agent_20260212_154210.json",
    },
}

# Datasets in display order
DATASETS = ["DS_LINUX", "DS_GITHUB-c", "DS_GITHUB-j"]

DATASET_DISPLAY_NAMES = {
    "DS_LINUX": r"DS\textsubscript{LINUX}",
    "DS_GITHUB-c": r"DS\textsubscript{GITHUB-c}",
    "DS_GITHUB-j": r"DS\textsubscript{GITHUB-j}",
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


def generate_agent_comparison_table():
    """Generate a table comparing SZZ-Agent vs Simple-SZZ-Agent."""
    args = parse_args()

    # (file_prefix, display_name, highlight)
    # highlight: "midrule+gray" = midrule before + gray row, None = normal
    AGENT_APPROACHES = [
        ("baseline_bszz",      "SZZ",              None),
        ("baseline_llm4szz",   "LLM4SZZ",          None),
        ("szz_agent_stage_02", "SZZ-Agent",         None),
        ("simple_szz_agent",   "Simple-SZZ-Agent",  "midrule+gray"),
    ]

    # (directory_name, display_label)
    COMP_DATASETS = [
        ("DS_LINUX",      "DS_LINUX"),
        ("DS_GITHUB-c",   "DS_GITHUB-c"),
        ("DS_GITHUB-j",   "DS_GITHUB-j"),
    ]

    # Collect metrics
    metrics = {}
    for ds_dir_name, ds_label in COMP_DATASETS:
        ds_dir = RESULTS_DIR / ds_dir_name
        metrics[ds_label] = {}
        for prefix, name, _ in AGENT_APPROACHES:
            if args.use_latest_results:
                fpath = find_latest_file(ds_dir, prefix)
            else:
                fpath = ds_dir / HARDCODED_FILES[ds_dir_name][prefix]
            summary = load_and_evaluate(fpath)
            metrics[ds_label][prefix] = (
                summary["precision"],
                summary["recall"],
                summary["f1_score"],
            )

    ds_labels = [label for _, label in COMP_DATASETS]

    # Determine best per dataset (compare on formatted/rounded values)
    best = {}
    for ds in ds_labels:
        best_p = max(fmt(m[0]) for m in metrics[ds].values())
        best_r = max(fmt(m[1]) for m in metrics[ds].values())
        best_f = max(fmt(m[2]) for m in metrics[ds].values())
        best[ds] = (best_p, best_r, best_f)

    # Build LaTeX
    n_ds = len(ds_labels)
    col_spec = "l" + "ccc" * n_ds

    lines = []
    lines.append(r"\begin{table*}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Comparison of SZZ-Agent and Simple-SZZ-Agent for bug-inducing commit identification. "
        r"Best results per dataset in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:agent_comparison}")
    lines.append(r"\begin{tabular}{" + col_spec + "}")
    lines.append(r"\toprule")

    # Header row 1: dataset names spanning 3 columns each
    header1_parts = [""]
    for ds in ds_labels:
        ds_escaped = ds.replace("_", r"\_")
        header1_parts.append(
            r"\multicolumn{3}{c}{\textbf{" + ds_escaped + "}}"
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
    for _ in ds_labels:
        header2_parts.extend([
            r"\textbf{Precision}",
            r"\textbf{Recall}",
            r"\textbf{F1-Score}",
        ])
    lines.append(" & ".join(header2_parts) + r" \\")
    lines.append(r"\midrule")

    # Data rows
    for prefix, name, highlight in AGENT_APPROACHES:
        if highlight == "midrule+gray":
            lines.append(r"\midrule")

        cells = []
        if highlight == "midrule+gray":
            cells.append(r"\textbf{" + name + "}")
        else:
            cells.append(name)

        for ds in ds_labels:
            p, r, f = metrics[ds][prefix]
            bp, br, bf = best[ds]

            vals = [(p, bp), (r, br), (f, bf)]
            for val, bval in vals:
                s = fmt(val)
                if s == bval:
                    s = r"\textbf{" + s + "}"
                cells.append(s)

        row = " & ".join(cells) + r" \\"
        if highlight == "midrule+gray":
            row = r"\rowcolor{gray!20} " + row
        lines.append(row)

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table*}")

    latex = "\n".join(lines) + "\n"

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(latex)
    print(f"Agent comparison table written to {OUTPUT_FILE}")
    print()
    print(latex)


if __name__ == "__main__":
    generate_agent_comparison_table()
