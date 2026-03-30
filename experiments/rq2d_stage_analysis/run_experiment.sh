#!/bin/bash
# RQ2d: Stage Analysis (Table 5)
# Compares Stage 1 only, Stage 2 only, and combined SZZ-Agent.
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

echo "=== RQ2d: Stage Analysis ==="

DS_RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$DS_RESULTS_DIR"

# Stage 1 only + combined SZZ-Agent
echo "--- Stage 1 + Combined SZZ-Agent ---"
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX-26_100_42.json $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
cp "$STAGE1_OUTPUT" "$DS_RESULTS_DIR/"
python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" $LIMIT
cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"

# Stage 2 only (no stage 1 candidates, all entries go to stage 2)
echo "--- Stage 2 Only ---"
python src/szz_agent_stage_02.py -d sampled_datasets/DS_LINUX-26_100_42.json $LIMIT
cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"

# Generate table from pre-computed results
echo "--- Generating Table 5 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_stage_01_vs_stage_02.tex"
