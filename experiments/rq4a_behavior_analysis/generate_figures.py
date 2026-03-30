#!/usr/bin/env python3
"""Generate all behavior analysis figures."""
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
scripts = [
    "generate_f1_vs_candidates_scatter.py",
    "generate_cost_vs_candidates_scatter.py",
    "generate_tokens_vs_candidates_scatter.py",
    "generate_toolcalls_vs_candidates_scatter.py",
    "generate_toolcalls_per_tool_bar.py",
    "generate_grep_source_bar.py",
]

# Forward --use-latest-results flag to sub-scripts
extra_args = ["--use-latest-results"] if "--use-latest-results" in sys.argv else []

for script in scripts:
    print(f"Running {script}...")
    subprocess.run([sys.executable, str(SCRIPT_DIR / script)] + extra_args, check=True)

print("All figures generated in figures/")
