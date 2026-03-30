#!/usr/bin/env python3
"""Baseline LLM4SZZ evaluation script.

This script runs LLM4SZZ on our sampled dataset by:
1. Converting our sample format to their format
2. Copying to their dataset directory
3. Running llm4szz.py
4. Parsing logs and generating detailed results file

Usage:
    python baseline_llm4szz.py --sample sampled_datasets/linux_5_42.json --test
    python baseline_llm4szz.py --sample sampled_datasets/linux_200_42.json
    python baseline_llm4szz.py --sample sampled_datasets/linux_200_42.json --model claude-opus-4-5
    python baseline_llm4szz.py --sample sampled_datasets/linux_200_42.json --model claude-sonnet-4-5
    python baseline_llm4szz.py --sample sampled_datasets/DS_GITHUB-j.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LLM4SZZ_DIR = Path(__file__).parent / "llm4szz"
DATASET_DIR = LLM4SZZ_DIR / "dataset"
SAVE_LOG_DIR = LLM4SZZ_DIR / "save_logs"
RESULTS_DIR = PROJECT_ROOT / "results"
REPOS_DIR = PROJECT_ROOT / "repos"


from utils import ensure_repos_exist


def convert_sample_to_llm4szz_format(sample_path: Path) -> list:
    """Convert our sample format to llm4szz format.

    Note: llm4szz.py expects 'fix_commit_hash' but cal_statistics_util.py
    expects 'bug_fixing_commit'. We include both for compatibility.
    """
    with open(sample_path, 'r') as f:
        our_data = json.load(f)

    converted = []
    for entry in our_data:
        converted.append({
            "repo_name": entry["repo_name"],
            # For llm4szz.py
            "fix_commit_hash": entry["fix_commit_hash"],
            # For cal_statistics_util.py
            "bug_fixing_commit": entry["fix_commit_hash"],
            "bug_inducing_commit": entry.get("bug_commit_hash", []),
            # Keep original ID for tracking
            "id": entry.get("id", "unknown")
        })

    return converted


def setup_dataset(sample_path: Path):
    """Set up the dataset files for llm4szz from a sample file path."""
    converted_data = convert_sample_to_llm4szz_format(sample_path)
    return setup_dataset_from_data(converted_data)


def setup_dataset_from_data(converted_data: list):
    """Set up the dataset files for llm4szz from already-converted data."""
    # Backup original final_all_dataset.json if it exists
    original_dataset = DATASET_DIR / "final_all_dataset.json"
    backup_path = DATASET_DIR / "final_all_dataset.json.backup"

    if original_dataset.exists() and not backup_path.exists():
        shutil.copy(original_dataset, backup_path)
        print(f"Backed up original dataset to {backup_path}")

    # Write our sample as final_all_dataset.json
    with open(original_dataset, 'w') as f:
        json.dump(converted_data, f, indent=2)
    print(f"Wrote {len(converted_data)} entries to {original_dataset}")

    # Also create a test file for filtering (DS_SAMPLE.json)
    test_file = DATASET_DIR / "DS_SAMPLE.json"
    with open(test_file, 'w') as f:
        json.dump(converted_data, f, indent=2)
    print(f"Wrote test file to {test_file}")

    return converted_data


def clear_logs_for_sample(sample_data: list):
    """Clear existing logs for the sample entries."""
    print("\n" + "=" * 60)
    print("Clearing existing logs...")
    print("=" * 60)

    cleared = 0
    for entry in sample_data:
        repo_name = entry["repo_name"]
        fix_commit = entry["fix_commit_hash"]
        log_dir = SAVE_LOG_DIR / repo_name / fix_commit

        if log_dir.exists():
            shutil.rmtree(log_dir)
            cleared += 1

    print(f"Cleared logs for {cleared} entries")


def run_llm4szz(model: str = "gpt-4o-mini", reasoning: str = "minimal"):
    """Run the llm4szz.py script."""
    print("\n" + "=" * 60)
    print(f"Running LLM4SZZ with model={model}, reasoning={reasoning}...")
    print("=" * 60 + "\n")

    # Need to run from the llm4szz directory for imports to work
    original_cwd = os.getcwd()
    os.chdir(str(LLM4SZZ_DIR))

    # Set environment variables for model configuration
    env = os.environ.copy()
    env["LLM4SZZ_MODEL"] = model
    env["LLM4SZZ_REASONING"] = reasoning

    try:
        result = subprocess.run(
            [sys.executable, "llm4szz.py"],
            capture_output=False,
            env=env
        )
        return result.returncode == 0
    finally:
        os.chdir(original_cwd)


# Import shared evaluation utilities
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from evaluation_utils import evaluate_results, print_summary as print_summary_base


def is_commit_match_substring(predicted: str, ground_truth_list: List[str]) -> bool:
    """Check if ground truth is substring of predicted (matches original llm4szz logic)."""
    for gt in ground_truth_list:
        if gt in predicted:
            return True
    return False


def get_r_commits(repo_name: str, cand_cids: List[str]) -> List[str]:
    """Get the most recent commit(s) from candidates (matches llm4szz logic)."""
    if not cand_cids:
        return []

    import git
    max_date = 0.0
    max_cid = []
    repo_path = PROJECT_ROOT / "repos" / repo_name

    try:
        repo = git.Repo(str(repo_path))
        for cand_cid in cand_cids:
            try:
                date = repo.commit(cand_cid).committed_datetime
                if date.timestamp() > max_date:
                    max_date = date.timestamp()
                    max_cid.clear()
                    max_cid.append(cand_cid)
            except Exception:
                continue
    except Exception:
        return cand_cids[:1] if cand_cids else []

    return max_cid


def extract_predictions_from_logs_per_iteration(repo_name: str, fix_commit: str, repeat_cnt: int) -> List[str]:
    """Extract predicted BICs from llm4szz log file for a specific iteration.

    This matches the original cal_statistics_util.py logic exactly.
    """
    log_path = SAVE_LOG_DIR / repo_name / fix_commit / f"llm4szz{repeat_cnt}.json"

    if not log_path.exists():
        return []

    try:
        with open(log_path, 'r') as f:
            logs = json.load(f)
    except Exception:
        return []

    s2_final_cids = []
    s1_ranked_stmts_infos = []
    s1_llm_file_final_cids = []
    can_determine = True

    for log in logs:
        if isinstance(log, dict):
            if "can_determine" in log:
                can_determine = can_determine and log["can_determine"]

            if "s2_final_cid" in log:
                s2_final_cids.extend(log["s2_final_cid"])

            if "s1_ranked_stmts_infos" in log:
                s1_ranked_stmts_infos.extend(log["s1_ranked_stmts_infos"][:5])

            if "s1_llm_file_final_cids" in log:
                s1_llm_file_final_cids.extend(log["s1_llm_file_final_cids"])

    # Extract s1 final cids from ranked stmts
    s1_final_cids = []
    for rank_info in s1_ranked_stmts_infos:
        if "induce_cid" in rank_info:
            s1_final_cids.append(rank_info["induce_cid"])

    s1_final_cids = list(set(s1_final_cids))
    s1_final_cids = get_r_commits(repo_name, s1_final_cids)

    # Determine final predictions (matches original logic)
    final_cids = []
    if can_determine:
        s2_final_cids = list(set(s2_final_cids))
        s2_final_cids = get_r_commits(repo_name, s2_final_cids)
        final_cids = s2_final_cids

    if len(final_cids) == 0:
        final_cids = s1_final_cids

    return final_cids


def extract_predictions_from_logs(repo_name: str, fix_commit: str) -> List[str]:
    """Extract predicted BICs from llm4szz log file (single iteration)."""
    # Only read iteration 0 (single run mode)
    return extract_predictions_from_logs_per_iteration(repo_name, fix_commit, 0)




def generate_results_file(sample_data: List[Dict], sample_path: Path, model: str = "gpt-4o-mini", reasoning: str = "low") -> Path:
    """Generate detailed results file from llm4szz logs."""
    print("\n" + "=" * 60)
    print("Generating detailed results file...")
    print("=" * 60 + "\n")

    results = []

    for entry in sample_data:
        entry_id = entry.get("id", "unknown")
        repo_name = entry["repo_name"]
        fix_commit = entry["fix_commit_hash"]
        gt_bics = entry.get("bug_inducing_commit", [])

        # Extract predictions from logs (combined from all iterations)
        predicted_bics = extract_predictions_from_logs(repo_name, fix_commit)

        result = {
            "id": entry_id,
            "fix_commit_hash": fix_commit,
            "ground_truth_bics": gt_bics,
            "predicted_bics": predicted_bics,
            "num_predictions": len(predicted_bics)
        }
        results.append(result)

        print(f"Entry {entry_id}: {len(predicted_bics)} predictions")

    # Calculate metrics using the shared evaluation function
    summary = evaluate_results(results)
    print_summary_base(summary, "LLM4SZZ")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = RESULTS_DIR / f"baseline_llm4szz_{timestamp}.json"

    output_data = {
        "metadata": {
            "timestamp": timestamp,
            "sample_file": str(sample_path),
            "sample_size": len(sample_data),
            "algorithm": "LLM4SZZ",
            "config": {
                "model": model,
                "reasoning": reasoning,
                "iterations": 1
            }
        },
        "summary": summary,
        "results": results
    }

    with open(results_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\nDetailed results saved to: {results_file}")

    return results_file


def main():
    parser = argparse.ArgumentParser(description="Run LLM4SZZ baseline evaluation")
    parser.add_argument(
        "--sample", "-s",
        type=str,
        required=True,
        help="Path to the sample dataset JSON file"
    )
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Only run evaluation (skip llm4szz execution)"
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Only setup the dataset (don't run llm4szz)"
    )
    parser.add_argument(
        "--clear-logs",
        action="store_true",
        help="Clear existing logs for sample entries before running"
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default="gpt-4o-mini",
        help="Model to use for LLM calls. Supports OpenAI models (gpt-4o-mini, gpt-4o, etc.) "
             "and Anthropic models (claude-opus-4-5-20251101, claude-sonnet-4-20250514, etc.). "
             "Default: gpt-4o-mini"
    )
    parser.add_argument(
        "--reasoning", "-r",
        type=str,
        default="minimal",
        choices=["none", "minimal", "low", "medium", "high"],
        help="Reasoning effort level for reasoning models like gpt-5-mini (default: minimal)"
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip cloning missing repositories (fail if repo not found)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of entries to process"
    )
    args = parser.parse_args()

    sample_path = Path(args.sample)
    if not sample_path.exists():
        print(f"ERROR: Sample file not found: {sample_path}")
        return 1

    print("=" * 60)
    print("          LLM4SZZ Baseline Evaluation")
    print("=" * 60)
    print(f"Sample: {sample_path}")
    print(f"Model: {args.model}")
    print(f"Reasoning: {args.reasoning}")

    # Create repos directory if it doesn't exist
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    # Setup dataset
    sample_data = None
    if not args.eval_only:
        sample_data = convert_sample_to_llm4szz_format(sample_path)
        if args.limit and args.limit > 0:
            sample_data = sample_data[:args.limit]
            print(f"Limited to {len(sample_data)} entries")
        sample_data = setup_dataset_from_data(sample_data)
        print(f"Set up dataset with {len(sample_data)} entries")
    else:
        # Load the dataset for eval-only mode
        sample_data = convert_sample_to_llm4szz_format(sample_path)
        if args.limit and args.limit > 0:
            sample_data = sample_data[:args.limit]

    # Ensure all repositories exist (clone if needed)
    if not args.skip_clone and not args.eval_only:
        failed_repos = ensure_repos_exist(sample_data, REPOS_DIR)
        if failed_repos:
            # Filter out entries for repos that failed to clone
            original_count = len(sample_data)
            sample_data = [e for e in sample_data if e["repo_name"] not in failed_repos]
            print(f"\nFiltered out {original_count - len(sample_data)} entries due to missing repos")
            print(f"Proceeding with {len(sample_data)} entries")
            # Re-setup the dataset with filtered data
            if sample_data:
                with open(DATASET_DIR / "final_all_dataset.json", 'w') as f:
                    json.dump(sample_data, f, indent=2)
                with open(DATASET_DIR / "DS_SAMPLE.json", 'w') as f:
                    json.dump(sample_data, f, indent=2)

    if args.setup_only:
        print("\nSetup complete. Run llm4szz.py manually from the llm4szz directory.")
        return 0

    # Clear existing logs if requested
    if args.clear_logs and not args.eval_only:
        clear_logs_for_sample(sample_data)

    # Run llm4szz
    if not args.eval_only:
        success = run_llm4szz(model=args.model, reasoning=args.reasoning)
        if not success:
            print("WARNING: llm4szz.py exited with errors")

    # Generate detailed results file
    generate_results_file(sample_data, sample_path, model=args.model, reasoning=args.reasoning)

    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
