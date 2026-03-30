#!/bin/bash
# RQ3: Simple-SZZ-Agent Comparison (Table 7)
# Runs Simple-SZZ-Agent on DS_LINUX-26, DS_GITHUB-c, and DS_GITHUB-j.
# Baselines and SZZ-Agent results are pre-computed and included in results/.
#
# Usage:
#   bash run_experiment.sh          # Full run
#   bash run_experiment.sh --quick  # Quick check (2 samples per dataset)
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

echo "=== RQ3: Simple-SZZ-Agent Comparison ==="

# DS_LINUX (200 samples)
echo "--- DS_LINUX ---"
python src/simple_szz_agent.py -d sampled_datasets/DS_LINUX_200_42.json $LIMIT
DS_RESULTS_DIR="$SCRIPT_DIR/results/DS_LINUX"
mkdir -p "$DS_RESULTS_DIR"
LATEST=$(ls -t results/simple_szz_agent_*.json | head -1)
[ -n "$LATEST" ] && cp "$LATEST" "$DS_RESULTS_DIR/"

# DS_GITHUB-c (100 samples)
echo "--- DS_GITHUB-c ---"
python src/simple_szz_agent.py -d sampled_datasets/DS_GITHUB-c_100_42.json $LIMIT
DS_RESULTS_DIR="$SCRIPT_DIR/results/DS_GITHUB-c"
mkdir -p "$DS_RESULTS_DIR"
LATEST=$(ls -t results/simple_szz_agent_*.json | head -1)
[ -n "$LATEST" ] && cp "$LATEST" "$DS_RESULTS_DIR/"

# DS_GITHUB-j (75 samples)
echo "--- DS_GITHUB-j ---"
python src/simple_szz_agent.py -d sampled_datasets/DS_GITHUB-j.json $LIMIT
DS_RESULTS_DIR="$SCRIPT_DIR/results/DS_GITHUB-j"
mkdir -p "$DS_RESULTS_DIR"
LATEST=$(ls -t results/simple_szz_agent_*.json | head -1)
[ -n "$LATEST" ] && cp "$LATEST" "$DS_RESULTS_DIR/"

# Generate table from pre-computed results
echo "--- Generating Table 7 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_agent_comparison.tex"
