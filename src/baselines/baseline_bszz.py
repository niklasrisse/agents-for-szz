#!/usr/bin/env python3
"""Baseline B-SZZ (Basic SZZ) evaluation script.

This script runs B-SZZ from pyszz_v2 on a sampled dataset and evaluates the results.

B-SZZ is the original SZZ algorithm that uses git blame to find bug-inducing commits
without any filtering or enhancements.

Reference:
    J. Sliwerski, T. Zimmermann, and A. Zeller, "When do changes induce fixes?"
    in ACM SIGSOFT Software Engineering Notes, vol. 30, 2005.

Usage:
    python baseline_bszz.py
    python baseline_bszz.py --limit 10  # Process only 10 samples
    python baseline_bszz.py --sample-file sampled_datasets/DS_GITHUB-j.json
"""

import argparse
import json
import logging as log
import os
import sys
from datetime import datetime
from pathlib import Path
from time import time as ts
from typing import Dict, List

# Add pyszz_v2 to path
PYSZZ_DIR = Path(__file__).parent / "pyszz_v2"
sys.path.insert(0, str(PYSZZ_DIR))

# Configure logging before importing pyszz modules
log.basicConfig(level=log.INFO, format='%(asctime)s :: %(funcName)s - %(levelname)s :: %(message)s')
log.getLogger('pydriller').setLevel(log.WARNING)

# Import pyszz modules
from szz.b_szz import BaseSZZ

# =============================================================================
# CONFIGURATION
# =============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_FILE = PROJECT_ROOT / "sampled_datasets/linux_200_42.json"
RESULTS_DIR = PROJECT_ROOT / "results"
REPOS_DIR = PROJECT_ROOT / "repos"

# B-SZZ config (basic SZZ with no filtering)
BSZZ_CONFIG = {
    "szz_name": "b",
    "issue_date_filter": False,
}


from utils import ensure_repos_exist


# Import shared evaluation utilities
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evaluation_utils import evaluate_results, print_summary


def run_bszz_on_entry(
    entry: Dict,
    repos_dir: Path,
    config: Dict
) -> List[str]:
    """Run B-SZZ on a single entry and return predicted BICs."""
    repo_name = entry["repo_name"]
    repo_url = f'https://test:test@github.com/{repo_name}.git'
    fix_commit = entry["fix_commit_hash"]

    try:
        b_szz = BaseSZZ(
            repo_full_name=repo_name,
            repo_url=repo_url,
            repos_dir=str(repos_dir)
        )

        imp_files = b_szz.get_impacted_files(
            fix_commit_hash=fix_commit,
            file_ext_to_parse=config.get('file_ext_to_parse'),
            only_deleted_lines=True
        )

        bug_inducing_commits = b_szz.find_bic(
            fix_commit_hash=fix_commit,
            impacted_files=imp_files,
            issue_date_filter=config.get('issue_date_filter', False),
            issue_date=None
        )

        return [bic.hexsha for bic in bug_inducing_commits if bic]

    except Exception as e:
        log.error(f"Error processing {repo_name} {fix_commit}: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Run B-SZZ baseline evaluation")
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of entries to process"
    )
    parser.add_argument(
        "--sample-file", "-s",
        type=str,
        default=str(SAMPLE_FILE),
        help=f"Path to sample file (default: {SAMPLE_FILE})"
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip cloning missing repositories (fail if repo not found)"
    )
    args = parser.parse_args()

    sample_file = Path(args.sample_file).resolve()
    os.chdir(Path(__file__).parent.parent)

    print("=" * 70)
    print("              B-SZZ (Basic SZZ) Baseline Evaluation")
    print("=" * 70)
    print("\nNote: B-SZZ is the original SZZ algorithm using git blame")
    print("without any filtering or enhancements.")

    # Create repos directory if it doesn't exist
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    if not sample_file.exists():
        print(f"ERROR: Sample file not found at {sample_file}")
        return 1

    print(f"\nLoading sample data from {sample_file}...")
    with open(sample_file, 'r') as f:
        sample_data = json.load(f)

    print(f"Loaded {len(sample_data)} entries")

    if args.limit and args.limit > 0:
        sample_data = sample_data[:args.limit]
        print(f"Limited to {len(sample_data)} entries")

    # Ensure all repositories exist (clone if needed)
    if not args.skip_clone:
        failed_repos = ensure_repos_exist(sample_data, REPOS_DIR)
        if failed_repos:
            # Filter out entries for repos that failed to clone
            original_count = len(sample_data)
            sample_data = [e for e in sample_data if e["repo_name"] not in failed_repos]
            print(f"\nFiltered out {original_count - len(sample_data)} entries due to missing repos")
            print(f"Proceeding with {len(sample_data)} entries")

    results = []
    start_time = ts()

    for idx, entry in enumerate(sample_data, 1):
        entry_id = entry.get("id", "unknown")
        fix_commit = entry["fix_commit_hash"]
        gt_bics = entry.get("bug_commit_hash", [])

        print(f"\n[{idx}/{len(sample_data)}] Processing entry {entry_id} (fix: {fix_commit[:8]})")

        predicted_bics = run_bszz_on_entry(entry, REPOS_DIR, BSZZ_CONFIG)

        result = {
            "id": entry_id,
            "fix_commit_hash": fix_commit,
            "ground_truth_bics": gt_bics,
            "predicted_bics": predicted_bics,
            "num_predictions": len(predicted_bics)
        }
        results.append(result)

        if idx % 10 == 0 or idx == len(sample_data):
            elapsed = ts() - start_time
            avg_per_entry = elapsed / idx
            print(f"\n  --- Progress: {idx}/{len(sample_data)}, "
                  f"elapsed: {elapsed:.1f}s, avg: {avg_per_entry:.1f}s/entry ---")

    print("\n\nEvaluating results...")
    summary = evaluate_results(results)
    print_summary(summary, "B-SZZ")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = RESULTS_DIR / f"baseline_bszz_{timestamp}.json"

    output_data = {
        "metadata": {
            "timestamp": timestamp,
            "sample_file": str(sample_file),
            "sample_size": len(sample_data),
            "algorithm": "B-SZZ",
            "config": BSZZ_CONFIG
        },
        "summary": summary,
        "results": results
    }

    with open(results_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\nDetailed results saved to: {results_file}")
    print("\nDone!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
