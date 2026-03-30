#!/usr/bin/env python3
"""Generate LaTeX table comparing SZZ-Agent against LLM4SZZ with different models.

Reads results, computes macro-averaged precision, recall, and F1 using
evaluation_utils, and outputs a LaTeX table to
tables/table_llm4szz_different_models.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_llm4szz_different_models.tex"

# Display order for LLM4SZZ model variants
LLM4SZZ_MODEL_ORDER = ["gpt-4o-mini", "gpt-5.2", "claude-opus-4-5"]

# Hardcoded result file names
HARDCODED_FILES = {
    "gpt-4o-mini": "baseline_llm4szz_20260129_231224.json",
    "gpt-5.2": "baseline_llm4szz_20260130_111252.json",
    "claude-opus-4-5": "baseline_llm4szz_20260203_125937.json",
    "szz_agent": "szz_agent_stage_02_20260130_125819.json",
}


def load_and_evaluate(filepath: Path) -> dict:
    """Load a results JSON file and compute metrics."""
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
        # Discover baseline LLM4SZZ files and map by model name
        llm4szz_files = {}
        agent_file = None

        for f in RESULTS_DIR.glob("*.json"):
            if f.name == "statistical_comparison.json" or f.name.startswith("szz_agent_stage_01"):
                continue
            if f.name.startswith("szz_agent_stage_02"):
                if agent_file is None or f.name > agent_file.name:
                    agent_file = f
                continue
            if f.name.startswith("baseline_llm4szz"):
                with open(f) as fh:
                    meta = json.load(fh).get("metadata", {})
                model = meta.get("model", meta.get("config", {}).get("model", "unknown"))
                if model not in llm4szz_files or f.name > llm4szz_files[model].name:
                    llm4szz_files[model] = f

        if agent_file is None:
            raise FileNotFoundError("No szz_agent_stage_02 file found")
    else:
        llm4szz_files = {model: RESULTS_DIR / HARDCODED_FILES[model] for model in LLM4SZZ_MODEL_ORDER}
        agent_file = RESULTS_DIR / HARDCODED_FILES["szz_agent"]

    # Evaluate all approaches: list of (display_name, precision, recall, f1, is_ours)
    rows = []

    for model in LLM4SZZ_MODEL_ORDER:
        if model not in llm4szz_files:
            raise FileNotFoundError(f"No LLM4SZZ baseline found for model {model}")
        summary, _ = load_and_evaluate(llm4szz_files[model])
        rows.append((f"LLM4SZZ ({model})", summary["precision"], summary["recall"], summary["f1_score"], False))

    summary, meta = load_and_evaluate(agent_file)
    agent_model = meta.get("model", "claude-opus-4-5")
    rows.append((f"SZZ-Agent ({agent_model})", summary["precision"], summary["recall"], summary["f1_score"], True))

    # Determine best values
    best_p = max(r[1] for r in rows)
    best_r = max(r[2] for r in rows)
    best_f = max(r[3] for r in rows)

    # Build LaTeX
    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Comparison of SZZ-Agent against LLM4SZZ with different underlying "
        r"models on 200 samples. Best results in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:results_models}")
    lines.append(r"\begin{tabular}{lccc}")
    lines.append(r"\toprule")
    lines.append(
        r"\textbf{Approach} & \textbf{Precision} & \textbf{Recall} & \textbf{F1-Score} \\"
    )
    lines.append(r"\midrule")

    for name, p, r, f, is_ours in rows:
        if is_ours:
            lines.append(r"\midrule")

        cells = []
        if is_ours:
            cells.append(r"\textbf{" + name + "}")
        else:
            cells.append(name)

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
