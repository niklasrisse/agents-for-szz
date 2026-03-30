#!/usr/bin/env python3
"""Generate LaTeX ablation table for the Without_Message_Or_Diff experiment.

Reads stage_02 results and baseline_bszz, computes macro-averaged precision,
recall, and F1 using evaluation_utils, and outputs a LaTeX table to
tables/table_without_message_or_diff.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_without_message_or_diff.tex"

# Hardcoded result file names
HARDCODED_BASELINE = "baseline_bszz_20260202_143005.json"
# (without_fc_message, without_fc_diff) -> filename
HARDCODED_STAGE02 = {
    (None, None): "szz_agent_stage_02_20260202_194137.json",        # reference (both present)
    (False, True): "szz_agent_stage_02_20260206_111716.json",       # without diff
    (True, False): "szz_agent_stage_02_20260206_131801.json",       # without message
    (True, True): "szz_agent_stage_02_20260206_165630.json",        # without both
}


def load_and_evaluate(filepath: Path) -> tuple:
    """Load a results JSON file and compute metrics, returning (summary, metadata)."""
    with open(filepath) as f:
        data = json.load(f)

    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}

    if isinstance(data, dict) and "results" in data:
        results = data["results"]
    else:
        results = data

    return evaluate_results(results), metadata


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

    if args.use_latest_results:
        # Load baseline
        baseline_files = sorted(RESULTS_DIR.glob("baseline_bszz*.json"))
        if not baseline_files:
            raise FileNotFoundError("No baseline_bszz file found")
        baseline_summary, _ = load_and_evaluate(baseline_files[-1])

        # Load all stage_02 files and classify by metadata, keeping latest per variant
        stage02_files = sorted(RESULTS_DIR.glob("szz_agent_stage_02*.json"))
    else:
        baseline_summary, _ = load_and_evaluate(RESULTS_DIR / HARDCODED_BASELINE)
        stage02_files = [RESULTS_DIR / HARDCODED_STAGE02[k] for k in HARDCODED_STAGE02]

    if not args.use_latest_results:
        # Load from hardcoded files
        ref_file = RESULTS_DIR / HARDCODED_STAGE02[(None, None)]
        ref_summary, _ = load_and_evaluate(ref_file)
        reference = (ref_summary, True, True)

        ablations = []
        for (wm, wd), fname in HARDCODED_STAGE02.items():
            if wm is None and wd is None:
                continue
            summary, _ = load_and_evaluate(RESULTS_DIR / fname)
            with_msg = not wm
            with_diff = not wd
            ablations.append((summary, with_msg, with_diff))
    else:
        reference = None
        ablations = []

        # Keep only the latest file per variant (keyed by without_fc_message, without_fc_diff)
        # Normalize None/False to False for consistent keying
        latest_per_variant: dict[tuple, Path] = {}
        for f in stage02_files:
            with open(f) as fh:
                meta = json.load(fh).get("metadata", {})
            key = (bool(meta.get("without_fc_message")), bool(meta.get("without_fc_diff")))
            latest_per_variant[key] = f  # files are sorted, so last wins

        for key, f in latest_per_variant.items():
            summary, _ = load_and_evaluate(f)
            without_msg, without_diff = key

            if not without_msg and not without_diff:
                reference = (summary, True, True)
            else:
                with_msg = not without_msg
                with_diff = not without_diff
                ablations.append((summary, with_msg, with_diff))

        if reference is None:
            raise FileNotFoundError("No reference stage_02 file found (without metadata keys)")

    # Sort ablations: without both first, then without diff, then without message
    ablations.sort(key=lambda x: (x[1], x[2]))

    # Build rows: (name, with_msg, with_diff, precision, recall, f1, is_baseline, is_reference)
    rows = []

    # Baseline first
    rows.append((
        "SZZ",
        None, None,
        baseline_summary["precision"],
        baseline_summary["recall"],
        baseline_summary["f1_score"],
        True, False,
    ))

    # Ablation variants
    for summary, with_msg, with_diff in ablations:
        rows.append((
            "SZZ-Agent",
            with_msg, with_diff,
            summary["precision"],
            summary["recall"],
            summary["f1_score"],
            False, False,
        ))

    # Reference at bottom
    summary, with_msg, with_diff = reference
    rows.append((
        "SZZ-Agent",
        with_msg, with_diff,
        summary["precision"],
        summary["recall"],
        summary["f1_score"],
        False, True,
    ))

    # Determine best values (across all rows)
    best_p = max(r[3] for r in rows)
    best_r = max(r[4] for r in rows)
    best_f = max(r[5] for r in rows)

    # Build LaTeX
    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Ablation study on the impact of the fix commit message and diff "
        r"on SZZ-Agent performance. Best results in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:results_without_message_or_diff}")
    lines.append(r"\begin{tabular}{lcc ccc}")
    lines.append(r"\toprule")
    lines.append(
        r"\textbf{Approach} & \makecell{\textbf{With}\\\textbf{Message}} "
        r"& \makecell{\textbf{With}\\\textbf{Diff}} "
        r"& \textbf{Precision} & \textbf{Recall} & \textbf{F1-Score} \\"
    )
    lines.append(r"\midrule")

    for name, with_msg, with_diff, p, r, f, is_baseline, is_reference in rows:
        if is_reference:
            lines.append(r"\midrule")

        cells = []

        # Approach name
        if is_reference:
            cells.append(r"\textbf{" + name + "}")
        else:
            cells.append(name)

        # With FC Message / With FC Diff columns
        if is_baseline:
            cells.append("--")
            cells.append("--")
        else:
            cells.append(r"\checkmark" if with_msg else r"\ding{55}")
            cells.append(r"\checkmark" if with_diff else r"\ding{55}")

        # Metrics
        for val, bval in [(p, best_p), (r, best_r), (f, best_f)]:
            s = fmt(val)
            if abs(val - bval) < 1e-9:
                s = r"\textbf{" + s + "}"
            cells.append(s)

        row = " & ".join(cells) + r" \\"
        if is_reference:
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
