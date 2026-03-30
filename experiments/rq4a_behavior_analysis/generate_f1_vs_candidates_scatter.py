"""
Generate a TikZ grouped bar plot: number of candidate commits (binned, x) vs
average F1-score (y).

Bins candidates into log-scaled ranges and shows the mean F1-score per bin,
with the number of samples annotated above each bar.

Produces:
  - figures/scatter_f1_vs_candidates.tex  (inputable in a paper)
  - figures/scatter_f1_vs_candidates_standalone.tex  (compilable with pdflatex)
"""

import argparse
import json
import math
import os
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

# Bin boundaries (inclusive)
BINS = [
    (1, 10, "1--10"),
    (11, 50, "11--50"),
    (51, 200, "51--200"),
    (201, 1000, "201--1k"),
    (1001, float("inf"), "{$>$1k}"),
]


def load_data(results_file):
    with open(results_file) as f:
        data = json.load(f)

    points = []
    for r, d in zip(data["results"], data["details"]):
        candidates = d.get("total_candidates")
        cs = d.get("call_stats")
        if cs is None or candidates is None:
            continue

        p = r.get("precision", 0)
        rec = r.get("recall", 0)
        if p + rec > 0:
            f1 = 2 * p * rec / (p + rec)
        else:
            f1 = 0.0

        points.append((candidates, f1))
    return points


def bin_data(points):
    """Group points into bins and compute average F1 per bin."""
    binned = []
    for lo, hi, label in BINS:
        in_bin = [f1 for tc, f1 in points if lo <= tc <= hi]
        if in_bin:
            avg_f1 = sum(in_bin) / len(in_bin)
            binned.append((label, avg_f1, len(in_bin)))
        else:
            binned.append((label, 0.0, 0))
    return binned


def generate_tikz_body(binned):
    """Generate the tikzpicture environment (the inputable part)."""
    lines = []
    lines.append(r"\begin{tikzpicture}")
    lines.append(r"\begin{axis}[")
    lines.append(r"    width=1.02\columnwidth,")
    lines.append(r"    height=0.65\columnwidth,")
    lines.append(r"    ybar,")
    lines.append(r"    bar width=20pt,")
    lines.append(r"    xlabel={Number of candidate commits},")
    lines.append(r"    ylabel={Average F1-score},")
    lines.append(r"    ymin=0, ymax=1.15,")
    lines.append(r"    ytick={0, 0.2, 0.4, 0.6, 0.8, 1.0},")
    lines.append(r"    symbolic x coords={" + ",".join(b[0] for b in binned) + r"},")
    lines.append(r"    xtick=data,")
    lines.append(r"    x tick label style={font=\small},")
    lines.append(r"    tick label style={font=\small},")
    lines.append(r"    label style={font=\small},")
    lines.append(r"    grid=major,")
    lines.append(r"    grid style={gray!30},")
    lines.append(r"    ymajorgrids=true,")
    lines.append(r"    xmajorgrids=false,")
    lines.append(r"    nodes near coords={\scriptsize $n\!=\!" + "{}$},")
    # We need per-bar node content, so use point meta for the count
    lines.append(r"    every node near coord/.append style={anchor=south, yshift=1pt},")
    lines.append(r"    xtick pos=left,")
    lines.append(r"]")

    # We'll use two addplots: one for the bars, one invisible for the labels
    # Actually, use visualization depends on to put count labels
    # Simpler: just use nodes near coords with the count value as point meta
    lines.append(r"\addplot[")
    lines.append(r"    fill=blue!50,")
    lines.append(r"    draw=blue!70!black,")
    lines.append(r"    point meta=explicit symbolic,")
    lines.append(r"    nodes near coords={\pgfplotspointmeta},")
    lines.append(r"] coordinates {")

    for label, avg_f1, n in binned:
        lines.append(f"    ({label}, {avg_f1:.3f}) [$n\\!=\\!{n}$]")

    lines.append(r"};")
    lines.append(r"\end{axis}")
    lines.append(r"\end{tikzpicture}")

    return "\n".join(lines)


def write_inputable(tikz_body):
    path = os.path.join(OUTPUT_DIR, "scatter_f1_vs_candidates.tex")
    with open(path, "w") as f:
        f.write(tikz_body + "\n")
    print(f"Written: {path}")


def write_standalone(tikz_body):
    path = os.path.join(OUTPUT_DIR, "scatter_f1_vs_candidates_standalone.tex")
    standalone_body = tikz_body.replace(
        r"width=1.02\columnwidth", "width=12cm"
    ).replace(r"height=0.65\columnwidth", "height=7cm")
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
    points = load_data(results_file)
    print(f"Loaded {len(points)} data points")
    binned = bin_data(points)
    for label, avg_f1, n in binned:
        print(f"  {label}: n={n}, avg_f1={avg_f1:.3f}")
    tikz_body = generate_tikz_body(binned)
    write_inputable(tikz_body)
    write_standalone(tikz_body)


if __name__ == "__main__":
    main()
