#!/bin/bash
# RQ2a: Data Leakage Analysis (Table 2)
# Runs SZZ-Agent (stage 01 + stage 02) on DS_LINUX-26 (post-cutoff data).
# Baselines are pre-computed and included in results/.
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

echo "=== RQ2a: Data Leakage Analysis ==="
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX-26_100_42.json $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" $LIMIT

# Copy results to experiment results directory
DS_RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$DS_RESULTS_DIR"
for prefix in szz_agent_stage_01 szz_agent_stage_02; do
    LATEST=$(ls -t results/${prefix}_*.json | head -1)
    if [ -n "$LATEST" ]; then
        cp "$LATEST" "$DS_RESULTS_DIR/"
    fi
done

# Generate table from pre-computed results
echo "--- Generating Table 2 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_data_leakage.tex"
