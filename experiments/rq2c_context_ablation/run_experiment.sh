#!/bin/bash
# RQ2c: Context Ablation Study (Table 4)
# Runs SZZ-Agent with/without commit message and diff context.
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

echo "=== RQ2c: Context Ablation Study ==="

DS_RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$DS_RESULTS_DIR"

# Full SZZ-Agent (reference)
echo "--- Full SZZ-Agent (with message and diff) ---"
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX-26_100_42.json $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" $LIMIT
cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"

# Without message
echo "--- Without fix commit message ---"
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX-26_100_42.json --without-fc-message $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" --without-fc-message $LIMIT
cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"

# Without diff
echo "--- Without fix commit diff ---"
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX-26_100_42.json --without-fc-diff $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" --without-fc-diff $LIMIT
cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"

# Generate table from pre-computed results
echo "--- Generating Table 4 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_without_message_or_diff.tex"
