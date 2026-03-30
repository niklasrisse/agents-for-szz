"""
Generate a TikZ bar chart: total number of tool calls per tool.

Produces:
  - figures/bar_toolcalls_per_tool.tex  (inputable in a paper)
  - figures/bar_toolcalls_per_tool_standalone.tex  (compilable with pdflatex)
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_FILE = str(SCRIPT_DIR / "results" / "simple_szz_agent_20260211_212858.json")
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


def classify_bash_command(cmd):
    """Classify a Bash command by its actual purpose."""
    cmd = cmd.strip()
    first = cmd.split()[0] if cmd else ""

    # grep invocations (including grep inside for/if loops)
    if first == "grep":
        return "Grep"
    if first in ("for", "if") and "grep" in cmd:
        return "Grep"

    # ls commands -> Glob (listing files is equivalent to globbing)
    if first == "ls":
        return "Glob"

    # head/tail -> Read
    if first in ("head", "tail"):
        return "Read"

    return "Bash"


def load_data(results_file):
    """Return list of (tool, total_count, avg_per_entry) sorted descending."""
    with open(results_file) as f:
        data = json.load(f)

    totals = Counter()
    n_entries = 0
    for d in data["details"]:
        cs = d.get("call_stats")
        if cs is None:
            continue
        n_entries += 1

        # Count non-Bash tools directly
        by_name = cs.get("tool_calls_by_name", {})
        for tool, count in by_name.items():
            if tool != "Bash":
                totals[tool] += count

        # Reclassify Bash commands by their actual purpose
        for turn in cs.get("turns", []):
            for tc in turn.get("tool_calls", []):
                if tc.get("name") == "Bash":
                    cmd = tc.get("input", {}).get("command", "")
                    category = classify_bash_command(cmd)
                    totals[category] += 1

    # Sort descending by count, include average
    result = []
    for tool, count in totals.most_common():
        avg = count / n_entries if n_entries > 0 else 0
        result.append((tool, count, avg))
    return result


def generate_tikz_body(tool_counts):
    """Generate the tikzpicture environment (the inputable part)."""
    labels = [t for t, _, _ in tool_counts]

    lines = []
    lines.append(r"\begin{tikzpicture}")
    lines.append(r"\begin{axis}[")
    lines.append(r"    width=0.85\columnwidth,")
    lines.append(r"    height=0.52\columnwidth,")
    lines.append(r"    ybar,")
    lines.append(r"    bar width=20pt,")
    lines.append(r"    ylabel={Avg.\ number of tool calls},")
    lines.append(r"    ymin=0,")
    # Add top margin: ymax = ceil(max_avg) + 1
    max_avg = max(avg for _, _, avg in tool_counts)
    ymax = int(max_avg) + 2
    lines.append(r"    ymax=" + str(ymax) + r",")
    lines.append(r"    symbolic x coords={" + ",".join(labels) + r"},")
    lines.append(r"    xtick=data,")
    lines.append(r"    x tick label style={font=\small},")
    lines.append(r"    tick label style={font=\small},")
    lines.append(r"    label style={font=\small},")
    lines.append(r"    grid=major,")
    lines.append(r"    grid style={gray!30},")
    lines.append(r"    ymajorgrids=true,")
    lines.append(r"    xmajorgrids=false,")
    lines.append(r"    point meta=explicit symbolic,")
    lines.append(r"    nodes near coords={\pgfplotspointmeta},")
    lines.append(r"    nodes near coords style={font=\scriptsize, anchor=south},")
    lines.append(r"    enlarge x limits=0.15,")
    lines.append(r"    xtick pos=left,")
    lines.append(r"]")

    lines.append(r"\addplot[")
    lines.append(r"    fill=blue!50,")
    lines.append(r"    draw=blue!70!black,")
    lines.append(r"] coordinates {")
    for tool, _, avg in tool_counts:
        # Use enough decimals so value is never shown as 0
        if avg == 0:
            label = "0"
        elif avg < 0.05:
            label = f"{avg:.2f}"
        else:
            label = f"{avg:.1f}"
        lines.append(f"    ({tool}, {avg:.2f}) [{label}]")
    lines.append(r"};")

    lines.append(r"\end{axis}")
    lines.append(r"\end{tikzpicture}")

    return "\n".join(lines)


def write_inputable(tikz_body):
    path = os.path.join(OUTPUT_DIR, "bar_toolcalls_per_tool.tex")
    with open(path, "w") as f:
        f.write(tikz_body + "\n")
    print(f"Written: {path}")


def write_standalone(tikz_body):
    path = os.path.join(OUTPUT_DIR, "bar_toolcalls_per_tool_standalone.tex")
    standalone_body = tikz_body.replace(
        r"width=0.85\columnwidth", "width=10cm"
    ).replace(r"height=0.52\columnwidth", "height=5.6cm")
    content = r"""\documentclass[border=5pt]{standalone}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\begin{document}
""" + standalone_body + "\n" + r"""\end{document}
"""
    with open(path, "w") as f:
        f.write(content)
    print(f"Written: {path}")


def main():
    args = parse_args()
    results_file = find_latest_results_file() if args.use_latest_results else RESULTS_FILE
    tool_counts = load_data(results_file)
    print(f"Tool call totals:")
    for tool, count, avg in tool_counts:
        print(f"  {tool}: total={count}, avg={avg:.1f}")
    tikz_body = generate_tikz_body(tool_counts)
    write_inputable(tikz_body)
    write_standalone(tikz_body)


if __name__ == "__main__":
    main()
