#!/usr/bin/env python3
"""Generate LaTeX table comparing Simple SZZ Agent with different models and agents.

Reads simple_szz_agent results from results/, computes
macro-averaged precision, recall, and F1 using evaluation_utils, derives
total cost, tool calls, and tokens from aggregate_stats, and outputs a LaTeX
table to tables/table_cheaper_models.tex.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "src"))
from evaluation_utils import evaluate_results


RESULTS_DIR = SCRIPT_DIR / "results"
OUTPUT_FILE = SCRIPT_DIR / "tables" / "table_cheaper_models.tex"

# Display order: (agent, model) -- grouped by agent, then cheapest to most expensive model
ROW_ORDER = [
    ("claude-code", "claude-haiku-4-5"),
    ("claude-code", "claude-sonnet-4-5"),
    ("claude-code", "claude-opus-4-5"),
    ("openhands", "claude-haiku-4-5"),
    ("openhands", "claude-sonnet-4-5"),
    ("openhands", "claude-opus-4.5"),
    ("openhands", "minimax-m2.5"),
    ("openhands", "glm-5"),
]

AGENT_DISPLAY = {
    "claude-code": "Claude Code",
    "openhands": "OpenHands",
}

# Hardcoded result file names per (agent, model)
HARDCODED_FILES = {
    ("claude-code", "claude-haiku-4-5"): "simple_szz_agent_20260213_142918.json",
    ("claude-code", "claude-sonnet-4-5"): "simple_szz_agent_20260213_201348.json",
    ("claude-code", "claude-opus-4-5"): "simple_szz_agent_20260211_212858.json",
    ("openhands", "claude-haiku-4-5"): "simple_szz_agent_20260216_152115.json",
    ("openhands", "claude-sonnet-4-5"): "simple_szz_agent_20260216_231320.json",
    ("openhands", "claude-opus-4.5"): "simple_szz_agent_20260218_013414.json",
    ("openhands", "minimax-m2.5"): "simple_szz_agent_20260218_181637.json",
    ("openhands", "glm-5"): "simple_szz_agent_20260219_212408.json",
}


def load_json(filepath: Path) -> dict:
    """Load a JSON file and return the parsed data."""
    with open(filepath) as f:
        return json.load(f)


def safe_mean(stats: dict) -> float:
    """Return the mean from a stats dict, treating None as 0."""
    return stats.get("mean") or 0.0


def load_and_evaluate(filepath: Path):
    """Load a results JSON file, compute metrics and per-fix-commit averages."""
    data = load_json(filepath)
    metadata = data.get("metadata", {})

    if "results" in data:
        results = data["results"]
    else:
        results = data

    agg = data.get("aggregate_stats", {})
    num_fix_commits = len(results)

    total_cost = agg.get("total_cost_usd", {}).get("total", 0.0)
    cost_per_fix_commit = total_cost / num_fix_commits if num_fix_commits > 0 else 0.0

    tool_calls_per_fix_commit = safe_mean(agg.get("total_tool_calls", {}))

    tokens_per_fix_commit = (
        safe_mean(agg.get("input_tokens", {}))
        + safe_mean(agg.get("output_tokens", {}))
        + safe_mean(agg.get("cache_creation_input_tokens", {}))
        + safe_mean(agg.get("cache_read_input_tokens", {}))
    )

    return evaluate_results(results), metadata, cost_per_fix_commit, tool_calls_per_fix_commit, tokens_per_fix_commit, num_fix_commits


def fmt(value: float) -> str:
    """Format a float to 2 decimal places."""
    return f"{value:.2f}"


def fmt_cost(value: float) -> str:
    """Format a cost value with dollar sign."""
    return f"\\${value:.2f}"


def fmt_tool_calls(value: float) -> str:
    """Format tool calls average to 1 decimal place."""
    return f"{value:.1f}"


def fmt_tokens(value: float) -> str:
    """Format token count with k/M suffix."""
    if value >= 1_000_000:
        m = value / 1_000_000
        return f"{m:.1f}M"
    if value >= 1_000:
        k = value / 1_000
        return f"{k:.0f}k"
    return str(int(value))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-latest-results", action="store_true",
                        help="Use the latest results files by timestamp instead of hardcoded paths")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.use_latest_results:
        # Discover simple_szz_agent files and map by (agent, model), keeping latest per key
        files_by_key: dict[tuple[str, str], Path] = {}
        for f in sorted(RESULTS_DIR.glob("simple_szz_agent_*.json")):
            data = load_json(f)
            meta = data.get("metadata", {})
            model = meta.get("model", "unknown")
            agent = meta.get("agent", "claude-code")
            files_by_key[(agent, model)] = f
    else:
        files_by_key = {key: RESULTS_DIR / fname for key, fname in HARDCODED_FILES.items()}

    # Build rows: (agent_display, model, precision, recall, f1, cost, tool_calls, tokens)
    rows = []
    for agent, model in ROW_ORDER:
        if (agent, model) not in files_by_key:
            raise FileNotFoundError(f"No simple_szz_agent file found for agent={agent}, model={model}")

        summary, metadata, cost_per_fix_commit, tool_calls_per_fc, tokens_per_fc, _ = load_and_evaluate(files_by_key[(agent, model)])

        rows.append((
            AGENT_DISPLAY.get(agent, agent),
            model,
            summary["precision"],
            summary["recall"],
            summary["f1_score"],
            cost_per_fix_commit,
            tool_calls_per_fc,
            tokens_per_fc,
        ))

    # Determine best values per column
    best_p = max(r[2] for r in rows)
    best_r = max(r[3] for r in rows)
    best_f = max(r[4] for r in rows)
    best_c = min(r[5] for r in rows)   # Lower cost is better
    best_tc = min(r[6] for r in rows)  # Lower tool calls is better
    best_tok = min(r[7] for r in rows) # Lower tokens is better

    # Build LaTeX
    lines = []
    lines.append(r"\begin{table*}[t]")
    lines.append(r"\centering")
    lines.append(
        r"\caption{Simple-SZZ-Agent with different underlying models and agents. "
        r"Best results in \textbf{bold}.}"
    )
    lines.append(r"\label{tab:results_cheaper_models}")
    lines.append(r"\begin{tabular}{llcccccc}")
    lines.append(r"\toprule")
    lines.append(
        r" & & & & & \multicolumn{3}{c}{\textit{Avg.\ per Fix Commit}} \\"
    )
    lines.append(r"\cmidrule(l){6-8}")
    lines.append(
        r"\textbf{Agent} & \textbf{Model} & \textbf{Precision} & \textbf{Recall} "
        r"& \textbf{F1-Score} & \textbf{Cost} & \textbf{Tool Calls} & \textbf{Tokens} \\"
    )
    lines.append(r"\midrule")

    prev_agent = None
    for agent_display, model, p, r, f, c, tc, tok in rows:
        # Add separator between agent groups
        if prev_agent is not None and agent_display != prev_agent:
            lines.append(r"\midrule")
        prev_agent = agent_display

        cells = [agent_display, model]

        for val, bval in [(p, best_p), (r, best_r), (f, best_f)]:
            s = fmt(val)
            if abs(val - bval) < 1e-9:
                s = r"\textbf{" + s + "}"
            cells.append(s)

        # Cost: bold if lowest
        s = fmt_cost(c)
        if abs(c - best_c) < 1e-9:
            s = r"\textbf{" + s + "}"
        cells.append(s)

        # Tool calls: bold if lowest
        s = fmt_tool_calls(tc)
        if abs(tc - best_tc) < 1e-9:
            s = r"\textbf{" + s + "}"
        cells.append(s)

        # Tokens: bold if lowest
        s = fmt_tokens(tok)
        if abs(tok - best_tok) < 1e-9:
            s = r"\textbf{" + s + "}"
        cells.append(s)

        lines.append(" & ".join(cells) + r" \\")

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
