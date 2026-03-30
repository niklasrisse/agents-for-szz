#!/usr/bin/env python3
"""Generate a random sample of C entries from the DS_GITHUB dataset.

This script filters entries from DS_GITHUB.json that have C as their language
and samples a subset of them.
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
DATA_FILE = PROJECT_ROOT / "data/DS_GITHUB.json"
OUTPUT_DIR = PROJECT_ROOT / "sampled_datasets"


def main():
    # Load dataset
    print(f"Loading dataset from {DATA_FILE}...")
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)

    print(f"Found {len(data)} entries in dataset")

    # Filter for C entries
    c_entries = [
        entry for entry in data
        if "c" in [lang.lower() for lang in entry.get("language", [])]
    ]

    print(f"Found {len(c_entries)} C entries")

    # Sample
    random.seed(RANDOM_SEED)

    if len(c_entries) <= SAMPLE_SIZE:
        sample = c_entries
        print(f"Dataset has only {len(c_entries)} C entries, using all of them")
    else:
        sample = random.sample(c_entries, SAMPLE_SIZE)
        print(f"Sampled {SAMPLE_SIZE} entries with seed {RANDOM_SEED}")

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"DS_GITHUB-c_{len(sample)}_{RANDOM_SEED}.json"

    with open(output_file, 'w') as f:
        json.dump(sample, f, indent=2)

    print(f"Saved sample to {output_file}")


if __name__ == "__main__":
    main()
