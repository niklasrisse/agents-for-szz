#!/bin/bash
# Regenerate all sampled datasets from the raw data.
# This reproduces the exact samples used in the paper (seed=42).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Generating DS_LINUX sample..."
python generate_DS_LINUX_sample.py

echo "Generating DS_LINUX Data Leakage sample..."
python generate_DS_LINUX-26_sample.py

echo "Generating DS_GITHUB-c sample..."
python generate_DS_GITHUB-c_sample.py

echo "Generating DS_GITHUB-j sample..."
python generate_DS_GITHUB-j_sample.py

echo "All samples generated successfully."
