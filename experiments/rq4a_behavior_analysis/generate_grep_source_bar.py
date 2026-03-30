"""
Generate a TikZ horizontal bar chart: source classification of grep search patterns.

Shows where the agent's grep search patterns originate from (fix commit message,
diff removed/added/context lines, function names, file paths, etc.).

Produces:
  - figures/bar_grep_sources.tex           (inputable in a paper)
  - figures/bar_grep_sources_standalone.tex (compilable with pdflatex)
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_FILE = str(SCRIPT_DIR / "results" / "simple_szz_agent_20260211_212858.json")

# Reuse the analysis logic from analyze_grep_calls.py
sys.path.insert(0, str(SCRIPT_DIR))
from analyze_grep_calls import (
    REPO_PATH,
    get_commit_message,
    get_commit_diff,
    parse_diff,
    extract_grep_patterns_from_tool,
    extract_grep_patterns_from_bash,
    classify_pattern_source,
)

OUTPUT_DIR = str(SCRIPT_DIR / "figures")


def find_latest_results_file():
    """Find the latest simple_szz_agent results file by timestamp."""
    matches = sorted(Path(SCRIPT_DIR / "results").glob("simple_szz_agent_*.json"))
    if not matches:
        raise FileNotFoundError("No simple_szz_agent results files found")
    return str(matches[-1])


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-latest-results", action="store_true",
                        help="Use the latest results file by timestamp instead of hardcoded path")
    return parser.parse_args()

# Human-readable labels for each source category, ordered for display
SOURCE_LABELS = {
    "diff_removed_lines": "FC Diff Removed Lines",
    "fix_commit_message": "FC Message",
    "diff_added_lines": "FC Diff Added Lines",
    "diff_context_lines": "FC Diff Context Lines",
    "diff_hunk_headers": "FC Diff Hunk Headers",
    "diff_function_names": "FC Diff Function Names",
    "diff_file_paths": "FC Diff File Paths",
    "diff_path_components": "FC Diff Path Components",
    "diff_raw_match": "FC Diff Raw Match",
    "commit_hash_pattern": "Commit Hash Pattern",
    "could_not_be_determined": "Undetermined",
}


def load_data(results_file):
    """Compute source classification counts. Returns (categories, total)."""
    with open(results_file) as f:
        data = json.load(f)

    # Collect all grep calls
    all_grep_calls = []
    for detail in data["details"]:
        cs = detail.get("call_stats")
        if cs is None:
            continue
        turns = cs.get("turns")
        if turns is None:
            continue

        for turn in turns:
            for tc in turn["tool_calls"]:
                if tc["name"] == "Grep":
                    pattern = extract_grep_patterns_from_tool(tc)
                    if pattern:
                        all_grep_calls.append({
                            "pattern": pattern,
                            "fix_commit": detail["fix_commit"],
                        })
                elif tc["name"] == "Bash":
                    command = tc["input"].get("command", "")
                    pattern = extract_grep_patterns_from_bash(command)
                    if pattern:
                        all_grep_calls.append({
                            "pattern": pattern,
                            "fix_commit": detail["fix_commit"],
                        })

    # Fetch commit data
    fix_commits = set(g["fix_commit"] for g in all_grep_calls)
    repo_path = REPO_PATH
    commit_cache = {}
    for commit_hash in fix_commits:
        msg = get_commit_message(repo_path, commit_hash)
        diff = get_commit_diff(repo_path, commit_hash)
        parsed = parse_diff(diff)
        commit_cache[commit_hash] = {"message": msg, "diff_parsed": parsed}

    # Classify each grep call
    source_counts = defaultdict(int)
    total = len(all_grep_calls)

    for grep_call in all_grep_calls:
        cache = commit_cache[grep_call["fix_commit"]]
        sources = classify_pattern_source(
            grep_call["pattern"],
            cache["message"],
            cache["diff_parsed"],
        )
        for src in sources:
            source_counts[src] += 1

    # Build results as list of (key, label, count, pct), sorted by count desc
    categories = []
    for key, label in SOURCE_LABELS.items():
        count = source_counts.get(key, 0)
        if count > 0:
            pct = count / total * 100
            categories.append((key, label, count, pct))

    categories.sort(key=lambda x: x[2])  # ascending for bottom-to-top bar chart

    return categories, total


def generate_tikz_body(categories, total):
    """Generate the tikzpicture environment for a horizontal bar chart."""
    labels = [label for _, label, _, _ in categories]

    lines = []
    lines.append(r"\begin{tikzpicture}")
    lines.append(r"\begin{axis}[")
    lines.append(r"    width=0.95\columnwidth,")
    lines.append(r"    height=0.7\columnwidth,")
    lines.append(r"    xbar,")
    lines.append(r"    bar width=8pt,")
    lines.append(r"    xlabel={Percentage of grep calls (\%)},")
    lines.append(r"    xmin=0,")
    lines.append(r"    xmax=60,")
    # Wrap each label in braces for pgfplots symbolic coords with spaces
    coord_labels = ",".join("{" + l + "}" for l in labels)
    lines.append(r"    symbolic y coords={" + coord_labels + r"},")
    lines.append(r"    ytick=data,")
    lines.append(r"    y tick label style={font=\small, anchor=east},")
    lines.append(r"    tick label style={font=\small},")
    lines.append(r"    label style={font=\small},")
    lines.append(r"    grid=major,")
    lines.append(r"    grid style={gray!30},")
    lines.append(r"    xmajorgrids=true,")
    lines.append(r"    ymajorgrids=false,")
    lines.append(r"    point meta=explicit symbolic,")
    lines.append(r"    nodes near coords={\pgfplotspointmeta},")
    lines.append(r"    nodes near coords style={font=\scriptsize, anchor=west},")
    lines.append(r"    enlarge y limits=0.08,")
    lines.append(r"    clip=false,")
    lines.append(r"]")

    lines.append(r"\addplot[")
    lines.append(r"    fill=blue!50,")
    lines.append(r"    draw=blue!70!black,")
    lines.append(r"] coordinates {")
    for _, label, count, pct in categories:
        lines.append(f"    ({pct:.1f},{{{label}}}) [{pct:.1f}\\%]")
    lines.append(r"};")

    lines.append(r"\end{axis}")
    lines.append(r"\end{tikzpicture}")

    return "\n".join(lines)


def write_inputable(tikz_body):
    path = os.path.join(OUTPUT_DIR, "bar_grep_sources.tex")
    with open(path, "w") as f:
        f.write(tikz_body + "\n")
    print(f"Written: {path}")


def write_standalone(tikz_body):
    path = os.path.join(OUTPUT_DIR, "bar_grep_sources_standalone.tex")
    standalone_body = tikz_body.replace(
        r"width=0.95\columnwidth", "width=12cm"
    ).replace(r"height=0.7\columnwidth", "height=9cm")
    content = (
        r"""\documentclass[border=5pt]{standalone}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\begin{document}
"""
        + standalone_body
        + "\n"
        + r"""\end{document}
"""
    )
    with open(path, "w") as f:
        f.write(content)
    print(f"Written: {path}")


def main():
    args = parse_args()
    results_file = find_latest_results_file() if args.use_latest_results else RESULTS_FILE
    print("Computing grep pattern source classifications...")
    categories, total = load_data(results_file)
    print(f"\nTotal grep calls: {total}")
    print(f"Categories (ascending):")
    for key, label, count, pct in categories:
        print(f"  {label:25s}  {count:4d}  ({pct:5.1f}%)")

    tikz_body = generate_tikz_body(categories, total)
    write_inputable(tikz_body)
    write_standalone(tikz_body)


if __name__ == "__main__":
    main()
