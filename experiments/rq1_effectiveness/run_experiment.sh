#!/bin/bash
# RQ1: Effectiveness of SZZ-Agent (Table 1)
# Runs SZZ-Agent (stage 01 + stage 02) on DS_LINUX, DS_GITHUB-c, and DS_GITHUB-j.
# Baselines are pre-computed and included in results/.
# Optionally runs baselines with --baselines flag.
#
# Usage:
#   bash run_experiment.sh                      # Full run (SZZ-Agent only)
#   bash run_experiment.sh --quick              # Quick check (2 samples per dataset)
#   bash run_experiment.sh --baselines          # Full run including baselines
#   bash run_experiment.sh --quick --baselines  # Quick check including baselines
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

LIMIT=""
RUN_BASELINES=false
for arg in "$@"; do
    case "$arg" in
        --quick) LIMIT="--limit 2"; echo "[QUICK] Running with --limit 2" ;;
        --baselines) RUN_BASELINES=true ;;
    esac
done

# Load environment
set -a; source .env; set +a

DATASETS=(
    "DS_LINUX:sampled_datasets/DS_LINUX_200_42.json"
    "DS_GITHUB-c:sampled_datasets/DS_GITHUB-c_100_42.json"
    "DS_GITHUB-j:sampled_datasets/DS_GITHUB-j.json"
)

# --- Baselines (optional) ---
if [ "$RUN_BASELINES" = true ]; then
    echo "=== RQ1: Running Baselines ==="

    # pyszz_v2-based baselines (B-SZZ, AG-SZZ, L-SZZ, R-SZZ, MA-SZZ, RA-SZZ)
    PYSZZ_BASELINES=(baseline_bszz baseline_agszz baseline_lszz baseline_rszz baseline_maszz baseline_raszz)

    for ds_entry in "${DATASETS[@]}"; do
        DS_NAME="${ds_entry%%:*}"
        SAMPLE_FILE="${ds_entry##*:}"
        echo "--- $DS_NAME pyszz_v2 baselines ---"

        DS_RESULTS_DIR="$SCRIPT_DIR/results/$DS_NAME"
        mkdir -p "$DS_RESULTS_DIR"

        for baseline in "${PYSZZ_BASELINES[@]}"; do
            echo "  Running $baseline on $DS_NAME..."
            python "src/baselines/${baseline}.py" --sample-file "$SAMPLE_FILE" --skip-clone $LIMIT

            # Copy latest result to experiment results directory
            LATEST=$(ls -t results/${baseline}_*.json | head -1)
            if [ -n "$LATEST" ]; then
                cp "$LATEST" "$DS_RESULTS_DIR/"
                echo "  -> Copied to $DS_RESULTS_DIR/"
            fi
        done
    done

    # V-SZZ baseline (requires Java for ASTMapEval.jar)
    echo "--- V-SZZ baseline ---"
    if command -v java &> /dev/null; then
        for ds_entry in "${DATASETS[@]}"; do
            DS_NAME="${ds_entry%%:*}"
            SAMPLE_FILE="${ds_entry##*:}"
            DS_RESULTS_DIR="$SCRIPT_DIR/results/$DS_NAME"
            mkdir -p "$DS_RESULTS_DIR"

            echo "  Running V-SZZ on $DS_NAME..."
            python src/baselines/baseline_vszz.py --sample-file "$SAMPLE_FILE" --skip-clone $LIMIT

            LATEST=$(ls -t results/baseline_vszz_*.json | head -1)
            if [ -n "$LATEST" ]; then
                cp "$LATEST" "$DS_RESULTS_DIR/"
                echo "  -> Copied to $DS_RESULTS_DIR/"
            fi
        done
    else
        echo "  [SKIP] Java not found. V-SZZ requires Java for ASTMapEval.jar. Install Java 11+ to run."
    fi

    # LLM4SZZ baseline (requires API keys)
    echo "--- LLM4SZZ baseline ---"
    if [ -n "${OPENAI_API_KEY:-}" ] && [ "$OPENAI_API_KEY" != "your_openai_api_key_here" ]; then
        for ds_entry in "${DATASETS[@]}"; do
            DS_NAME="${ds_entry%%:*}"
            SAMPLE_FILE="${ds_entry##*:}"
            DS_RESULTS_DIR="$SCRIPT_DIR/results/$DS_NAME"
            mkdir -p "$DS_RESULTS_DIR"

            echo "  Running LLM4SZZ on $DS_NAME..."
            python src/baselines/baseline_llm4szz.py --sample "$SAMPLE_FILE" --skip-clone $LIMIT

            LATEST=$(ls -t results/baseline_llm4szz_*.json | head -1)
            if [ -n "$LATEST" ]; then
                cp "$LATEST" "$DS_RESULTS_DIR/"
                echo "  -> Copied to $DS_RESULTS_DIR/"
            fi
        done
    else
        echo "  [SKIP] OPENAI_API_KEY not set. LLM4SZZ requires an OpenAI API key. Set it in .env to run."
    fi
fi

# --- SZZ-Agent ---
echo "=== RQ1: SZZ-Agent Effectiveness ==="

for ds_entry in "${DATASETS[@]}"; do
    DS_NAME="${ds_entry%%:*}"
    SAMPLE_FILE="${ds_entry##*:}"
    echo "--- $DS_NAME ---"

    python src/szz_agent_stage_01.py -s "$SAMPLE_FILE" $LIMIT
    STAGE1_OUTPUT=$(ls -t results/szz_agent_stage_01_*.json | head -1)
    python src/szz_agent_stage_02.py -d "$STAGE1_OUTPUT" $LIMIT

    # Copy results to experiment results directory
    DS_RESULTS_DIR="$SCRIPT_DIR/results/$DS_NAME"
    mkdir -p "$DS_RESULTS_DIR"
    for prefix in szz_agent_stage_01 szz_agent_stage_02; do
        LATEST=$(ls -t results/${prefix}_*.json | head -1)
        if [ -n "$LATEST" ]; then
            cp "$LATEST" "$DS_RESULTS_DIR/"
        fi
    done
done

# Generate table
echo "--- Generating Table 1 ---"
python "$SCRIPT_DIR/generate_table.py" --use-latest-results
echo "Done. Table written to $SCRIPT_DIR/tables/table_effectiveness.tex"
