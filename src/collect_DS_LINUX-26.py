#!/usr/bin/env python3
"""
Collect Linux kernel fix commits and their bug-inducing commits using the "Fixes:" tag.

Based on the methodology from Lyu et al. "Evaluating SZZ Implementations: An Empirical
Study on the Linux Kernel" - extracts developer-annotated fix/BIC pairs from the Linux
kernel's "Fixes:" tag convention.

Usage:
    python collect_linux_fixes_dataset.py
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


# Configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_PATH = PROJECT_ROOT / "repos" / "linux-kernel"
OUTPUT_PATH = PROJECT_ROOT / "data" / "DS_LINUX-26.json"

# Date range: September 1, 2025 - January 31, 2026
START_DATE = "2025-09-01"
END_DATE = "2026-01-31"


def run_git_command(args: list[str], cwd: Path) -> tuple[str, int]:
    """Run a git command and return stdout and return code."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        print(f"Git command timed out: git {' '.join(args)}")
        return "", 1
    except Exception as e:
        print(f"Git command failed: {e}")
        return "", 1


def pull_latest(repo_path: Path) -> bool:
    """Pull the latest changes from the remote repository."""
    print(f"Pulling latest changes from {repo_path}...")

    # Fetch all
    stdout, rc = run_git_command(["fetch", "--all"], repo_path)
    if rc != 0:
        print(f"Warning: git fetch failed")

    # Pull (try to fast-forward)
    stdout, rc = run_git_command(["pull", "--ff-only"], repo_path)
    if rc != 0:
        print(f"Warning: git pull --ff-only failed, trying regular pull")
        stdout, rc = run_git_command(["pull"], repo_path)
        if rc != 0:
            print(f"Warning: git pull failed, continuing with existing state")
            return False

    print("Repository updated successfully")
    return True


def get_commits_in_date_range(repo_path: Path, start_date: str, end_date: str) -> list[str]:
    """Get all commit hashes in the given date range."""
    print(f"Finding commits between {start_date} and {end_date}...")

    # Use git log to find commits with "Fixes:" in the message within date range
    # Format: just the commit hash
    stdout, rc = run_git_command([
        "log",
        "--all",
        f"--after={start_date}",
        f"--before={end_date}T23:59:59",
        "--grep=Fixes:",
        "--format=%H",
    ], repo_path)

    if rc != 0:
        print("Failed to get commits")
        return []

    commits = [c.strip() for c in stdout.strip().split('\n') if c.strip()]
    print(f"Found {len(commits)} commits with 'Fixes:' tag in date range")
    return commits


def get_commit_message(repo_path: Path, commit_hash: str) -> str:
    """Get the full commit message for a commit."""
    stdout, rc = run_git_command(["log", "-1", "--format=%B", commit_hash], repo_path)
    if rc != 0:
        return ""
    return stdout


def parse_fixes_tags(commit_message: str) -> list[str]:
    """
    Parse "Fixes:" tags from a commit message and extract commit hash references.

    The format is typically:
        Fixes: a1b2c3d4e5f6 ("Original commit subject line")

    Returns a list of partial commit hashes (7-12+ characters).
    """
    fixes_hashes = []

    # Pattern to match "Fixes:" followed by a commit hash (7-40 hex chars)
    # The hash is usually followed by a space and the commit subject in quotes
    pattern = r'[Ff]ixes:\s*([0-9a-f]{7,40})'

    matches = re.findall(pattern, commit_message)
    for match in matches:
        fixes_hashes.append(match)

    return fixes_hashes


def resolve_partial_hash(repo_path: Path, partial_hash: str) -> Optional[str]:
    """
    Resolve a partial commit hash to its full SHA.
    Returns None if the hash cannot be resolved or is ambiguous.
    """
    stdout, rc = run_git_command(["rev-parse", "--verify", f"{partial_hash}^{{commit}}"], repo_path)
    if rc != 0:
        return None

    full_hash = stdout.strip()
    if len(full_hash) == 40 and all(c in '0123456789abcdef' for c in full_hash):
        return full_hash
    return None


def verify_commit_exists(repo_path: Path, commit_hash: str) -> bool:
    """Verify that a commit exists in the repository."""
    stdout, rc = run_git_command(["cat-file", "-t", commit_hash], repo_path)
    return rc == 0 and stdout.strip() == "commit"


def collect_dataset(repo_path: Path, start_date: str, end_date: str) -> list[dict]:
    """
    Collect fix commit / bug-inducing commit pairs from the repository.
    """
    dataset = []

    # Get all commits with Fixes: tags in the date range
    fix_commits = get_commits_in_date_range(repo_path, start_date, end_date)

    print(f"\nProcessing {len(fix_commits)} fix commits...")

    skipped_no_fixes = 0
    skipped_unresolved = 0
    skipped_not_exists = 0

    for idx, fix_commit in enumerate(fix_commits, 1):
        if idx % 100 == 0:
            print(f"  Processed {idx}/{len(fix_commits)} commits...")

        # Get commit message
        message = get_commit_message(repo_path, fix_commit)
        if not message:
            continue

        # Parse Fixes: tags
        partial_hashes = parse_fixes_tags(message)
        if not partial_hashes:
            skipped_no_fixes += 1
            continue

        # Resolve partial hashes to full hashes
        bug_commit_hashes = []
        for partial in partial_hashes:
            full_hash = resolve_partial_hash(repo_path, partial)
            if full_hash is None:
                skipped_unresolved += 1
                continue

            # Verify the commit exists
            if not verify_commit_exists(repo_path, full_hash):
                skipped_not_exists += 1
                continue

            bug_commit_hashes.append(full_hash)

        # Only add if we have at least one valid bug-inducing commit
        if bug_commit_hashes:
            entry = {
                "id": str(len(dataset) + 1),
                "repo_name": "torvalds/linux",
                "fix_commit_hash": fix_commit,
                "bug_commit_hash": bug_commit_hashes,
                "language": ["c"]
            }
            dataset.append(entry)

    print(f"\nCollection complete:")
    print(f"  Total fix commits found: {len(fix_commits)}")
    print(f"  Valid fix/BIC pairs: {len(dataset)}")
    print(f"  Skipped (no valid Fixes: tag): {skipped_no_fixes}")
    print(f"  Skipped (unresolved hash): {skipped_unresolved}")
    print(f"  Skipped (commit not exists): {skipped_not_exists}")

    return dataset


def main():
    print("=" * 60)
    print("Linux Kernel Fixes Dataset Collector")
    print("=" * 60)
    print(f"Repository: {REPO_PATH}")
    print(f"Date range: {START_DATE} to {END_DATE}")
    print(f"Output: {OUTPUT_PATH}")
    print("=" * 60)

    # Check repo exists
    if not REPO_PATH.exists():
        print(f"Error: Repository not found at {REPO_PATH}")
        return 1

    # Pull latest changes
    pull_latest(REPO_PATH)

    # Collect dataset
    dataset = collect_dataset(REPO_PATH, START_DATE, END_DATE)

    if not dataset:
        print("\nNo valid fix/BIC pairs found in the date range.")
        return 1

    # Ensure output directory exists
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Write output
    print(f"\nWriting {len(dataset)} entries to {OUTPUT_PATH}...")
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(dataset, f, indent=4)

    print("Done!")
    return 0


if __name__ == "__main__":
    exit(main())
