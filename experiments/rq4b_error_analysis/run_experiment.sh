#!/bin/bash
# RQ4b: Error Analysis (Figure 6)
# Runs Simple-SZZ-Agent with Sonnet on DS_LINUX-26, then generates Sankey diagram.
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

echo "=== RQ4b: Error Analysis ==="

# Run Simple-SZZ-Agent with Sonnet on DS_LINUX-26
echo "--- Running Simple-SZZ-Agent (Sonnet) on DS_LINUX-26 ---"
python src/simple_szz_agent.py -d sampled_datasets/DS_LINUX-26_100_42.json --model claude-sonnet-4-5 --agent openhands $LIMIT

# Copy results to experiment results directory
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"
LATEST=$(ls -t results/simple_szz_agent_*.json | head -1)
if [ -n "$LATEST" ]; then
    cp "$LATEST" "$RESULTS_DIR/"
    echo "  -> Copied to $RESULTS_DIR/"
fi

# Generate figure (uses hardcoded counts from manual error analysis)
echo "--- Generating Figure 6 ---"
python "$SCRIPT_DIR/generate_figure.py"
echo "Done. Figure written to $SCRIPT_DIR/figures/"
