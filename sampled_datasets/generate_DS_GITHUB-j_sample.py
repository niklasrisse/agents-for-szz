#!/usr/bin/env python3
"""Extract Java samples from the DS_GITHUB dataset.

This script filters entries from DS_GITHUB.json that have Java as their language
and saves them to a new file.
"""

import json
from pathlib import Path

# Project root (one level up from sampled_datasets/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Paths
DATA_FILE = PROJECT_ROOT / "data/DS_GITHUB.json"
OUTPUT_DIR = PROJECT_ROOT / "sampled_datasets"
OUTPUT_FILE = OUTPUT_DIR / "DS_GITHUB-j.json"


def main():
    # Load dataset
    print(f"Loading dataset from {DATA_FILE}...")
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)

    print(f"Found {len(data)} entries in dataset")

    # Filter for Java entries
    java_entries = [
        entry for entry in data
        if "java" in [lang.lower() for lang in entry.get("language", [])]
    ]

    print(f"Found {len(java_entries)} Java entries")

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(java_entries, f, indent=2)

    print(f"Saved Java samples to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
