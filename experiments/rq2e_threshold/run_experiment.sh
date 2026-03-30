#!/bin/bash
# RQ2e: Commit Identifier Threshold Analysis (Table 6)
# Runs SZZ-Agent with different candidate selection thresholds.
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

echo "=== RQ2e: Commit Identifier Threshold Analysis ==="

DS_RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$DS_RESULTS_DIR"

# Run Stage 1 once (shared across all thresholds)
echo "--- Stage 1 ---"
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX-26_100_42.json $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
cp "$STAGE1_OUTPUT" "$DS_RESULTS_DIR/"

# Run Stage 2 with different thresholds
for THRESHOLD in 9 33 129 513 1000000; do
    echo "--- Stage 2 with threshold=$THRESHOLD ---"
    python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" --threshold $THRESHOLD $LIMIT
    cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"
done

# Generate table from pre-computed results
echo "--- Generating Table 6 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_commit_identifier_threshold.tex"
