#!/bin/bash
# RQ2b: LLM Backbone Comparison (Table 3)
# Runs LLM4SZZ baseline with different LLM models + SZZ-Agent.
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

echo "=== RQ2b: LLM Backbone Comparison ==="

DS_RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$DS_RESULTS_DIR"

# Run LLM4SZZ with different models
python src/baselines/baseline_llm4szz.py -s sampled_datasets/DS_LINUX_200_42.json --model gpt-4o $LIMIT
cp "$(ls -t results/baseline_llm4szz_*.json | head -1)" "$DS_RESULTS_DIR/"
python src/baselines/baseline_llm4szz.py -s sampled_datasets/DS_LINUX_200_42.json --model gpt-5.2 $LIMIT
cp "$(ls -t results/baseline_llm4szz_*.json | head -1)" "$DS_RESULTS_DIR/"
python src/baselines/baseline_llm4szz.py -s sampled_datasets/DS_LINUX_200_42.json --model claude-opus-4-5 $LIMIT
cp "$(ls -t results/baseline_llm4szz_*.json | head -1)" "$DS_RESULTS_DIR/"

# Run SZZ-Agent
python src/szz_agent_stage_01.py -s sampled_datasets/DS_LINUX_200_42.json $LIMIT
STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" $LIMIT
cp "$(ls -t results/szz_agent_stage_02_*.json | head -1)" "$DS_RESULTS_DIR/"

# Generate table from pre-computed results
echo "--- Generating Table 3 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_llm4szz_different_models.tex"
