"""
Generate a TikZ scatterplot: number of candidate commits (x) vs total cost in USD (y).

Produces:
  - figures/scatter_cost_vs_candidates.tex  (inputable in a paper)
  - figures/scatter_cost_vs_candidates_standalone.tex  (compilable with pdflatex)
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


def load_data(results_file):
    with open(results_file) as f:
        data = json.load(f)

    points = []
    for d in data["details"]:
        candidates = d.get("total_candidates")
        cs = d.get("call_stats")
        if cs is None:
            continue
        cost = cs.get("total_cost_usd")
        if candidates is not None and cost is not None:
            points.append((candidates, cost))
    return points


def fit_log_linear(points):
    """Fit y = a * ln(x) + b via least squares. Returns (a, b, r)."""
    n = len(points)
    lnx = [math.log(x) for x, _ in points]
    ys = [y for _, y in points]

    sum_lnx = sum(lnx)
    sum_y = sum(ys)
    sum_lnx_y = sum(l * y for l, y in zip(lnx, ys))
    sum_lnx2 = sum(l ** 2 for l in lnx)
    sum_y2 = sum(y ** 2 for y in ys)

    a = (n * sum_lnx_y - sum_lnx * sum_y) / (n * sum_lnx2 - sum_lnx ** 2)
    b = (sum_y - a * sum_lnx) / n

    # Pearson r between ln(x) and y
    num = n * sum_lnx_y - sum_lnx * sum_y
    den = math.sqrt((n * sum_lnx2 - sum_lnx ** 2) * (n * sum_y2 - sum_y ** 2))
    r = num / den

    return a, b, r


def generate_tikz_body(points):
    """Generate the tikzpicture environment (the inputable part)."""
    y_max = max(p[1] for p in points)
    y_ceil = round((y_max + 0.05) * 10) / 10

    # Fit regression line: y = a * ln(x) + b
    a, b, r = fit_log_linear(points)

    # Generate trend line sample points across the x range
    x_min = min(p[0] for p in points)
    x_max = max(p[0] for p in points)
    trend_points = []
    log_min = math.log10(x_min)
    log_max = math.log10(x_max)
    for i in range(50):
        lx = log_min + (log_max - log_min) * i / 49
        x = 10 ** lx
        y = a * math.log(x) + b
        trend_points.append((x, y))

    lines = []
    lines.append(r"\begin{tikzpicture}")
    lines.append(r"\begin{axis}[")
    lines.append(r"    width=1.02\columnwidth,")
    lines.append(r"    height=0.65\columnwidth,")
    lines.append(r"    xlabel={Number of candidate commits},")
    lines.append(r"    ylabel={Cost (USD)},")
    lines.append(r"    xmode=log,")
    lines.append(f"    ymin=0, ymax={y_ceil:.1f},")
    lines.append(r"    grid=major,")
    lines.append(r"    grid style={gray!30},")
    lines.append(r"    tick label style={font=\small},")
    lines.append(r"    label style={font=\small},")
    lines.append(r"    legend style={at={(0.97,0.97)}, anchor=north east, font=\small, draw=gray!50},")
    lines.append(r"]")

    lines.append(r"\addplot[")
    lines.append(r"    only marks,")
    lines.append(r"    mark=*,")
    lines.append(r"    mark size=1.5pt,")
    lines.append(r"    draw=blue!70!black,")
    lines.append(r"    fill=blue!50,")
    lines.append(r"    fill opacity=0.5,")
    lines.append(r"    forget plot,")
    lines.append(r"] coordinates {")

    for candidates, cost in points:
        lines.append(f"    ({candidates}, {cost:.6f})")

    lines.append(r"};")

    # Trend line
    lines.append(r"\addplot[")
    lines.append(r"    red, thick, no markers,")
    lines.append(r"] coordinates {")
    for x, y in trend_points:
        lines.append(f"    ({x:.4f}, {y:.6f})")
    lines.append(r"};")
    lines.append(f"\\addlegendentry{{$r = {r:.2f}$}}")

    lines.append(r"\end{axis}")
    lines.append(r"\end{tikzpicture}")

    return "\n".join(lines)


def write_inputable(tikz_body):
    path = os.path.join(OUTPUT_DIR, "scatter_cost_vs_candidates.tex")
    with open(path, "w") as f:
        f.write(tikz_body + "\n")
    print(f"Written: {path}")


def write_standalone(tikz_body):
    path = os.path.join(OUTPUT_DIR, "scatter_cost_vs_candidates_standalone.tex")
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
    tikz_body = generate_tikz_body(points)
    write_inputable(tikz_body)
    write_standalone(tikz_body)


if __name__ == "__main__":
    main()
