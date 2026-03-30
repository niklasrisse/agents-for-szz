#!/usr/bin/env python3
"""Generate a random sample from the Linux dataset.

This script samples entries from the full dataset and saves them to a new file.
"""

import json
import random
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================
SAMPLE_SIZE = 100
RANDOM_SEED = 42

# Project root (one level up from sampled_datasets/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Paths
DATA_FILE = PROJECT_ROOT / "data/DS_LINUX.json"
OUTPUT_DIR = PROJECT_ROOT / "sampled_datasets"


def main():
    # Load dataset
    print(f"Loading dataset from {DATA_FILE}...")
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)

    print(f"Found {len(data)} entries in dataset")

    # Sample
    random.seed(RANDOM_SEED)

    if len(data) <= SAMPLE_SIZE:
        sample = data
        print(f"Dataset has only {len(data)} entries, using all of them")
    else:
        sample = random.sample(data, SAMPLE_SIZE)
        print(f"Sampled {SAMPLE_SIZE} entries with seed {RANDOM_SEED}")

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"DS_LINUX_{len(sample)}_{RANDOM_SEED}.json"

    with open(output_file, 'w') as f:
        json.dump(sample, f, indent=2)

    print(f"Saved sample to {output_file}")


if __name__ == "__main__":
    main()
