#!/bin/bash
# RQ4c: Model and Agent Comparison (Table 8)
# Runs Simple-SZZ-Agent with different models and agents.
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

DATASET="sampled_datasets/DS_LINUX-26_100_42.json"

echo "=== RQ4c: Model and Agent Comparison ==="

DS_RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$DS_RESULTS_DIR"

# Claude Code with different models
echo "--- Claude Code + Haiku ---"
python src/simple_szz_agent.py -d "$DATASET" --model claude-haiku-4-5 $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"
echo "--- Claude Code + Sonnet ---"
python src/simple_szz_agent.py -d "$DATASET" --model claude-sonnet-4-5 $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"
echo "--- Claude Code + Opus ---"
python src/simple_szz_agent.py -d "$DATASET" --model claude-opus-4-5 $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"

# OpenHands with Claude models (requires OpenHands running)
echo "--- OpenHands + Haiku ---"
python src/simple_szz_agent.py -d "$DATASET" --agent openhands --model claude-haiku-4-5  $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"
echo "--- OpenHands + Sonnet ---"
python src/simple_szz_agent.py -d "$DATASET" --agent openhands --model claude-sonnet-4-5  $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"
echo "--- OpenHands + Opus ---"
python src/simple_szz_agent.py -d "$DATASET" --agent openhands --model claude-opus-4-5 $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"

# OpenHands with non-Claude models via OpenRouter
echo "--- OpenHands + Minimax-m2.5 ---"
python src/simple_szz_agent.py -d "$DATASET" --agent openhands --model minimax-m2.5 --backend openrouter $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"
echo "--- OpenHands + GLM-5 ---"
python src/simple_szz_agent.py -d "$DATASET" --agent openhands --model glm-5 --backend openrouter $LIMIT
cp "$(ls -t results/simple_szz_agent_*.json | head -1)" "$DS_RESULTS_DIR/"

# Generate table from pre-computed results
echo "--- Generating Table 8 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_cheaper_models.tex"
