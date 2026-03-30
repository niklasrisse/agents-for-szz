#!/bin/bash
# Generate all tables and figures from pre-computed results.
# No API calls required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PASSED=0
FAILED=0

run() {
    local name="$1"
    local script="$2"
    echo -n "[$name] "
    if python "$script" > /dev/null 2>&1; then
        echo "OK"
        PASSED=$((PASSED + 1))
    else
        echo "FAILED"
        FAILED=$((FAILED + 1))
    fi
}

echo "========================================"
echo "Generating all tables and figures"
echo "========================================"
echo

run "RQ1  Table 1 (Effectiveness)"          experiments/rq1_effectiveness/generate_table.py
run "RQ2a Table 2 (Data Leakage)"           experiments/rq2a_data_leakage/generate_table.py
run "RQ2b Table 3 (LLM Backbone)"           experiments/rq2b_llm_backbone/generate_table.py
run "RQ2c Table 4 (Context Ablation)"       experiments/rq2c_context_ablation/generate_table.py
run "RQ2d Table 5 (Stage Analysis)"         experiments/rq2d_stage_analysis/generate_table.py
run "RQ2e Table 6 (Threshold)"             experiments/rq2e_threshold/generate_table.py
run "RQ3  Table 7 (Agent Comparison)"       experiments/rq3_simple_agent/generate_table.py
run "RQ4a Figures 3-5 (Behavior Analysis)"  experiments/rq4a_behavior_analysis/generate_figures.py
run "RQ4b Figure 6 (Error Analysis)"        experiments/rq4b_error_analysis/generate_figure.py
run "RQ4c Table 8 (Models & Agents)"        experiments/rq4c_models_agents/generate_table.py

echo
echo "========================================"
echo "Done: $PASSED passed, $FAILED failed"
echo "========================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
