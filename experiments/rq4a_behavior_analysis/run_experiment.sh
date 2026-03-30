#!/bin/bash
# RQ4a: Behavior Analysis (Figures 3-5)
# Runs Simple-SZZ-Agent on DS_LINUX-26, then generates scatter plots and bar charts.
#
# Usage:
#   bash run_experiment.sh          # Full run
#   bash run_experiment.sh --quick  # Quick check (2 samples)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

LIMIT=""
if [ "${1:-}" = "--quick" ]; then
    echo "[QUICK] Running with --limit 2"
    LIMIT="--limit 2"
fi

# Load environment
set -a; source .env; set +a

echo "=== RQ4a: Behavior Analysis ==="

# Run Simple-SZZ-Agent on DS_LINUX-26
echo "--- Running Simple-SZZ-Agent on DS_LINUX-26 ---"
python src/simple_szz_agent.py -d sampled_datasets/DS_LINUX-26_100_42.json $LIMIT

# Copy results to experiment results directory
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"
LATEST=$(ls -t results/simple_szz_agent_*.json | head -1)
if [ -n "$LATEST" ]; then
    cp "$LATEST" "$RESULTS_DIR/"
    echo "  -> Copied to $RESULTS_DIR/"
fi

# Generate figures
echo "--- Generating Figures 3-5 ---"
python "$SCRIPT_DIR/generate_figures.py" --use-latest-results
echo "Done. Figures written to $SCRIPT_DIR/figures/"
