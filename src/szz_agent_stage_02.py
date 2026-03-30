#!/usr/bin/env python3
"""
Claude Code BIC Finder with Hybrid Binary Search + Candidate Selection.

Finds the bug-introducing commit (BIC) using a hybrid approach:
1. Binary search to narrow down candidates (minimizing API calls)
2. When ≤9 candidates remain, switch to direct candidate selection

This combines the efficiency of binary search with the accuracy of
presenting multiple candidates for direct comparison.

Designed to work as a second-stage method after szz_agent_stage_01.py:
- Processes entries where SZZ found no candidates OR szz_agent_stage_01 abstained
- Combines predictions from both stages for final evaluation

Usage:
    python szz_agent_stage_02.py                                  # Full run with default dataset
    python szz_agent_stage_02.py --limit 10                       # Process only 10 entries
    python szz_agent_stage_02.py -d sampled_datasets/DS_GITHUB-j.json  # Use DS_GITHUB-j dataset
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

from prompts import create_binary_search_instructions, create_candidate_selection_instructions

# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# =============================================================================
# CONFIGURATION
# =============================================================================
REPOS_DIR = PROJECT_ROOT / "repos"
DEFAULT_DATASET = PROJECT_ROOT / "sampled_datasets/linux_200_42_stage_1.json"
TEMP_DIR = PROJECT_ROOT / "temp_analysis"
RESULTS_DIR = PROJECT_ROOT / "results"
LOGS_DIR = PROJECT_ROOT / "logs"
AGENT_LOGS_DIR = PROJECT_ROOT / "agent_logs"
RANDOM_SEED = 44
CLAUDE_TIMEOUT = 600  # 10 min per invocation
OPENHANDS_TIMEOUT = 1800  # 30 min per invocation (local models are slower)
GIT_TIMEOUT = 300  # seconds, for large repos like linux-kernel

# Hybrid approach configuration
CANDIDATE_SELECTION_THRESHOLD = 33  # Switch to candidate selection when ≤X candidates remain

# Import evaluation utilities (for baseline-compatible evaluation)
from evaluation_utils import evaluate_results, print_summary as print_eval_summary


# =============================================================================
# AGENT INVOCATION HELPERS
# =============================================================================
def build_agent_command(agent: str, model: str, prompt: str, base_url: Optional[str] = None, api_key: str = "local-llm") -> Tuple[List[str], dict, int]:
    """Build the subprocess command, environment, and timeout for the chosen agent.

    Returns: (command_list, env_dict, timeout_seconds)
    """
    env = os.environ.copy()

    if agent == "claude-code":
        cmd = [
            "claude", "-p", "--dangerously-skip-permissions",
            "--disallowedTools", "WebFetch,WebSearch",
            "--model", model, prompt
        ]
        return cmd, env, CLAUDE_TIMEOUT

    elif agent == "openhands":
        # OpenHands uses LiteLLM; prefix model with openai/ for OpenAI-compatible APIs
        llm_model = model if model.startswith("openai/") else f"openai/{model}"
        env["LLM_MODEL"] = llm_model
        env["LLM_API_KEY"] = api_key
        if base_url:
            env["LLM_BASE_URL"] = base_url
        cmd = [
            "openhands", "--headless", "--override-with-envs",
            "-t", prompt
        ]
        return cmd, env, OPENHANDS_TIMEOUT

    else:
        raise ValueError(f"Unknown agent: {agent}")


def verify_agent_cli(agent: str) -> bool:
    """Verify the agent CLI tool is installed."""
    exe_map = {"claude-code": "claude", "openhands": "openhands"}
    exe = exe_map[agent]
    try:
        subprocess.run([exe, "--version"], capture_output=True, timeout=10)
        return True
    except FileNotFoundError:
        logging.error(f"{exe} CLI not found")
        print(f"\nERROR: {exe} CLI not found. Please install it first.")
        return False
    except Exception as e:
        logging.warning(f"Could not verify {exe} CLI: {e}")
        return True  # Don't block on transient errors


# =============================================================================
# REPOSITORY MANAGEMENT
# =============================================================================
def get_unique_repos(sample_data: List[Dict]) -> Set[str]:
    """Extract unique repository names from sample data."""
    return {entry.get("repo_name", "linux-kernel") for entry in sample_data}


def repo_exists(repo_name: str, repos_dir: Path) -> bool:
    """Check if a repository already exists in the repos directory."""
    repo_path = repos_dir / repo_name
    return repo_path.exists() and (repo_path / ".git").exists()


def clone_repo(repo_name: str, repos_dir: Path) -> bool:
    """Clone a repository from GitHub to the repos directory.

    Args:
        repo_name: Full repository name (e.g., 'owner/repo')
        repos_dir: Directory to clone repositories into

    Returns:
        True if cloning succeeded, False otherwise
    """
    repo_url = f"https://github.com/{repo_name}.git"
    repo_path = repos_dir / repo_name

    # Create parent directory if needed (for owner/repo structure)
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"  Cloning {repo_name} from {repo_url}...")
    try:
        result = subprocess.run(
            ["git", "clone", "--quiet", repo_url, str(repo_path)],
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout for large repos
        )
        if result.returncode == 0:
            print(f"  Successfully cloned {repo_name}")
            return True
        else:
            print(f"  ERROR cloning {repo_name}: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ERROR: Timeout cloning {repo_name}")
        return False
    except Exception as e:
        print(f"  ERROR cloning {repo_name}: {e}")
        return False


def ensure_repos_exist(sample_data: List[Dict], repos_dir: Path) -> List[str]:
    """Ensure all repositories needed for the sample exist.

    Clones any missing repositories from GitHub.

    Args:
        sample_data: List of sample entries with repo_name field
        repos_dir: Directory containing repositories

    Returns:
        List of repository names that failed to clone
    """
    unique_repos = get_unique_repos(sample_data)
    print(f"\nChecking {len(unique_repos)} unique repositories...")

    missing_repos = []
    existing_repos = []

    for repo_name in sorted(unique_repos):
        if repo_exists(repo_name, repos_dir):
            existing_repos.append(repo_name)
        else:
            missing_repos.append(repo_name)

    print(f"  Found {len(existing_repos)} existing repos")
    print(f"  Need to clone {len(missing_repos)} repos")

    if not missing_repos:
        return []

    print("\nCloning missing repositories...")
    failed_repos = []
    for idx, repo_name in enumerate(missing_repos, 1):
        print(f"  [{idx}/{len(missing_repos)}] {repo_name}")
        if not clone_repo(repo_name, repos_dir):
            failed_repos.append(repo_name)

    if failed_repos:
        print(f"\nWARNING: Failed to clone {len(failed_repos)} repos: {failed_repos}")

    return failed_repos


def get_repo_path(entry: Dict, repos_dir: Path) -> Path:
    """Get the repository path for a given entry.

    Handles both 'repo_name' field (for GitHub datasets) and legacy
    linux-kernel only datasets.
    """
    repo_name = entry.get("repo_name", "linux-kernel")
    return repos_dir / repo_name


# =============================================================================
# DATA CLASSES
# =============================================================================
@dataclass
class CommitInfo:
    """Information about a single commit."""
    hash: str
    timestamp: int  # Unix timestamp


@dataclass
class FileHistoryEntry:
    """A commit in a file's history with the file path at that commit."""
    hash: str
    timestamp: int
    file_path: str  # The path of the file at this commit (may differ due to renames)


@dataclass
class SearchStep:
    """Record of a single step in the binary search."""
    commit: str
    position: str  # index as string or "fix_parent"
    verdict: Optional[bool]  # True=BUG_PRESENT, False=BUG_NOT_PRESENT
    confidence: Optional[str]
    note: Optional[str] = None


@dataclass
class BICSearchResult:
    """Result from binary search for BIC."""
    found_bic: Optional[str]
    found_bic_index: Optional[int]
    is_earliest: bool  # True if BIC is oldest or before tracked history
    total_calls: int
    search_log: List[SearchStep] = field(default_factory=list)
    used_candidate_selection: bool = False  # True if switched to candidate selection mode
    candidate_selection_confidence: Optional[str] = None  # Confidence from candidate selection
    error: Optional[str] = None


@dataclass
class BICFinderEntryResult:
    """Result for a single dataset entry using BIC finder."""
    entry_id: str
    fix_commit: str
    ground_truth_bic: List[str]
    total_candidates: int
    search_result: BICSearchResult
    is_correct: Optional[bool]  # Does found_bic match ground truth?
    files_analyzed: List[str] = field(default_factory=list)
    session_limit_hit: bool = False
    error: Optional[str] = None


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> Path:
    """Setup logging to file and console."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"claude_code_hybrid_bic_finder_{timestamp}.log"

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

    return log_file


# =============================================================================
# GIT OPERATIONS
# =============================================================================
def git_cmd(repo_path: Path, *args, timeout: int = GIT_TIMEOUT) -> Tuple[str, int]:
    """Execute a git command and return (stdout, return_code)."""
    cmd = ["git", "-C", str(repo_path)] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors='replace'
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as e:
        return str(e), -1


def get_files_from_commit(repo_path: Path, commit_hash: str) -> List[str]:
    """Get list of files changed in a specific commit."""
    stdout, rc = git_cmd(repo_path, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash)
    if rc != 0:
        return []
    return [f.strip() for f in stdout.strip().split('\n') if f.strip()]


def get_file_history_with_paths(repo_path: Path, file_path: str, until_commit: str) -> List[FileHistoryEntry]:
    """
    Get complete commit history for a file, tracking renames and recording
    the file path at each commit.
    Returns entries ordered by timestamp (oldest first).
    """
    # Use --name-status to get file names, and --format to get commit info
    # The output format will be:
    # COMMIT_HASH TIMESTAMP
    # M/A/R  file_path [new_path for renames]
    stdout, rc = git_cmd(
        repo_path,
        "log",
        "--follow",
        "--format=%H %ct",
        "--name-status",
        until_commit,
        "--",
        file_path
    )
    if rc != 0 or not stdout.strip():
        return []

    entries = []
    current_hash = None
    current_timestamp = None
    current_path = file_path  # Start with current path, work backwards through renames

    lines = stdout.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Check if this is a commit line (hash + timestamp)
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 40:
            try:
                current_hash = parts[0]
                current_timestamp = int(parts[1])
                i += 1
                continue
            except ValueError:
                pass

        # This should be a file status line
        if current_hash and current_timestamp:
            # Parse status line: M/A/D/Rxxx  old_path [new_path]
            status_parts = line.split('\t')
            if len(status_parts) >= 2:
                status = status_parts[0]
                if status.startswith('R'):
                    # Rename: old_path -> new_path
                    # We're going backwards in history, so the "new_path" is what we knew
                    # and "old_path" is what it was called before
                    old_path = status_parts[1]
                    # new_path = status_parts[2] if len(status_parts) > 2 else current_path
                    entries.append(FileHistoryEntry(
                        hash=current_hash,
                        timestamp=current_timestamp,
                        file_path=old_path
                    ))
                    current_path = old_path  # Update for older commits
                else:
                    # M (modified), A (added), D (deleted)
                    entries.append(FileHistoryEntry(
                        hash=current_hash,
                        timestamp=current_timestamp,
                        file_path=status_parts[1]
                    ))
            current_hash = None
            current_timestamp = None

        i += 1

    # Sort by timestamp (oldest first)
    entries.sort(key=lambda e: e.timestamp)
    return entries


def build_file_rename_timeline(
    repo_path: Path,
    file_path: str,
    until_commit: str
) -> List[Tuple[int, str]]:
    """
    Build a timeline of (timestamp, path) pairs for a file, tracking renames.

    Returns a list sorted by timestamp (oldest first) where each entry represents
    the path the file had starting from that timestamp until the next rename.
    The last entry's path is valid up to and including until_commit.
    """
    history = get_file_history_with_paths(repo_path, file_path, until_commit)

    if not history:
        # File has no history, assume current path for all time
        return [(0, file_path)]

    # Build timeline: each entry is (timestamp, path_from_this_point_forward)
    # We process in chronological order (oldest first)
    timeline: List[Tuple[int, str]] = []

    for entry in history:
        # Each entry tells us the path at that commit
        # We record when a path became valid
        if not timeline or timeline[-1][1] != entry.file_path:
            timeline.append((entry.timestamp, entry.file_path))

    return timeline


def get_file_path_at_timestamp(timeline: List[Tuple[int, str]], timestamp: int) -> Optional[str]:
    """
    Given a rename timeline, find the file path at a specific timestamp.

    Returns the path, or None if the file didn't exist yet (timestamp before first entry).
    """
    if not timeline:
        return None

    # Find the last timeline entry with timestamp <= target timestamp
    result_path = None
    for ts, path in timeline:
        if ts <= timestamp:
            result_path = path
        else:
            break

    return result_path


def build_file_path_mapping(
    repo_path: Path,
    fix_commit: str
) -> Tuple[List[CommitInfo], Dict[str, Dict[str, str]]]:
    """
    Build a mapping of commit -> {current_file_path: path_at_that_commit}.

    For every candidate commit, maps ALL analyzable files touched by the fix commit
    to their paths at that commit's timestamp (handling renames properly).

    Excludes binary/generated files that Claude cannot analyze meaningfully.

    Returns:
        - List of all candidate commits (sorted by timestamp, oldest first)
        - Mapping: commit_hash -> {current_file_path: file_path_at_commit}
    """
    files = get_files_from_commit(repo_path, fix_commit)
    if not files:
        return [], {}

    # Exclude binary/generated files that Claude can't analyze meaningfully
    binary_extensions = (
        '.o', '.ko', '.a', '.so', '.bin', '.elf', '.exe', '.dll',  # compiled
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',   # images
        '.pdf', '.doc', '.docx',                                    # documents
        '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z',               # archives
        '.pyc', '.pyo', '.class',                                   # bytecode
    )
    analyzable_files = [f for f in files if not f.endswith(binary_extensions)]

    if not analyzable_files:
        logging.warning(f"No analyzable files in fix commit {fix_commit}")
        return [], {}

    # Step 1: Collect all candidate commits from file histories
    all_commits: Dict[str, CommitInfo] = {}

    # Step 2: Build rename timeline for each file
    file_timelines: Dict[str, List[Tuple[int, str]]] = {}

    for current_file_path in analyzable_files:
        history = get_file_history_with_paths(repo_path, current_file_path, fix_commit)

        # Collect commits
        for entry in history:
            if entry.hash not in all_commits:
                all_commits[entry.hash] = CommitInfo(hash=entry.hash, timestamp=entry.timestamp)

        # Build rename timeline for this file
        file_timelines[current_file_path] = build_file_rename_timeline(
            repo_path, current_file_path, fix_commit
        )

    # Sort commits by timestamp (oldest first)
    sorted_commits = sorted(all_commits.values(), key=lambda c: c.timestamp)

    if not sorted_commits:
        return [], {}

    # Step 3: Build complete path mapping for every commit and every file
    path_mapping: Dict[str, Dict[str, str]] = {}

    for commit in sorted_commits:
        path_mapping[commit.hash] = {}

        for current_file_path in analyzable_files:
            timeline = file_timelines.get(current_file_path, [])
            path_at_commit = get_file_path_at_timestamp(timeline, commit.timestamp)

            if path_at_commit:
                path_mapping[commit.hash][current_file_path] = path_at_commit
            # If path_at_commit is None, the file didn't exist yet at this commit
            # We don't add it to the mapping (correct behavior)

    return sorted_commits, path_mapping


def get_file_at_commit(repo_path: Path, commit: str, file_path: str) -> Optional[str]:
    """Get file content at a specific commit."""
    stdout, rc = git_cmd(repo_path, "show", f"{commit}:{file_path}")
    if rc != 0:
        return None
    return stdout


def get_parent_commit(repo_path: Path, commit: str) -> Optional[str]:
    """Get the parent commit hash."""
    stdout, rc = git_cmd(repo_path, "rev-parse", f"{commit}^")
    if rc != 0 or not stdout.strip():
        return None
    return stdout.strip()


def get_commit_message(repo_path: Path, commit: str) -> str:
    """Get the full commit message for a commit."""
    stdout, rc = git_cmd(repo_path, "log", "-1", "--format=%B", commit)
    if rc != 0:
        return ""
    return stdout.strip()


def get_commit_diff(repo_path: Path, commit: str) -> str:
    """Get the diff of a commit (changes introduced by the commit)."""
    stdout, rc = git_cmd(repo_path, "show", "--format=", "--patch", commit)
    if rc != 0:
        return ""
    return stdout


def is_commit_match(commit_hash: str, vics: Set[str]) -> bool:
    """Check if a commit matches any VIC using prefix matching."""
    for vic in vics:
        if commit_hash.startswith(vic) or vic.startswith(commit_hash):
            return True
    return False


# =============================================================================
# BIC REDACTION
# =============================================================================
def redact_bic_from_message(message: str, bic_commits: List[str]) -> str:
    """
    Remove BIC commit hashes from commit message.
    Removes entire lines containing 'Fixes:' tags that reference BIC commits,
    and redacts any remaining BIC references elsewhere.

    Handles cases where a longer hash contains the 8-char prefix of a BIC.
    E.g., if redacting 'cfbce435...', will also redact 'cfbce4351327f0f1a52a'.

    Also redacts any word containing 4+ character prefixes of BIC commits,
    to catch short references like 'cfbce43' that don't match the 7-40 char pattern.
    """
    # Build set of 8-character prefixes for efficient lookup
    prefixes_8char = set()
    for bic in bic_commits:
        if len(bic) >= 8:
            prefixes_8char.add(bic[:8].lower())

    # Build set of 4-character prefixes for catching short references
    prefixes_4char = set()
    for bic in bic_commits:
        if len(bic) >= 4:
            prefixes_4char.add(bic[:4].lower())

    lines = message.split('\n')
    filtered_lines = []

    for line in lines:
        # Check if this line contains a "Fixes:" tag with a BIC reference
        line_lower = line.lower()
        if 'fixes:' in line_lower:
            # Check if any BIC commit hash (full or prefix) is in this line
            skip_line = False
            for bic in bic_commits:
                # Check full SHA
                if bic.lower() in line_lower:
                    skip_line = True
                    break
                # Check if any 8-char prefix appears in the line
                if len(bic) >= 8:
                    prefix = bic[:8].lower()
                    if prefix in line_lower:
                        skip_line = True
                        break
            if skip_line:
                continue  # Skip this entire line
        filtered_lines.append(line)

    redacted = '\n'.join(filtered_lines)

    # Redact any hex strings that contain an 8-char prefix of BIC commits
    # This handles cases like 'cfbce4351327f0f1a52a' when redacting 'cfbce435...'
    def replace_if_contains_prefix(match):
        hex_string = match.group(0).lower()
        for prefix in prefixes_8char:
            if prefix in hex_string:
                return "[REDACTED_COMMIT]"
        return match.group(0)

    # Match any hex string that looks like a commit hash (7-40 chars)
    pattern = r'\b[a-fA-F0-9]{7,40}\b'
    redacted = re.sub(pattern, replace_if_contains_prefix, redacted)

    # Additionally, redact any word containing a 4+ char prefix of BIC commits
    # This catches short references like 'cfbce43' that don't match the 7-40 char pattern
    def replace_word_if_contains_short_prefix(match):
        word = match.group(0).lower()
        for prefix in prefixes_4char:
            if prefix in word:
                return "[REDACTED_COMMIT]"
        return match.group(0)

    # Match words that contain hex characters (potential partial commit references)
    # This catches things like 'commit cfbce4' or references in brackets like '(cfbce4)'
    short_pattern = r'\b[a-fA-F0-9]{4,6}\b'
    redacted = re.sub(short_pattern, replace_word_if_contains_short_prefix, redacted)

    return redacted


# =============================================================================
# FILE PREPARATION
# =============================================================================
def prepare_analysis_directory(
    repo_path: Path,
    fix_commit: str,
    candidate_commit: str,
    bic_commits: List[str],
    entry_id: str,
    path_mapping: Optional[Dict[str, Dict[str, str]]] = None,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Optional[Path]:
    """
    Prepare directory with files for Claude Code analysis.

    Args:
        path_mapping: Optional mapping of commit_hash -> {current_file_path: path_at_that_commit}
                      Used to handle file renames when retrieving old versions.
        without_fc_message: If True, do not include fix commit message.
        without_fc_diff: If True, do not include fix commit diff or buggy/fixed file versions.

    Structure:
        entry_{id}_check_{commit[:8]}/
          INSTRUCTIONS.md
          fix_commit_message.txt
          files/
            old/      # File(s) at candidate commit
            fixed/    # File(s) after fix commit (excluded if without_fc_diff)
            buggy/    # File(s) before fix commit (parent) (excluded if without_fc_diff)
    """
    work_dir = TEMP_DIR / f"entry_{entry_id}_check_{candidate_commit[:8]}"

    # Clean up if exists
    if work_dir.exists():
        shutil.rmtree(work_dir)

    work_dir.mkdir(parents=True)
    files_dir = work_dir / "files"
    old_dir = files_dir / "old"
    old_dir.mkdir(parents=True)

    # Only create buggy/fixed dirs if we're providing the fix commit diff context
    if not without_fc_diff:
        fixed_dir = files_dir / "fixed"
        buggy_dir = files_dir / "buggy"
        fixed_dir.mkdir(parents=True)
        buggy_dir.mkdir(parents=True)

    # Get parent of fix commit (buggy version) - needed for buggy/ dir
    parent_commit = None
    if not without_fc_diff:
        parent_commit = get_parent_commit(repo_path, fix_commit)
        if not parent_commit:
            logging.error(f"Could not get parent commit for {fix_commit}")
            return None

    # Get files changed in fix commit
    files = get_files_from_commit(repo_path, fix_commit)
    if not files:
        logging.error(f"No files found in fix commit {fix_commit}")
        return None

    # Exclude binary/generated files that Claude can't analyze meaningfully
    binary_extensions = (
        '.o', '.ko', '.a', '.so', '.bin', '.elf', '.exe', '.dll',  # compiled
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',   # images
        '.pdf', '.doc', '.docx',                                    # documents
        '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z',               # archives
        '.pyc', '.pyo', '.class',                                   # bytecode
    )
    analyzable_files = [f for f in files if not f.endswith(binary_extensions)]

    if not analyzable_files:
        logging.warning(f"No analyzable files in fix commit {fix_commit} for entry {entry_id}")
        return None

    files_copied = 0

    for file_path in analyzable_files:
        # Create subdirectory structure for file (use current path for consistency)
        file_subdir = Path(file_path).parent

        # Look up the file path at the candidate commit using the path mapping
        # The mapping now includes entries for all files at all commits where the file existed
        if path_mapping and candidate_commit in path_mapping:
            if file_path not in path_mapping[candidate_commit]:
                # File didn't exist yet at this commit - skip it for old/
                # but still include fixed and buggy versions for context (if not excluded)
                logging.debug(f"File {file_path} did not exist at commit {candidate_commit[:8]}, skipping for old/")
                if not without_fc_diff:
                    fixed_content = get_file_at_commit(repo_path, fix_commit, file_path)
                    if fixed_content is not None:
                        (fixed_dir / file_subdir).mkdir(parents=True, exist_ok=True)
                        (fixed_dir / file_path).write_text(fixed_content)

                    buggy_content = get_file_at_commit(repo_path, parent_commit, file_path)
                    if buggy_content is not None:
                        (buggy_dir / file_subdir).mkdir(parents=True, exist_ok=True)
                        (buggy_dir / file_path).write_text(buggy_content)
                continue

            old_file_path = path_mapping[candidate_commit][file_path]
        else:
            # No path mapping provided - use current path as fallback
            old_file_path = file_path

        # Get file at candidate commit (old) - use the path at that commit
        # The file state is AFTER the commit was applied (git show commit:file shows the tree at that commit)
        old_content = get_file_at_commit(repo_path, candidate_commit, old_file_path)
        if old_content is not None:
            (old_dir / file_subdir).mkdir(parents=True, exist_ok=True)
            # Save with current file name for consistency with fixed/buggy
            (old_dir / file_path).write_text(old_content)
            files_copied += 1
        else:
            logging.debug(f"Could not retrieve {old_file_path} at commit {candidate_commit[:8]}")

        # Get file after fix (fixed) and before fix (buggy) - skip if without_fc_diff
        if not without_fc_diff:
            # Get file after fix (fixed) - uses current path
            fixed_content = get_file_at_commit(repo_path, fix_commit, file_path)
            if fixed_content is not None:
                (fixed_dir / file_subdir).mkdir(parents=True, exist_ok=True)
                (fixed_dir / file_path).write_text(fixed_content)

            # Get file before fix (buggy) - uses current path (parent of fix has same path as fix)
            buggy_content = get_file_at_commit(repo_path, parent_commit, file_path)
            if buggy_content is not None:
                (buggy_dir / file_subdir).mkdir(parents=True, exist_ok=True)
                (buggy_dir / file_path).write_text(buggy_content)

    if files_copied == 0:
        logging.warning(f"No files could be retrieved for entry {entry_id} at commit {candidate_commit[:8]}. "
                       f"The files may not have existed at this commit.")
        return None

    # Get and redact commit message (unless excluded)
    if not without_fc_message:
        commit_message = get_commit_message(repo_path, fix_commit)
        redacted_message = redact_bic_from_message(commit_message, bic_commits)
        (work_dir / "fix_commit_message.txt").write_text(redacted_message)

    # Get and redact commit diff (unless excluded)
    if not without_fc_diff:
        commit_diff = get_commit_diff(repo_path, fix_commit)
        redacted_diff = redact_bic_from_message(commit_diff, bic_commits)
        (work_dir / "fix_commit_diff.txt").write_text(redacted_diff)

    # Create INSTRUCTIONS.md
    instructions = create_binary_search_instructions(without_fc_message, without_fc_diff)
    (work_dir / "INSTRUCTIONS.md").write_text(instructions)

    logging.info(f"Prepared analysis directory: {work_dir} ({files_copied} files)")
    return work_dir


def prepare_candidate_selection_directory(
    repo_path: Path,
    fix_commit: str,
    candidate_commits: List[CommitInfo],
    bic_commits: List[str],
    entry_id: str,
    lo_index: int,
    hi_index: int,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Optional[Path]:
    """
    Prepare directory with files for Claude Code candidate selection analysis.

    Args:
        repo_path: Path to the git repository
        fix_commit: The fix commit hash
        candidate_commits: The commits in the candidate window (ordered oldest first)
        bic_commits: List of BIC commit hashes (for redaction)
        entry_id: The entry ID for directory naming
        lo_index: Index in full candidate list where window starts
        hi_index: Index in full candidate list where window ends
        without_fc_message: If True, do not include fix commit message.
        without_fc_diff: If True, do not include fix commit diff.

    Structure:
        entry_{id}_candidate_selection_{lo}_{hi}/
          INSTRUCTIONS.md
          fix_commit_message.txt
          fix_commit_diff.txt
          candidates/
            candidate_01.diff
            candidate_02.diff
            ...
            candidate_XX.diff
    """
    work_dir = TEMP_DIR / f"entry_{entry_id}_candidate_selection_{lo_index}_{hi_index}"

    # Clean up if exists
    if work_dir.exists():
        shutil.rmtree(work_dir)

    work_dir.mkdir(parents=True)
    candidates_dir = work_dir / "candidates"
    candidates_dir.mkdir()

    # Get and redact fix commit message (unless excluded)
    if not without_fc_message:
        commit_message = get_commit_message(repo_path, fix_commit)
        redacted_message = redact_bic_from_message(commit_message, bic_commits)
        (work_dir / "fix_commit_message.txt").write_text(redacted_message)

    # Get and redact fix commit diff (unless excluded)
    if not without_fc_diff:
        commit_diff = get_commit_diff(repo_path, fix_commit)
        redacted_diff = redact_bic_from_message(commit_diff, bic_commits)
        (work_dir / "fix_commit_diff.txt").write_text(redacted_diff)

    # Pre-compile a single regex matching any candidate commit hash (7+ char prefixes)
    # This avoids O(n^2) by replacing the inner loop over all candidates per diff
    hash_prefixes = set()
    for c in candidate_commits:
        if len(c.hash) >= 7:
            hash_prefixes.add(re.escape(c.hash[:7]))

    # Build a single alternation regex: match any 7-char hash prefix followed by hex chars
    # This replaces the O(n) inner loop per diff with a single O(1) regex substitution
    if hash_prefixes:
        prefix_pattern = '|'.join(sorted(hash_prefixes, key=len, reverse=True))
        candidate_hash_re = re.compile(r'\b(?:' + prefix_pattern + r')[a-f0-9]*\b')
    else:
        candidate_hash_re = None

    # Create candidate diffs with neutral naming — written incrementally
    num_candidates = len(candidate_commits)
    for idx, commit in enumerate(candidate_commits, 1):
        candidate_diff = get_commit_diff(repo_path, commit.hash)

        # Redact any BIC references from the diff
        redacted_candidate_diff = redact_bic_from_message(candidate_diff, bic_commits)

        # Redact candidate commit hashes using the pre-compiled regex (O(1) per diff)
        if candidate_hash_re:
            redacted_candidate_diff = candidate_hash_re.sub("[COMMIT_HASH]", redacted_candidate_diff)

        # Write each candidate file immediately (incremental progress)
        candidate_file = candidates_dir / f"candidate_{idx:02d}.diff"
        candidate_file.write_text(redacted_candidate_diff)

        if idx % 500 == 0 or idx == num_candidates:
            logging.info(f"  Prepared candidate {idx}/{num_candidates} for entry {entry_id}")

    # Create instructions
    instructions = create_candidate_selection_instructions(len(candidate_commits), without_fc_message, without_fc_diff)
    (work_dir / "INSTRUCTIONS.md").write_text(instructions)

    logging.info(f"Prepared candidate selection directory: {work_dir} ({len(candidate_commits)} candidates)")
    return work_dir


def parse_candidate_selection_result(content: str) -> Tuple[List[int], Optional[str], Optional[str]]:
    """
    Parse the result.txt file content from candidate selection.

    Returns: (selected_candidates, confidence, explanation)
    - selected_candidates: List of 1-indexed candidate numbers
    - confidence: HIGH, MEDIUM, LOW or None
    - explanation: Truncated explanation or None
    """
    selected = []
    confidence = None
    explanation = None

    lines = content.split('\n')

    for i, line in enumerate(lines):
        line_upper = line.upper().strip()

        # Parse SELECTED line
        if line_upper.startswith("SELECTED:"):
            selection_part = line.split(":", 1)[1].strip()
            matches = re.findall(r'candidate[_\s]*(\d+)', selection_part, re.IGNORECASE)
            for match in matches:
                try:
                    selected.append(int(match))
                except ValueError:
                    pass

        # Parse CONFIDENCE line
        elif line_upper.startswith("CONFIDENCE:"):
            conf_part = line.split(":", 1)[1].strip().upper()
            for level in ["HIGH", "MEDIUM", "LOW"]:
                if level in conf_part:
                    confidence = level
                    break

        # Parse EXPLANATION line
        elif line_upper.startswith("EXPLANATION:"):
            explanation_lines = []
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j].strip():
                    explanation_lines.append(lines[j].strip())
            explanation = " ".join(explanation_lines)[:500]

    return selected, confidence, explanation


def invoke_claude_candidate_selection(
    work_dir: Path,
    entry_id: str,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm"
) -> Tuple[List[int], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Invoke an LLM agent to perform candidate selection analysis.

    Returns: (selected_candidates, confidence, explanation, error, raw_output)
    - selected_candidates: List of 1-indexed candidate numbers
    - confidence: HIGH, MEDIUM, LOW or None
    - explanation: Truncated explanation or None
    - error: Error message if something went wrong
    - raw_output: Raw Claude output for debugging

    Implements retry logic for rate limiting per CLAUDE.md instructions.
    """
    prompt = "Read INSTRUCTIONS.md and follow exactly. Write result to result.txt"
    cmd, env, timeout = build_agent_command(agent, model, prompt, base_url, api_key)

    for attempt in range(MAX_RETRIES):
        try:
            # Log agent output to a file
            call_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            log_file = AGENT_LOGS_DIR / f"s02_{entry_id}_candsel_{call_ts}.log"
            AGENT_LOGS_DIR.mkdir(parents=True, exist_ok=True)

            # Remove stale result.txt before retrying
            result_file = work_dir / "result.txt"
            if result_file.exists():
                result_file.unlink()

            with open(log_file, 'w') as lf:
                result = subprocess.run(
                    cmd,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    cwd=work_dir,
                    env=env,
                )

            raw_output = log_file.read_text(errors='replace')
            logging.info(f"[{entry_id}] Agent candidate selection log: {log_file}")

            # Check for session limit
            if check_session_limit(raw_output):
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"Rate limit hit, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
                else:
                    return [], None, None, "SESSION_LIMIT", raw_output

            # Check if result.txt was created
            result_file = work_dir / "result.txt"
            if not result_file.exists():
                logging.error(f"No result.txt created. Raw output: {raw_output[:2000] if raw_output else 'None'}")
                # Retry on this transient error
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"No result.txt created, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
                return [], None, None, "No result.txt created", raw_output

            # Parse result
            result_content = result_file.read_text()
            selected, confidence, explanation = parse_candidate_selection_result(result_content)

            if not selected:
                return [], confidence, explanation, "Could not parse selection from result", raw_output

            # Small delay between successful invocations to avoid rate limiting
            time.sleep(2)
            return selected, confidence, explanation, None, raw_output

        except subprocess.TimeoutExpired:
            return [], None, None, f"Timeout after {CLAUDE_TIMEOUT}s", None
        except FileNotFoundError:
            return [], None, None, "Claude CLI not found", None
        except Exception as e:
            if "overload" in str(e).lower():
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"API overloaded, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
            return [], None, None, str(e), None

    return [], None, None, "Max retries exceeded", None


# =============================================================================
# CLAUDE CODE INVOCATION
# =============================================================================
def invoke_claude_code(work_dir: Path, entry_id: str, call_label: str, model: str = "claude-opus-4-5",
                       agent: str = "claude-code", base_url: Optional[str] = None, api_key: str = "local-llm") -> Tuple[Optional[bool], Optional[str], Optional[str]]:
    """
    Invoke an LLM agent to analyze the prepared directory.

    Returns: (verdict, error, raw_output)
    - verdict: True = BUG_PRESENT, False = BUG_NOT_PRESENT, None = could not parse
    - error: Error message if something went wrong
    - raw_output: Raw agent output for debugging
    """
    prompt = "Read INSTRUCTIONS.md and follow exactly. Write result to result.txt"
    cmd, env, timeout = build_agent_command(agent, model, prompt, base_url, api_key)

    try:
        # Log agent output to a file
        call_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = AGENT_LOGS_DIR / f"s02_{entry_id}_verdict_{call_label}_{call_ts}.log"
        AGENT_LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Remove stale result.txt before invocation
        result_file = work_dir / "result.txt"
        if result_file.exists():
            result_file.unlink()

        with open(log_file, 'w') as lf:
            result = subprocess.run(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                cwd=work_dir,
                env=env,
            )

        raw_output = log_file.read_text(errors='replace')
        logging.info(f"[{entry_id}] Agent verdict log: {log_file}")

        # Check for session limit
        if check_session_limit(raw_output):
            return None, "SESSION_LIMIT", raw_output

        # Check if result.txt was created
        result_file = work_dir / "result.txt"
        if not result_file.exists():
            logging.error(f"No result.txt created (binary search). Raw output: {raw_output[:2000] if raw_output else 'None'}")
            return None, "No result.txt created", raw_output

        # Parse result
        result_content = result_file.read_text()
        verdict, parse_error = parse_result_file(result_content)

        if verdict is None and parse_error:
            return None, parse_error, raw_output

        return verdict, None, raw_output

    except subprocess.TimeoutExpired:
        return None, f"Timeout after {CLAUDE_TIMEOUT}s", None
    except FileNotFoundError:
        return None, "Claude CLI not found", None
    except Exception as e:
        return None, str(e), None


def check_session_limit(output: str) -> bool:
    """Check if Claude output indicates session/usage limit."""
    limit_indicators = [
        "session limit",
        "usage limit",
        "upgrade",
        "rate limit",
        "quota exceeded",
        "limit reached"
    ]
    output_lower = output.lower()
    return any(indicator in output_lower for indicator in limit_indicators)


def parse_result_file(content: str) -> Tuple[Optional[bool], Optional[str]]:
    """
    Parse the result.txt file content.

    Returns: (verdict, error)
    - verdict: True = BUG_PRESENT, False = BUG_NOT_PRESENT
    """
    content_upper = content.upper()

    # Look for verdict pattern
    if "VERDICT:" in content_upper:
        if "BUG_PRESENT" in content_upper and "BUG_NOT_PRESENT" not in content_upper:
            return True, None
        elif "BUG_NOT_PRESENT" in content_upper:
            return False, None

    # Fallback: look for keywords
    if "BUG_NOT_PRESENT" in content_upper:
        return False, None
    if "BUG_PRESENT" in content_upper:
        return True, None

    return None, f"Could not parse verdict from: {content[:200]}"


def parse_confidence_from_result(work_dir: Path) -> Optional[str]:
    """Extract confidence level from result.txt if available."""
    result_file = work_dir / "result.txt"
    if not result_file.exists():
        return None
    content = result_file.read_text().upper()
    if "CONFIDENCE:" in content:
        for level in ["HIGH", "MEDIUM", "LOW"]:
            if level in content:
                return level
    return None


# =============================================================================
# BIC FINDER FUNCTIONS
# =============================================================================

# Rate limiting retry settings (per CLAUDE.md)
MAX_RETRIES = 10
RETRY_DELAY = 60  # seconds


def check_bug_at_commit(
    repo_path: Path,
    fix_commit: str,
    candidate_commit: str,
    bic_commits: List[str],
    entry_id: str,
    position_label: str,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm",
    path_mapping: Optional[Dict[str, Dict[str, str]]] = None,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Tuple[Optional[bool], Optional[str], Optional[str]]:
    """
    Check if bug exists at a specific commit.

    Returns: (verdict, confidence, error)
    - verdict: True = BUG_PRESENT, False = BUG_NOT_PRESENT, None = inconclusive
    - confidence: HIGH, MEDIUM, LOW or None
    - error: Error message if something went wrong

    Implements retry logic for rate limiting per CLAUDE.md instructions.
    """
    import time

    work_dir = prepare_analysis_directory(
        repo_path, fix_commit, candidate_commit,
        bic_commits, entry_id, path_mapping,
        without_fc_message=without_fc_message,
        without_fc_diff=without_fc_diff
    )

    if not work_dir:
        return None, None, "Failed to prepare analysis directory"

    call_label = candidate_commit[:8]
    for attempt in range(MAX_RETRIES):
        verdict, error, raw_output = invoke_claude_code(work_dir, entry_id, call_label, model,
                                                           agent=agent, base_url=base_url, api_key=api_key)

        if error == "SESSION_LIMIT":
            # Rate limit or overload - wait and retry
            if attempt < MAX_RETRIES - 1:
                logging.warning(f"Rate limit hit, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY)
                continue
            else:
                return None, None, "Rate limit: max retries exceeded"

        if error and "overload" in error.lower():
            # Overloaded error - wait and retry
            if attempt < MAX_RETRIES - 1:
                logging.warning(f"API overloaded, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY)
                continue
            else:
                return None, None, "Overloaded: max retries exceeded"

        # Retry on "No result.txt created" - this seems to be a transient issue
        if error == "No result.txt created":
            if attempt < MAX_RETRIES - 1:
                logging.warning(f"No result.txt created, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY)
                continue
            else:
                return None, None, "No result.txt: max retries exceeded"

        # Success or non-retryable error
        if error:
            return None, None, error

        confidence = parse_confidence_from_result(work_dir)
        # Small delay between successful invocations to avoid rate limiting
        time.sleep(2)
        return verdict, confidence, None

    return None, None, "Max retries exceeded"


def find_commit_index(commit_hash: str, candidates: List[CommitInfo]) -> Optional[int]:
    """Find the index of a commit in the candidate list using prefix matching."""
    for idx, c in enumerate(candidates):
        if c.hash.startswith(commit_hash) or commit_hash.startswith(c.hash):
            return idx
    return None


def find_bic_binary_search(
    repo_path: Path,
    fix_commit: str,
    bic_commits: List[str],
    candidates: List[CommitInfo],
    entry_id: str,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm",
    path_mapping: Optional[Dict[str, Dict[str, str]]] = None,
    ground_truth_bic_indices: Optional[List[int]] = None,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> BICSearchResult:
    """
    Find the bug-introducing commit using hybrid binary search + candidate selection.

    Algorithm:
    1. Binary search to narrow down candidates
    2. When ≤X candidates remain (hi - lo <= CANDIDATE_SELECTION_THRESHOLD),
       switch to direct candidate selection for final determination

    Early stops if Claude makes a wrong prediction (detected via ground truth).
    For multiple BICs, uses the earliest (minimum index) BIC as the threshold.

    Invariant:
    - lo: index where bug is NOT present (-1 means before oldest commit)
    - hi: index where bug IS present (assumed for newest, verified during search)
    - BIC is in range (lo, hi]
    """
    search_log: List[SearchStep] = []
    total_calls = 0

    # For early stopping, use both earliest and latest BIC indices
    # This determines when a prediction makes it impossible to reach any correct BIC
    earliest_bic_index = min(ground_truth_bic_indices) if ground_truth_bic_indices else None
    latest_bic_index = max(ground_truth_bic_indices) if ground_truth_bic_indices else None

    def is_prediction_wrong(idx: int, verdict: bool) -> bool:
        """Check if Claude's prediction is wrong based on ground truth.

        Early stop only when no correct BIC can be reached:
        - "Bug present" at idx sets hi=idx, so BIC must be <= idx. Wrong if idx < earliest BIC.
        - "Bug not present" at idx sets lo=idx, so BIC must be > idx. Wrong if idx >= latest BIC.
        """
        if earliest_bic_index is None:
            return False
        # If Claude says bug present at idx, binary search sets hi=idx
        # BIC must be in (lo, idx], so we need at least one BIC <= idx
        # Wrong if idx < earliest BIC (no BIC can be reached)
        if verdict is True and idx < earliest_bic_index:
            return True
        # If Claude says bug not present at idx, binary search sets lo=idx
        # BIC must be in (idx, hi], so we need at least one BIC > idx
        # Wrong if idx >= latest BIC (no BIC can be reached)
        if verdict is False and idx >= latest_bic_index:
            return True
        return False

    if not candidates:
        return BICSearchResult(
            found_bic=None,
            found_bic_index=None,
            is_earliest=False,
            total_calls=total_calls,
            search_log=search_log,
            error="No candidate commits"
        )

    n = len(candidates)
    if ground_truth_bic_indices:
        if len(ground_truth_bic_indices) == 1:
            gt_str = f" (ground truth BIC at index {ground_truth_bic_indices[0]})"
        else:
            gt_str = f" (ground truth BICs at indices {ground_truth_bic_indices})"
    else:
        gt_str = ""

    # Start binary search directly
    # lo = -1 means bug is NOT present before the oldest tracked commit
    # hi = n - 1 assumes bug is present at newest (since it's present at fix parent)
    lo = -1
    hi = n - 1

    # Binary search with hybrid candidate selection
    while hi - lo > 1:
        # Check if we should switch to candidate selection mode
        # hi - lo is the number of candidates in range (lo, hi]
        remaining_candidates = hi - lo
        if remaining_candidates <= CANDIDATE_SELECTION_THRESHOLD:
            # Switch to candidate selection mode
            # Candidates are in range (lo, hi], i.e., indices lo+1 to hi inclusive
            window_start = lo + 1
            window_end = hi + 1  # +1 because we want inclusive range
            window_commits = candidates[window_start:window_end]

            print(f"  [{entry_id}] Switching to candidate selection: {len(window_commits)} candidates (indices {window_start}-{hi}){gt_str}")
            logging.info(f"[{entry_id}] Switching to candidate selection: {len(window_commits)} candidates (indices {window_start}-{hi})")

            search_log.append(SearchStep(
                commit="",
                position=f"{window_start}-{hi}",
                verdict=None,
                confidence=None,
                note=f"Switching to candidate selection ({len(window_commits)} candidates)"
            ))

            # Prepare candidate selection directory
            work_dir = prepare_candidate_selection_directory(
                repo_path, fix_commit, window_commits, bic_commits, entry_id, window_start, hi,
                without_fc_message=without_fc_message, without_fc_diff=without_fc_diff
            )

            if not work_dir:
                return BICSearchResult(
                    found_bic=None,
                    found_bic_index=None,
                    is_earliest=False,
                    total_calls=total_calls,
                    search_log=search_log,
                    error="Failed to prepare candidate selection directory"
                )

            # Invoke candidate selection
            selected, confidence, explanation, error, raw_output = invoke_claude_candidate_selection(
                work_dir, entry_id, model, agent=agent, base_url=base_url, api_key=api_key)
            total_calls += 1

            if error:
                search_log.append(SearchStep(
                    commit="",
                    position="candidate_selection",
                    verdict=None,
                    confidence=confidence,
                    note=f"Candidate selection error: {error}"
                ))
                return BICSearchResult(
                    found_bic=None,
                    found_bic_index=None,
                    is_earliest=False,
                    total_calls=total_calls,
                    search_log=search_log,
                    used_candidate_selection=True,
                    candidate_selection_confidence=confidence,
                    error=f"Candidate selection failed: {error}"
                )

            if not selected:
                search_log.append(SearchStep(
                    commit="",
                    position="candidate_selection",
                    verdict=None,
                    confidence=confidence,
                    note="No candidate selected"
                ))
                return BICSearchResult(
                    found_bic=None,
                    found_bic_index=None,
                    is_earliest=False,
                    total_calls=total_calls,
                    search_log=search_log,
                    used_candidate_selection=True,
                    candidate_selection_confidence=confidence,
                    error="Candidate selection returned no selection"
                )

            # Convert 1-indexed selection to actual index in full candidate list
            # selected[0] is 1-indexed within the window
            selected_window_idx = selected[0] - 1  # Convert to 0-indexed within window
            selected_full_idx = window_start + selected_window_idx  # Convert to index in full list
            found_bic = candidates[selected_full_idx]

            selected_str = f"candidate_{selected[0]:02d}"
            print(f"  [{entry_id}] Candidate selection chose {selected_str} (index {selected_full_idx}): {found_bic.hash[:8]}, confidence: {confidence}")
            logging.info(f"[{entry_id}] Candidate selection result: {selected_str} -> index {selected_full_idx}, confidence={confidence}")

            search_log.append(SearchStep(
                commit=found_bic.hash,
                position=str(selected_full_idx),
                verdict=True,  # Implicitly saying bug was introduced here
                confidence=confidence,
                note=f"Candidate selection chose {selected_str}"
            ))

            is_earliest = (selected_full_idx == 0)

            return BICSearchResult(
                found_bic=found_bic.hash,
                found_bic_index=selected_full_idx,
                is_earliest=is_earliest,
                total_calls=total_calls,
                search_log=search_log,
                used_candidate_selection=True,
                candidate_selection_confidence=confidence
            )

        # Continue with binary search
        mid = (lo + hi) // 2
        mid_commit = candidates[mid]

        print(f"  [{entry_id}] Checking index {mid}/{n-1} (lo={lo}, hi={hi}){gt_str}: {mid_commit.hash[:8]}")
        logging.info(f"[{entry_id}] Binary search: testing index {mid}/{n-1} (lo={lo}, hi={hi}): {mid_commit.hash[:8]}")
        verdict, confidence, error = check_bug_at_commit(
            repo_path, fix_commit, mid_commit.hash, bic_commits, entry_id, f"binary_{mid}", model,
            agent=agent, base_url=base_url, api_key=api_key, path_mapping=path_mapping,
            without_fc_message=without_fc_message, without_fc_diff=without_fc_diff
        )
        total_calls += 1

        search_log.append(SearchStep(
            commit=mid_commit.hash,
            position=str(mid),
            verdict=verdict,
            confidence=confidence,
            note=f"Binary search (lo={lo}, hi={hi})"
        ))

        if error:
            return BICSearchResult(
                found_bic=None,
                found_bic_index=None,
                is_earliest=False,
                total_calls=total_calls,
                search_log=search_log,
                error=f"Binary search failed at index {mid}: {error}"
            )

        if verdict is None:
            # Inconclusive - retry once
            print(f"  [{entry_id}] Inconclusive at index {mid}, retrying...")
            logging.warning(f"[{entry_id}] Inconclusive result at index {mid}, retrying...")
            verdict, confidence, error = check_bug_at_commit(
                repo_path, fix_commit, mid_commit.hash, bic_commits, entry_id, f"binary_{mid}_retry", model,
                agent=agent, base_url=base_url, api_key=api_key, path_mapping=path_mapping,
                without_fc_message=without_fc_message, without_fc_diff=without_fc_diff
            )
            total_calls += 1

            search_log.append(SearchStep(
                commit=mid_commit.hash,
                position=str(mid),
                verdict=verdict,
                confidence=confidence,
                note="Retry after inconclusive"
            ))

            if verdict is None:
                # Still inconclusive - assume no bug (conservative)
                print(f"  [{entry_id}] Still inconclusive, assuming bug not present")
                logging.warning(f"[{entry_id}] Still inconclusive, assuming bug not present")
                verdict = False

        # Check for wrong prediction based on BIC bounds
        if is_prediction_wrong(mid, verdict):
            if verdict:
                print(f"  [{entry_id}] WRONG PREDICTION at index {mid}: Claude said BUG_PRESENT, but earliest ground truth BIC is at {earliest_bic_index}")
            else:
                print(f"  [{entry_id}] WRONG PREDICTION at index {mid}: Claude said BUG_NOT_PRESENT, but latest ground truth BIC is at {latest_bic_index}")
            logging.warning(f"[{entry_id}] WRONG PREDICTION at index {mid}: verdict={verdict}, earliest_bic={earliest_bic_index}, latest_bic={latest_bic_index}")
            return BICSearchResult(
                found_bic=None,
                found_bic_index=None,
                is_earliest=False,
                total_calls=total_calls,
                search_log=search_log,
                error=f"Wrong prediction at index {mid} (early stop)"
            )

        if verdict:
            hi = mid
            print(f"  [{entry_id}] Bug present at {mid}, narrowed hi to {mid}")
            logging.info(f"[{entry_id}] Bug present at {mid}, narrowed hi to {mid}")
        else:
            lo = mid
            print(f"  [{entry_id}] Bug not present at {mid}, narrowed lo to {mid}")
            logging.info(f"[{entry_id}] Bug not present at {mid}, narrowed lo to {mid}")

    # If we get here without candidate selection, binary search completed normally
    # This happens when hi - lo == 1, meaning we've narrowed to a single candidate
    found_bic = candidates[hi]
    is_earliest = (hi == 0)  # BIC is oldest commit or before tracked history

    if is_earliest:
        print(f"  [{entry_id}] Found BIC at index {hi} (earliest): {found_bic.hash[:8]}")
        logging.info(f"[{entry_id}] Found BIC at index {hi} (earliest or before history): {found_bic.hash[:8]}")
    else:
        print(f"  [{entry_id}] Found BIC at index {hi}: {found_bic.hash[:8]}")
        logging.info(f"[{entry_id}] Found BIC at index {hi}: {found_bic.hash[:8]}")

    return BICSearchResult(
        found_bic=found_bic.hash,
        found_bic_index=hi,
        is_earliest=is_earliest,
        total_calls=total_calls,
        search_log=search_log
    )


# =============================================================================
# ANALYSIS
# =============================================================================


def find_all_bic_positions(bic_commits: List[str], candidates: List[CommitInfo]) -> List[int]:
    """
    Find positions of ALL BICs in candidate list.
    Returns list of 0-indexed positions (may be empty if none found).
    """
    bic_set = set(bic_commits)
    positions = []
    for idx, commit in enumerate(candidates):
        if is_commit_match(commit.hash, bic_set):
            positions.append(idx)
    return positions


def analyze_entry_bic_finder(
    repo_path: Path,
    entry: Dict,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm",
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Optional[BICFinderEntryResult]:
    """
    Analyze a single dataset entry using binary search to find BIC.

    1. Get sorted candidates
    2. Run binary search to find BIC
    3. Compare found BIC with ground truth
    """
    entry_id = entry["id"]
    fix_commit = entry["fix_commit_hash"]
    bic_commits = entry.get("bug_commit_hash", [])

    # Get candidate commits with file path mapping (handles renames)
    candidates, path_mapping = build_file_path_mapping(repo_path, fix_commit)
    files_analyzed = get_files_from_commit(repo_path, fix_commit)

    if not candidates:
        logging.error(f"[{entry_id}] No candidate commits found")
        print(f"\n[{entry_id}] No candidate commits found")
        return BICFinderEntryResult(
            entry_id=entry_id,
            fix_commit=fix_commit,
            ground_truth_bic=bic_commits,
            total_candidates=0,
            search_result=BICSearchResult(
                found_bic=None,
                found_bic_index=None,
                is_earliest=False,
                total_calls=0,
                error="No candidate commits found"
            ),
            is_correct=None,
            files_analyzed=files_analyzed,
            error="No candidate commits found"
        )

    # Find ALL ground truth BIC positions in candidates (handles multiple BICs)
    ground_truth_bic_indices = find_all_bic_positions(bic_commits, candidates)

    # Early stop: if ground truth BIC is not in candidates, binary search cannot find it
    if not ground_truth_bic_indices:
        print(f"\n[{entry_id}] Early stop: ground truth BIC not in {len(candidates)} candidates")
        logging.info(f"[{entry_id}] Early stop: ground truth BIC not in candidates")
        return BICFinderEntryResult(
            entry_id=entry_id,
            fix_commit=fix_commit,
            ground_truth_bic=bic_commits,
            total_candidates=len(candidates),
            search_result=BICSearchResult(
                found_bic=None,
                found_bic_index=None,
                is_earliest=False,
                total_calls=0,
                error="Early stop: ground truth BIC not in candidates"
            ),
            is_correct=False,
            files_analyzed=files_analyzed,
            error="Early stop: ground truth BIC not in candidates"
        )

    # Log entry info with ground truth positions
    if ground_truth_bic_indices:
        if len(ground_truth_bic_indices) == 1:
            gt_pos_str = f"ground truth BIC at index {ground_truth_bic_indices[0]}"
        else:
            gt_pos_str = f"ground truth BICs at indices {ground_truth_bic_indices}"
    else:
        gt_pos_str = "ground truth BIC NOT in candidates"
    print(f"\n[{entry_id}] Starting BIC finder: {len(candidates)} candidates, {gt_pos_str}")
    logging.info(f"[{entry_id}] Starting BIC finder (fix: {fix_commit[:8]}, {len(candidates)} candidates, {gt_pos_str})")

    # Run binary search with path mapping and ground truth for early stopping
    # Note: early stopping is only enabled for single BIC cases
    search_result = find_bic_binary_search(
        repo_path, fix_commit, bic_commits, candidates, entry_id, model,
        agent=agent, base_url=base_url, api_key=api_key,
        path_mapping=path_mapping, ground_truth_bic_indices=ground_truth_bic_indices,
        without_fc_message=without_fc_message, without_fc_diff=without_fc_diff
    )

    # Check if found BIC matches ground truth (correct if matches ANY ground truth BIC)
    is_correct = None
    if search_result.error and "early stop" in search_result.error.lower():
        # Early stopped due to wrong prediction
        is_correct = False
        print(f"  [{entry_id}] Result: INCORRECT (early stopped due to wrong prediction)")
    elif search_result.found_bic and bic_commits:
        bic_set = set(bic_commits)
        is_correct = is_commit_match(search_result.found_bic, bic_set)
        result_str = "CORRECT" if is_correct else "INCORRECT"
        gt_indices_str = str(ground_truth_bic_indices[0]) if len(ground_truth_bic_indices) == 1 else str(ground_truth_bic_indices)
        print(f"  [{entry_id}] Result: {result_str} (found index {search_result.found_bic_index}, ground truth at {gt_indices_str})")

    session_limit_hit = search_result.error and "rate limit" in search_result.error.lower()

    logging.info(f"[{entry_id}] Search complete: found_bic={search_result.found_bic[:8] if search_result.found_bic else None}, "
                 f"is_correct={is_correct}, calls={search_result.total_calls}")

    return BICFinderEntryResult(
        entry_id=entry_id,
        fix_commit=fix_commit,
        ground_truth_bic=bic_commits,
        total_candidates=len(candidates),
        search_result=search_result,
        is_correct=is_correct,
        files_analyzed=files_analyzed,
        session_limit_hit=session_limit_hit,
        error=search_result.error
    )


# =============================================================================
# BIC FINDER OUTPUT
# =============================================================================
def print_bic_finder_result(result: BICFinderEntryResult, idx: int, total: int):
    """Print result for a single BIC finder entry."""
    print(f"\n[{idx}/{total}] Entry {result.entry_id} (fix: {result.fix_commit[:8]})")

    if result.error:
        print(f"  ERROR: {result.error}")

    sr = result.search_result
    print(f"  Total candidates: {result.total_candidates}")
    print(f"  API calls: {sr.total_calls}")

    # Show if hybrid mode (candidate selection) was used
    if sr.used_candidate_selection:
        conf_str = f", confidence: {sr.candidate_selection_confidence}" if sr.candidate_selection_confidence else ""
        print(f"  Mode: Hybrid (binary search + candidate selection{conf_str})")
    else:
        print(f"  Mode: Pure binary search")

    if sr.found_bic:
        found_idx = sr.found_bic_index if sr.found_bic_index is not None else "?"
        print(f"  Found BIC: {sr.found_bic[:8]} (index {found_idx})")
        if sr.is_earliest:
            print(f"    -> BIC is earliest commit or before tracked history")
    else:
        print(f"  Found BIC: None")

    if result.ground_truth_bic:
        gt = ", ".join(b[:8] for b in result.ground_truth_bic[:3])
        if len(result.ground_truth_bic) > 3:
            gt += f" (+{len(result.ground_truth_bic) - 3} more)"
        print(f"  Ground truth: {gt}")

    if result.is_correct is not None:
        correct_str = "CORRECT" if result.is_correct else "INCORRECT"
        print(f"  Result: {correct_str}")

    # Show search progression
    if sr.search_log:
        print(f"  Search log ({len(sr.search_log)} steps):")
        for step in sr.search_log[:5]:  # Show first 5 steps
            verdict_str = "BUG" if step.verdict else "NO_BUG" if step.verdict is not None else "?"
            note = f" ({step.note})" if step.note else ""
            print(f"    [{step.position}] {step.commit[:8]} -> {verdict_str}{note}")
        if len(sr.search_log) > 5:
            print(f"    ... and {len(sr.search_log) - 5} more steps")


def print_bic_finder_summary(results: List[BICFinderEntryResult]):
    """Print comprehensive summary statistics for BIC finder."""
    print("\n" + "=" * 80)
    print("              HYBRID BIC FINDER SUMMARY")
    print("=" * 80)

    total = len(results)
    # Early stops count as wrong predictions, not errors
    errors = sum(1 for r in results if r.error and "early stop" not in r.error.lower())
    session_limits = sum(1 for r in results if r.session_limit_hit)

    # Accuracy statistics - include early stops (is_correct is not None)
    with_result = [r for r in results if r.is_correct is not None]
    correct = sum(1 for r in with_result if r.is_correct is True)
    incorrect = sum(1 for r in with_result if r.is_correct is False)
    unknown = sum(1 for r in with_result if r.is_correct is None)

    # API call statistics
    total_calls = sum(r.search_result.total_calls for r in results)
    avg_calls = total_calls / total if total > 0 else 0

    # Hybrid mode statistics
    used_candidate_selection = sum(1 for r in results if r.search_result.used_candidate_selection)
    pure_binary_search = total - used_candidate_selection

    # Accuracy by mode
    hybrid_results = [r for r in with_result if r.search_result.used_candidate_selection]
    binary_results = [r for r in with_result if not r.search_result.used_candidate_selection]
    hybrid_correct = sum(1 for r in hybrid_results if r.is_correct is True)
    hybrid_incorrect = sum(1 for r in hybrid_results if r.is_correct is False)
    binary_correct = sum(1 for r in binary_results if r.is_correct is True)
    binary_incorrect = sum(1 for r in binary_results if r.is_correct is False)

    # Confidence distribution for candidate selection
    confidence_dist = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "Unknown": 0}
    for r in results:
        if r.search_result.used_candidate_selection:
            conf = r.search_result.candidate_selection_confidence
            if conf in confidence_dist:
                confidence_dist[conf] += 1
            else:
                confidence_dist["Unknown"] += 1

    # Distribution of where BIC was found
    earliest = sum(1 for r in results if r.search_result.is_earliest)

    print(f"\nOVERALL")
    print("-" * 40)
    print(f"Total entries:              {total}")
    print(f"Entries with errors:        {errors}")
    print(f"Session limits hit:         {session_limits}")

    print(f"\nHYBRID MODE USAGE")
    print("-" * 40)
    print(f"Used candidate selection:   {used_candidate_selection} ({100*used_candidate_selection/total:.1f}%)" if total > 0 else f"Used candidate selection:   0")
    print(f"Pure binary search:         {pure_binary_search}")
    print(f"Selection threshold:        ≤{CANDIDATE_SELECTION_THRESHOLD} candidates")

    print(f"\nACCURACY (OVERALL)")
    print("-" * 40)
    print(f"Entries with BIC found:     {len(with_result)}")
    if with_result:
        accuracy = correct / (correct + incorrect) if (correct + incorrect) > 0 else 0
        print(f"Correct:                    {correct} ({100*accuracy:.1f}%)")
        print(f"Incorrect:                  {incorrect}")
        print(f"Unknown:                    {unknown}")

    if hybrid_results:
        print(f"\nACCURACY (HYBRID MODE)")
        print("-" * 40)
        hybrid_acc = hybrid_correct / (hybrid_correct + hybrid_incorrect) if (hybrid_correct + hybrid_incorrect) > 0 else 0
        print(f"Entries:                    {len(hybrid_results)}")
        print(f"Correct:                    {hybrid_correct} ({100*hybrid_acc:.1f}%)")
        print(f"Incorrect:                  {hybrid_incorrect}")

    if binary_results:
        print(f"\nACCURACY (PURE BINARY SEARCH)")
        print("-" * 40)
        binary_acc = binary_correct / (binary_correct + binary_incorrect) if (binary_correct + binary_incorrect) > 0 else 0
        print(f"Entries:                    {len(binary_results)}")
        print(f"Correct:                    {binary_correct} ({100*binary_acc:.1f}%)")
        print(f"Incorrect:                  {binary_incorrect}")

    if used_candidate_selection > 0:
        print(f"\nCANDIDATE SELECTION CONFIDENCE")
        print("-" * 40)
        for level, count in confidence_dist.items():
            if count > 0:
                print(f"{level}:                       {count}")

    print(f"\nAPI CALL EFFICIENCY")
    print("-" * 40)
    print(f"Total API calls:            {total_calls}")
    print(f"Average calls per entry:    {avg_calls:.1f}")

    print(f"\nBIC LOCATION")
    print("-" * 40)
    print(f"Earliest/before history:    {earliest}")

    # Position distribution
    positions = [r.search_result.found_bic_index for r in with_result if r.search_result.found_bic_index is not None]
    if positions:
        avg_pos = sum(positions) / len(positions)
        print(f"Average BIC position:       {avg_pos:.1f}")

    print("\n" + "=" * 80)


# =============================================================================
# MAIN
# =============================================================================
def filter_entries_for_stage2(data: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Filter entries from stage 1 output that need stage 2 processing.

    Entries need stage 2 processing if:
    - SZZ found no candidates (szz_candidates is empty)
    - OR the LLM in stage 1 abstained (llm_abstained is True)

    Returns:
        - filtered: entries that need stage 2 processing
        - stage1_decided: entries where stage 1 made a prediction (for combining later)
    """
    filtered = []
    stage1_decided = []

    # Statistics
    no_szz_candidates = 0
    llm_abstained = 0
    stage1_predicted = 0

    for entry in data:
        szz_candidates = entry.get("szz_candidates", [])
        llm_abstained_flag = entry.get("llm_abstained", False)
        llm_selected = entry.get("llm_selected_commit")

        # Check if SZZ found no candidates
        if not szz_candidates:
            no_szz_candidates += 1
            filtered.append(entry)
        # Check if LLM abstained (has candidates but chose not to select)
        elif llm_abstained_flag:
            llm_abstained += 1
            filtered.append(entry)
        # Otherwise, stage 1 made a prediction
        elif llm_selected:
            stage1_predicted += 1
            stage1_decided.append(entry)
        else:
            # LLM error or other case - treat as needing stage 2
            filtered.append(entry)

    print(f"\n--- Stage 1 Dataset Analysis ---")
    print(f"Total entries:                   {len(data)}")
    print(f"Entries with SZZ prediction:     {stage1_predicted}")
    print(f"Entries needing stage 2:")
    print(f"  - No SZZ candidates:           {no_szz_candidates}")
    print(f"  - LLM abstained:               {llm_abstained}")
    print(f"  - Total for stage 2:           {len(filtered)}")
    print(f"--------------------------------\n")

    logging.info(f"Stage 1 analysis: {stage1_predicted} decided, {len(filtered)} need stage 2 "
                 f"(no candidates: {no_szz_candidates}, abstained: {llm_abstained})")

    return filtered, stage1_decided


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Claude Code BIC Finder with Hybrid Binary Search + Candidate Selection"
    )
    parser.add_argument(
        "--dataset", "-d",
        type=str,
        default=str(DEFAULT_DATASET),
        help=f"Path to the stage 1 dataset JSON file (default: {DEFAULT_DATASET})"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of entries to process (for testing)"
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Skip cleanup of temp directories after processing"
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default="claude-opus-4-5",
        help="Model to use for LLM API calls (default: claude-opus-4-5)"
    )
    parser.add_argument(
        "--agent",
        type=str,
        choices=["claude-code", "openhands"],
        default="claude-code",
        help="Agent to use for LLM invocations (default: claude-code)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Base URL for the LLM API (used with --agent=openhands, e.g. http://127.0.0.1:8000/v1)"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default="local-llm",
        help="API key for the LLM (used with --agent=openhands, default: local-llm)"
    )
    parser.add_argument(
        "--without-fc-message",
        action="store_true",
        help="Do not provide the fix commit message as context in the LLM prompts"
    )
    parser.add_argument(
        "--without-fc-diff",
        action="store_true",
        help="Do not provide the fix commit diff as context in the LLM prompts (SZZ still uses it internally). Also excludes buggy/fixed file versions in binary search."
    )
    parser.add_argument(
        "--threshold", "-t",
        type=int,
        default=33,
        help="Candidate selection threshold: switch from binary search to direct selection when ≤N candidates remain (default: 33)"
    )
    return parser.parse_args()


def build_combined_results(
    stage1_decided: List[Dict],
    stage2_results: List[BICFinderEntryResult],
    all_entries: List[Dict]
) -> List[Dict]:
    """Build combined results in baseline-compatible format.

    For entries decided by stage 1: use llm_selected_commit as predicted_bics
    For entries processed by stage 2: use found_bic as predicted_bics
    For entries that couldn't be processed: empty predicted_bics

    Returns list of dicts with:
    - id, fix_commit_hash, ground_truth_bics, predicted_bics
    - Additional fields for tracking prediction source
    """
    # Create lookup for stage 2 results
    stage2_by_id = {r.entry_id: r for r in stage2_results}

    # Create lookup for stage 1 decided entries
    stage1_by_id = {e["id"]: e for e in stage1_decided}

    combined = []
    for entry in all_entries:
        entry_id = entry["id"]
        result = {
            "id": entry_id,
            "fix_commit_hash": entry["fix_commit_hash"],
            "ground_truth_bics": entry.get("bug_commit_hash", []),
            "predicted_bics": [],
            "prediction_source": None,  # "stage1" or "stage2" or None
            "stage2_details": None,  # For stage 2 predictions
        }

        if entry_id in stage1_by_id:
            # Stage 1 made a prediction
            stage1_entry = stage1_by_id[entry_id]
            llm_selected = stage1_entry.get("llm_selected_commit")
            if llm_selected:
                result["predicted_bics"] = [llm_selected]
            result["prediction_source"] = "stage1"
        elif entry_id in stage2_by_id:
            # Stage 2 made a prediction
            s2_result = stage2_by_id[entry_id]
            if s2_result.search_result.found_bic:
                result["predicted_bics"] = [s2_result.search_result.found_bic]
            result["prediction_source"] = "stage2"
            result["stage2_details"] = {
                "found_bic": s2_result.search_result.found_bic,
                "found_bic_index": s2_result.search_result.found_bic_index,
                "total_candidates": s2_result.total_candidates,
                "total_calls": s2_result.search_result.total_calls,
                "used_candidate_selection": s2_result.search_result.used_candidate_selection,
                "candidate_selection_confidence": s2_result.search_result.candidate_selection_confidence,
                "is_correct": s2_result.is_correct,
                "error": s2_result.error,
            }
        # else: no prediction (entry wasn't processed or failed)

        combined.append(result)

    return combined


def export_combined_results(
    combined_results: List[Dict],
    stage2_results: List[BICFinderEntryResult],
    dataset_path: str,
    model: str,
    timestamp: str,
    agent: str = "claude-code",
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Path:
    """Export combined results in baseline-compatible format with stage2 details included."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = RESULTS_DIR / f"szz_agent_stage_02_{timestamp}.json"

    # Calculate summary statistics for stage 2 only
    stage2_correct = sum(1 for r in stage2_results if r.is_correct is True)
    stage2_incorrect = sum(1 for r in stage2_results if r.is_correct is False)
    stage2_total_calls = sum(r.search_result.total_calls for r in stage2_results)

    # Hybrid mode statistics
    used_candidate_selection = sum(1 for r in stage2_results if r.search_result.used_candidate_selection)
    with_result = [r for r in stage2_results if r.is_correct is not None]
    hybrid_results = [r for r in with_result if r.search_result.used_candidate_selection]
    binary_results = [r for r in with_result if not r.search_result.used_candidate_selection]
    hybrid_correct = sum(1 for r in hybrid_results if r.is_correct is True)
    hybrid_incorrect = sum(1 for r in hybrid_results if r.is_correct is False)
    binary_correct = sum(1 for r in binary_results if r.is_correct is True)
    binary_incorrect = sum(1 for r in binary_results if r.is_correct is False)

    hybrid_accuracy = hybrid_correct / (hybrid_correct + hybrid_incorrect) if (hybrid_correct + hybrid_incorrect) > 0 else None
    binary_accuracy = binary_correct / (binary_correct + binary_incorrect) if (binary_correct + binary_incorrect) > 0 else None

    # Build stage2 details for all entries
    stage2_details = []
    for r in stage2_results:
        sr = r.search_result
        entry_data = {
            "entry_id": r.entry_id,
            "fix_commit": r.fix_commit,
            "ground_truth_bic": r.ground_truth_bic,
            "total_candidates": r.total_candidates,
            "found_bic": sr.found_bic,
            "found_bic_index": sr.found_bic_index,
            "is_earliest": sr.is_earliest,
            "is_correct": r.is_correct,
            "total_calls": sr.total_calls,
            "used_candidate_selection": sr.used_candidate_selection,
            "candidate_selection_confidence": sr.candidate_selection_confidence,
            "files_analyzed": r.files_analyzed,
            "session_limit_hit": r.session_limit_hit,
            "error": r.error,
            "search_log": [
                {
                    "commit": step.commit,
                    "position": step.position,
                    "verdict": step.verdict,
                    "confidence": step.confidence,
                    "note": step.note
                }
                for step in sr.search_log
            ]
        }
        stage2_details.append(entry_data)

    output_data = {
        "metadata": {
            "timestamp": timestamp,
            "dataset_file": dataset_path,
            "model": model,
            "agent": agent,
            "algorithm": "SZZ-Agent (our method)",
            "candidate_selection_threshold": CANDIDATE_SELECTION_THRESHOLD,
            "without_fc_message": without_fc_message,
            "without_fc_diff": without_fc_diff,
        },
        "stage2_summary": {
            "entries_processed": len(stage2_results),
            "correct": stage2_correct,
            "incorrect": stage2_incorrect,
            "accuracy": stage2_correct / (stage2_correct + stage2_incorrect) if (stage2_correct + stage2_incorrect) > 0 else None,
            "total_api_calls": stage2_total_calls,
            "avg_calls_per_entry": stage2_total_calls / len(stage2_results) if stage2_results else 0,
            "earliest_bic_count": sum(1 for r in stage2_results if r.search_result.is_earliest),
            "hybrid_mode": {
                "used_candidate_selection": used_candidate_selection,
                "pure_binary_search": len(stage2_results) - used_candidate_selection,
                "hybrid_accuracy": hybrid_accuracy,
                "hybrid_correct": hybrid_correct,
                "hybrid_incorrect": hybrid_incorrect,
                "binary_accuracy": binary_accuracy,
                "binary_correct": binary_correct,
                "binary_incorrect": binary_incorrect
            }
        },
        "results": combined_results,
        "stage2_details": stage2_details
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    return output_file


def main():
    """Main entry point."""
    args = parse_args()

    global CANDIDATE_SELECTION_THRESHOLD
    CANDIDATE_SELECTION_THRESHOLD = args.threshold

    log_file = setup_logging()

    print("=" * 80)
    print("SZZ-Agent (our method)")
    print("=" * 80)

    print(f"Strategy: Binary search until ≤{CANDIDATE_SELECTION_THRESHOLD} candidates, then direct selection")

    logging.info(f"Log file: {log_file}")
    logging.info(f"Using agent: {args.agent}")
    logging.info(f"Using model: {args.model}")
    logging.info(f"Hybrid config: candidate selection threshold = {CANDIDATE_SELECTION_THRESHOLD}")
    # Check agent CLI exists
    if not verify_agent_cli(args.agent):
        return

    # Load data from stage 1 dataset
    dataset_path = Path(args.dataset)
    logging.info(f"Loading data from {dataset_path}...")
    print(f"\nLoading data from {dataset_path}...")

    if not dataset_path.exists():
        print(f"\nERROR: Dataset file not found: {dataset_path}")
        logging.error(f"Dataset file not found: {dataset_path}")
        return

    with open(dataset_path, 'r') as f:
        dataset = json.load(f)

    # Handle stage 1 output format (wrapped in metadata/results)
    if isinstance(dataset, dict) and "results" in dataset:
        all_entries = dataset["results"]
        print(f"Loaded stage 1 dataset with {len(all_entries)} entries")
    else:
        # Fallback for flat list format
        all_entries = dataset
        print(f"Loaded dataset with {len(all_entries)} entries")

    # Create repos directory if it doesn't exist
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure all repositories exist (clone if needed)
    failed_repos = ensure_repos_exist(all_entries, REPOS_DIR)
    if failed_repos:
        original_count = len(all_entries)
        all_entries = [e for e in all_entries if e.get("repo_name", "linux-kernel") not in failed_repos]
        print(f"\nFiltered out {original_count - len(all_entries)} entries due to missing repos")
        print(f"Proceeding with {len(all_entries)} entries")

    # Filter entries for stage 2 processing
    filtered, stage1_decided = filter_entries_for_stage2(all_entries)

    if not filtered:
        print("No entries need stage 2 processing")
        # Still evaluate the combined results (stage 1 only)
        combined_results = build_combined_results(stage1_decided, [], all_entries)
        summary = evaluate_results(combined_results)
        print_eval_summary(summary, "Combined (Stage 1 only)")
        return

    # Set random seed before shuffle for reproducibility
    random.seed(RANDOM_SEED)
    random.shuffle(filtered)

    # Apply limit if specified
    if args.limit is not None and args.limit > 0:
        filtered = filtered[:args.limit]
        print(f"Limited to {len(filtered)} entries for testing")
        logging.info(f"Limited to {len(filtered)} entries")

    # Clear and create directories
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
        logging.info(f"Cleared temp directory: {TEMP_DIR}")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Process entries using hybrid BIC finder (binary search + candidate selection)
    stage2_results: List[BICFinderEntryResult] = []
    session_limit_hit = False

    for idx, entry in enumerate(filtered, 1):
        if session_limit_hit:
            logging.warning("Session limit was hit, stopping processing")
            print("\n*** Session limit hit, stopping processing ***")
            break

        repo_path = get_repo_path(entry, REPOS_DIR)
        result = analyze_entry_bic_finder(repo_path, entry, args.model,
                                         agent=args.agent, base_url=args.base_url,
                                         api_key=args.api_key,
                                         without_fc_message=args.without_fc_message,
                                         without_fc_diff=args.without_fc_diff)

        if result:
            stage2_results.append(result)
            print_bic_finder_result(result, idx, len(filtered))

            if result.session_limit_hit:
                session_limit_hit = True

    # Build combined results
    combined_results = build_combined_results(stage1_decided, stage2_results, all_entries)

    # Evaluate and print results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\n" + "=" * 80)
    print("                    EVALUATION RESULTS")
    print("=" * 80)

    # Overall combined evaluation using evaluation_utils
    print("\n### COMBINED (Stage 1 + Stage 2) ###")
    summary = evaluate_results(combined_results)
    print_eval_summary(summary, "Combined")

    # Stage 2 specific statistics
    if stage2_results:
        print("\n### STAGE 2 (This Script) DETAILS ###")
        print("-" * 50)

        # Stage 2 accuracy
        s2_with_result = [r for r in stage2_results if r.is_correct is not None]
        s2_correct = sum(1 for r in s2_with_result if r.is_correct is True)
        s2_incorrect = sum(1 for r in s2_with_result if r.is_correct is False)
        s2_total = s2_correct + s2_incorrect

        print(f"Entries processed by stage 2:    {len(stage2_results)}")
        print(f"Entries with BIC found:          {len(s2_with_result)}")
        if s2_total > 0:
            s2_accuracy = s2_correct / s2_total
            print(f"Stage 2 correct:                 {s2_correct} ({100*s2_accuracy:.1f}%)")
            print(f"Stage 2 incorrect:               {s2_incorrect}")

        # API call statistics
        total_calls = sum(r.search_result.total_calls for r in stage2_results)
        avg_calls = total_calls / len(stage2_results) if stage2_results else 0
        print(f"Total API calls:                 {total_calls}")
        print(f"Average calls per entry:         {avg_calls:.1f}")

        # Hybrid mode statistics
        used_candidate_selection = sum(1 for r in stage2_results if r.search_result.used_candidate_selection)
        print(f"Used candidate selection:        {used_candidate_selection}")

        # Stage 1 contribution
        print(f"\n### STAGE 1 CONTRIBUTION ###")
        print("-" * 50)
        stage1_entries = [r for r in combined_results if r.get("prediction_source") == "stage1"]
        stage1_with_pred = [r for r in stage1_entries if r.get("predicted_bics")]
        print(f"Entries decided by stage 1:      {len(stage1_entries)}")
        print(f"With predictions:                {len(stage1_with_pred)}")

    # Export results (single file with all information including stage2 details)
    output_file = export_combined_results(
        combined_results, stage2_results, str(dataset_path), args.model, timestamp,
        agent=args.agent, without_fc_message=args.without_fc_message,
        without_fc_diff=args.without_fc_diff
    )
    print(f"\nResults saved to: {output_file}")

    print("\nDone!")


if __name__ == "__main__":
    main()
