"""
Generate a TikZ Sankey diagram: error analysis of simple SZZ agent predictions.

Tracks 100 fix commits through prediction outcomes:
  Level 1: Correct (87) vs Incorrect (13)
  Level 2: BIC not in candidate set (4) vs BIC in candidate set (9)
  Level 3: Incorrect analysis (3), Questionable label (3), Near miss (2), Could not determine (1)

Hardcoded counts from deep error analysis (generate_deep_error_analysis.py).

Produces:
  - figures/sankey_error_analysis.tex  (inputable in a paper)
  - figures/sankey_error_analysis_standalone.tex  (compilable with pdflatex)
"""

import math
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = str(SCRIPT_DIR / "figures")

# Hardcoded counts from deep error analysis
TOTAL = 100
CORRECT = 87
INCORRECT = 13
BIC_NOT_IN_CANDIDATES = 4
BIC_IN_CANDIDATES = 9  # = INCORRECT - BIC_NOT_IN_CANDIDATES
INCORRECT_ANALYSIS = 3
QUESTIONABLE_LABEL = 3
NEAR_MISS = 2
COULD_NOT_DETERMINE = 1


def h(n):
    """Height for n items - sqrt scaling with minimum."""
    return max(0.2, math.sqrt(n) * 0.275)


def generate_tikz_body():
    """Generate a horizontal Sankey diagram with 4 levels."""

    total_h = h(TOTAL)
    correct_h = h(CORRECT)
    incorrect_h = h(INCORRECT)
    no_gt_h = h(BIC_NOT_IN_CANDIDATES)
    gt_in_h = h(BIC_IN_CANDIDATES)
    wrong_h = h(INCORRECT_ANALYSIS)
    quest_h = h(QUESTIONABLE_LABEL)
    near_h = h(NEAR_MISS)
    cnd_h = h(COULD_NOT_DETERMINE)

    col_x = [0, 4.5, 9.0, 13.5]
    bar_w = 1.8
    gap = 0.25

    # Column 0
    c0_bot, c0_top = 0, total_h

    # Column 1: correct (top), incorrect (bottom)
    c1_total = correct_h + incorrect_h + gap
    c1_base = (total_h - c1_total) / 2
    c1_incorrect_bot = c1_base
    c1_incorrect_top = c1_base + incorrect_h
    c1_correct_bot = c1_incorrect_top + gap
    c1_correct_top = c1_correct_bot + correct_h

    # Column 2: no_gt (top), gt_in (bottom) -- centered on incorrect bar
    c2_total = no_gt_h + gt_in_h + gap
    c2_base = c1_incorrect_bot + (incorrect_h - c2_total) / 2
    c2_gt_in_bot = c2_base
    c2_gt_in_top = c2_base + gt_in_h
    c2_no_gt_bot = c2_gt_in_top + gap
    c2_no_gt_top = c2_no_gt_bot + no_gt_h

    # Column 3: breakdown of gt_in (bottom to top: wrong, near, questionable)
    c3_bars = []
    cats = [
        (cnd_h, COULD_NOT_DETERMINE, "clCouldNotDet", "could not\\\\[-2pt]determine"),
        (wrong_h, INCORRECT_ANALYSIS, "clWrongSel", "incorrect\\\\[-2pt]analysis"),
        (near_h, NEAR_MISS, "clNearMiss", "near miss\\\\[-2pt]($\\leq$3 positions)"),
        (quest_h, QUESTIONABLE_LABEL, "clQuestionable", "questionable\\\\[-2pt]ground truth label"),
    ]
    c3_total_h = sum(ch for ch, _, _, _ in cats) + gap * (len(cats) - 1)
    c3_base = c2_gt_in_bot + (gt_in_h - c3_total_h) / 2
    cur_y = c3_base
    for bar_h, count, color, label in cats:
        c3_bars.append({"bot": cur_y, "top": cur_y + bar_h,
                        "count": count, "color": color, "label": label, "height": bar_h})
        cur_y += bar_h + gap

    def band(x0, b0, t0, x1, b1, t1, color, opacity="0.30"):
        mx = (x0 + x1) / 2
        return (
            rf"\fill[{color}, opacity={opacity}] "
            rf"({x0:.3f},{b0:.3f}) .. controls ({mx:.3f},{b0:.3f}) and ({mx:.3f},{b1:.3f}) .. ({x1:.3f},{b1:.3f}) "
            rf"-- ({x1:.3f},{t1:.3f}) .. controls ({mx:.3f},{t1:.3f}) and ({mx:.3f},{t0:.3f}) .. ({x0:.3f},{t0:.3f}) "
            rf"-- cycle;"
        )

    def rect(x, bot, top, color, opacity="0.75"):
        return rf"\fill[{color}, opacity={opacity}, rounded corners=1pt] ({x:.3f},{bot:.3f}) rectangle ({x + bar_w:.3f},{top:.3f});"

    lines = []
    lines.append(r"\begin{tikzpicture}[x=1cm, y=1cm]")
    lines.append("")

    lines.append(r"% Color definitions")
    lines.append(r"\definecolor{clCorrect}{HTML}{2E8B57}")     # Sea green
    lines.append(r"\definecolor{clIncorrect}{HTML}{CD5C5C}")   # Indian red
    lines.append(r"\definecolor{clNoGT}{HTML}{E8963E}")        # Orange
    lines.append(r"\definecolor{clQuestionable}{HTML}{9B7DC4}") # Purple
    lines.append(r"\definecolor{clNearMiss}{HTML}{D4A843}")    # Gold
    lines.append(r"\definecolor{clWrongSel}{HTML}{B84040}")    # Dark red
    lines.append(r"\definecolor{clCouldNotDet}{HTML}{7B8FA1}")  # Slate blue
    lines.append(r"\definecolor{clTotal}{HTML}{6B7B8D}")       # Steel gray
    lines.append("")

    # --- Bars ---
    lines.append(r"% Column 0: All fix commits")
    lines.append(rect(col_x[0], c0_bot, c0_top, "clTotal"))
    lines.append("")

    lines.append(r"% Column 1: Correct / Incorrect")
    lines.append(rect(col_x[1], c1_correct_bot, c1_correct_top, "clCorrect"))
    lines.append(rect(col_x[1], c1_incorrect_bot, c1_incorrect_top, "clIncorrect"))
    lines.append("")

    lines.append(r"% Column 2: BIC not in candidates / BIC in candidates")
    lines.append(rect(col_x[2], c2_no_gt_bot, c2_no_gt_top, "clNoGT"))
    lines.append(rect(col_x[2], c2_gt_in_bot, c2_gt_in_top, "clWrongSel"))
    lines.append("")

    lines.append(r"% Column 3: Detailed breakdown")
    for bar in c3_bars:
        lines.append(rect(col_x[3], bar["bot"], bar["top"], bar["color"]))
    lines.append("")

    # --- Bands ---
    # Col0 -> Col1
    frac_incorrect = incorrect_h / (incorrect_h + correct_h)
    c0_split = c0_bot + total_h * frac_incorrect

    lines.append(r"% Bands: Col 0 -> Col 1")
    lines.append(band(col_x[0] + bar_w, c0_split, c0_top, col_x[1], c1_correct_bot, c1_correct_top, "clCorrect"))
    lines.append(band(col_x[0] + bar_w, c0_bot, c0_split, col_x[1], c1_incorrect_bot, c1_incorrect_top, "clIncorrect"))
    lines.append("")

    # Col1 (incorrect) -> Col2
    frac_gt_in = gt_in_h / (gt_in_h + no_gt_h)
    c1i_split = c1_incorrect_bot + incorrect_h * frac_gt_in

    lines.append(r"% Bands: Col 1 -> Col 2")
    lines.append(band(col_x[1] + bar_w, c1i_split, c1_incorrect_top, col_x[2], c2_no_gt_bot, c2_no_gt_top, "clNoGT"))
    lines.append(band(col_x[1] + bar_w, c1_incorrect_bot, c1i_split, col_x[2], c2_gt_in_bot, c2_gt_in_top, "clWrongSel"))
    lines.append("")

    # Col2 (gt_in) -> Col3
    lines.append(r"% Bands: Col 2 (BIC in candidates) -> Col 3")
    total_bar_h = sum(b["height"] for b in c3_bars)
    src_bot = c2_gt_in_bot
    for bar in c3_bars:
        frac = bar["height"] / total_bar_h
        src_top = src_bot + gt_in_h * frac
        lines.append(band(
            col_x[2] + bar_w, src_bot, src_top,
            col_x[3], bar["bot"], bar["top"], bar["color"]
        ))
        src_bot = src_top
    lines.append("")

    # --- Labels ---
    lines.append(r"% Labels")
    c0_mid = (c0_bot + c0_top) / 2
    lines.append(rf"\node[anchor=east, font=\small] at ({col_x[0] - 0.15:.3f},{c0_mid:.3f}) "
                 r"{\textbf{100} fix commits};")

    lines.append(rf"\node[anchor=south, font=\small, text=clCorrect!90!black] at "
                 rf"({col_x[1] + bar_w/2:.3f},{c1_correct_top + 0.10:.3f}) "
                 rf"{{\textbf{{{CORRECT}}} correct}};")
    lines.append(rf"\node[anchor=north, font=\small, text=clIncorrect!90!black] at "
                 rf"({col_x[1] + bar_w/2:.3f},{c1_incorrect_bot - 0.10:.3f}) "
                 rf"{{\textbf{{{INCORRECT}}} incorrect}};")

    lines.append(rf"\node[anchor=south, font=\small, text=clNoGT!90!black, text width=3.5cm, align=center] at "
                 rf"({col_x[2] + bar_w/2:.3f},{c2_no_gt_top + 0.10:.3f}) "
                 rf"{{\textbf{{{BIC_NOT_IN_CANDIDATES}}} BIC not in\\[-2pt]candidate set}};")
    lines.append(rf"\node[anchor=north, font=\small, text=clWrongSel!90!black, text width=3.5cm, align=center] at "
                 rf"({col_x[2] + bar_w/2:.3f},{c2_gt_in_bot - 0.10:.3f}) "
                 rf"{{\textbf{{{BIC_IN_CANDIDATES}}} BIC in candidate\\[-2pt]set, wrong selection}};")

    for bar in c3_bars:
        mid = (bar["bot"] + bar["top"]) / 2
        lines.append(rf"\node[anchor=west, font=\small, text={bar['color']}!90!black, text width=3.5cm] at "
                     rf"({col_x[3] + bar_w + 0.15:.3f},{mid:.3f}) "
                     rf"{{\textbf{{{bar['count']}}} {bar['label']}}};")

    lines.append("")
    lines.append(r"\end{tikzpicture}")

    return "\n".join(lines)


def write_inputable(tikz_body):
    path = os.path.join(OUTPUT_DIR, "sankey_error_analysis.tex")
    with open(path, "w") as f:
        f.write(tikz_body + "\n")
    print(f"Written: {path}")


def write_standalone(tikz_body):
    path = os.path.join(OUTPUT_DIR, "sankey_error_analysis_standalone.tex")
    content = r"""\documentclass[border=5pt]{standalone}
\usepackage{tikz}
\usepackage[dvipsnames]{xcolor}
\begin{document}
""" + tikz_body + "\n" + r"""\end{document}
"""
    with open(path, "w") as f:
        f.write(content)
    print(f"Written: {path}")


def main():
    print("Error analysis breakdown (hardcoded from deep analysis):")
    print(f"  Total: {TOTAL}")
    print(f"  Correct: {CORRECT}")
    print(f"  Incorrect: {INCORRECT}")
    print(f"    BIC not in candidates: {BIC_NOT_IN_CANDIDATES}")
    print(f"    BIC in candidates, wrong selection: {BIC_IN_CANDIDATES}")
    print(f"      Incorrect analysis: {INCORRECT_ANALYSIS}")
    print(f"      Questionable label: {QUESTIONABLE_LABEL}")
    print(f"      Near miss: {NEAR_MISS}")
    print(f"      Could not determine: {COULD_NOT_DETERMINE}")

    tikz_body = generate_tikz_body()
    write_inputable(tikz_body)
    write_standalone(tikz_body)


if __name__ == "__main__":
    main()
