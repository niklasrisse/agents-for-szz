#!/usr/bin/env python3
"""SZZ algorithm with LLM-based candidate selection.

This script:
1. Runs SZZ to find ALL candidate bug-inducing commits (via git blame)
2. Tracks whether ground truth BIC is in the candidate set
3. Optionally uses Claude Code to select the most likely BIC from candidates
4. Claude can also decide that none of the candidates are the BIC
5. Evaluates the results against ground truth

Usage:
    python szz_agent_stage_01.py                              # Full run with default sample
    python szz_agent_stage_01.py --limit 10                   # Process only 10 samples (for testing)
    python szz_agent_stage_01.py -s other_sample.json         # Use a different sample file
    python szz_agent_stage_01.py -s sampled_datasets/DS_GITHUB-j.json  # Use DS_GITHUB-j dataset
"""

import argparse
import json
import logging
import os
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

from prompts import create_stage01_candidate_selection_instructions

# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# =============================================================================
# CONFIGURATION
# =============================================================================
REPOS_DIR = PROJECT_ROOT / "repos"
DEFAULT_SAMPLE_FILE = PROJECT_ROOT / "sampled_datasets/DS_LINUX-26_100_42.json"
RESULTS_DIR = PROJECT_ROOT / "results"
LOGS_DIR = PROJECT_ROOT / "logs"
AGENT_LOGS_DIR = PROJECT_ROOT / "agent_logs"
TEMP_DIR = PROJECT_ROOT / "temp_analysis"

# Git settings
GIT_TIMEOUT = 300  # seconds


# =============================================================================
# REPOSITORY MANAGEMENT
# =============================================================================
def get_unique_repos(sample_data: List[Dict]) -> Set[str]:
    """Extract unique repository names from sample data."""
    return {entry["repo_name"] for entry in sample_data}


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


# Agent settings
CLAUDE_TIMEOUT = 600  # 10 min per invocation
OPENHANDS_TIMEOUT = 1800  # 30 min per invocation (local models are slower)
MAX_RETRIES = 10
RETRY_DELAY = 60  # seconds


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
# DATA CLASSES
# =============================================================================
@dataclass
class CommitInfo:
    """Information about a single commit."""
    hash: str
    timestamp: int  # Unix timestamp


@dataclass
class SZZResult:
    """Result from SZZ candidate extraction for a single entry."""
    entry_id: str
    fix_commit: str
    ground_truth_bics: List[str]
    candidate_commits: List[str]  # All commits from git blame
    unique_candidates: List[str]  # Deduplicated candidates
    gt_in_candidates: bool  # Whether any ground truth BIC is in candidates
    matching_gt_commits: List[str]  # Which ground truth commits are in candidates


@dataclass
class LLMSelectionResult:
    """Result from LLM candidate selection."""
    selected_commit: Optional[str]  # None if LLM abstained
    abstained: bool  # True if LLM explicitly chose "none"
    confidence: Optional[str]  # HIGH, MEDIUM, LOW
    explanation: Optional[str]
    error: Optional[str]


@dataclass
class FullResult:
    """Complete result for a single entry."""
    entry_id: str
    fix_commit: str
    ground_truth_bics: List[str]
    szz_candidates: List[str]
    gt_in_candidates: bool
    matching_gt_commits: List[str]
    llm_selected_commit: Optional[str]
    llm_abstained: bool
    llm_confidence: Optional[str]
    llm_explanation: Optional[str]
    llm_correct: Optional[bool]  # None if abstained or error
    llm_error: Optional[str]


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> Path:
    """Setup logging to file and console."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"llm_szz_verifier_{timestamp}.log"

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


def is_semantic_line(line: str) -> bool:
    """Check if line is semantic (not blank/comment)."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return False
    if stripped == "{" or stripped == "}" or stripped == ");":
        return False
    return True


def get_blame_for_line(repo_path: Path, commit: str, filepath: str, line_num: int) -> Optional[str]:
    """Get the commit that last modified a specific line."""
    out, rc = git_cmd(repo_path, "blame", "-l", "-L", f"{line_num},{line_num}", commit, "--", filepath)
    if rc != 0 or not out.strip():
        return None
    match = re.match(r"(\^?[a-f0-9]+)", out)
    if match:
        blamed_commit = match.group(1).lstrip("^")
        return blamed_commit
    return None


def get_modified_lines(repo_path: Path, commit: str, filepath: str) -> List[Tuple[int, str]]:
    """Get deleted/modified lines from a commit's diff for a specific file."""
    out, rc = git_cmd(repo_path, "diff", "-U0", f"{commit}^", commit, "--", filepath)
    if rc != 0:
        return []

    lines = []
    current_old_line = 0
    for line in out.splitlines():
        hunk_match = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
        if hunk_match:
            current_old_line = int(hunk_match.group(1))
            continue
        if line.startswith("-") and not line.startswith("---"):
            content = line[1:]
            if is_semantic_line(content):
                lines.append((current_old_line, content))
            current_old_line += 1

    return lines


def get_changed_files(repo_path: Path, commit: str) -> List[str]:
    """Get list of files changed in a commit."""
    out, rc = git_cmd(repo_path, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    if rc != 0:
        return []
    return [f.strip() for f in out.splitlines() if f.strip()]


def get_commit_message(repo_path: Path, commit: str) -> str:
    """Get the commit message for a commit."""
    out, rc = git_cmd(repo_path, "log", "-1", "--format=%B", commit)
    if rc != 0:
        return ""
    return out.strip()


def get_commit_diff(repo_path: Path, commit: str, context_lines: int = 5) -> str:
    """Get the diff of a commit with specified context lines."""
    out, rc = git_cmd(repo_path, "show", "--format=", "--patch", f"-U{context_lines}", commit)
    if rc != 0:
        return ""
    return out


def is_commit_match(commit_hash: str, vics: Set[str]) -> bool:
    """Check if a commit matches any VIC using prefix matching."""
    for vic in vics:
        if commit_hash.startswith(vic) or vic.startswith(commit_hash):
            return True
    return False


# =============================================================================
# SZZ CANDIDATE EXTRACTION
# =============================================================================
# Source file extensions to analyze (covers common programming languages)
SOURCE_FILE_EXTENSIONS = (
    # C/C++
    '.c', '.h', '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx',
    # Java
    '.java',
    # Python
    '.py',
    # JavaScript/TypeScript
    '.js', '.ts', '.jsx', '.tsx',
    # Go
    '.go',
    # Rust
    '.rs',
    # Ruby
    '.rb',
    # PHP
    '.php',
    # C#
    '.cs',
    # Kotlin
    '.kt', '.kts',
    # Scala
    '.scala',
    # Swift
    '.swift',
)


def find_all_szz_candidates(repo_path: Path, fix_commit: str) -> List[str]:
    """
    Find ALL candidate BICs using SZZ: blame all deleted/modified lines.
    Returns ALL blamed commits (may contain duplicates).
    """
    blamed_commits = []
    changed_files = get_changed_files(repo_path, fix_commit)

    for filepath in changed_files:
        if not filepath.endswith(SOURCE_FILE_EXTENSIONS):
            continue

        modified_lines = get_modified_lines(repo_path, fix_commit, filepath)

        for line_num, line_content in modified_lines:
            parent_ref = f"{fix_commit}^"
            blamed_commit = get_blame_for_line(repo_path, parent_ref, filepath, line_num)

            if blamed_commit:
                blamed_commits.append(blamed_commit)

    return blamed_commits


def extract_szz_result(repo_path: Path, entry: Dict) -> SZZResult:
    """Extract SZZ candidates for a dataset entry."""
    entry_id = entry["id"]
    fix_commit = entry["fix_commit_hash"]
    gt_bics = entry.get("bug_commit_hash", [])

    # Get all blamed commits
    all_candidates = find_all_szz_candidates(repo_path, fix_commit)

    # Deduplicate while preserving order (by first occurrence)
    seen = set()
    unique_candidates = []
    for c in all_candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    # Check if ground truth is in candidates
    matching_gt = [gt for gt in gt_bics if is_commit_match(gt, set(unique_candidates)) or
                   any(is_commit_match(c, {gt}) for c in unique_candidates)]
    gt_in_candidates = len(matching_gt) > 0

    return SZZResult(
        entry_id=entry_id,
        fix_commit=fix_commit,
        ground_truth_bics=gt_bics,
        candidate_commits=all_candidates,
        unique_candidates=unique_candidates,
        gt_in_candidates=gt_in_candidates,
        matching_gt_commits=matching_gt
    )


# =============================================================================
# BIC REDACTION
# =============================================================================
def redact_commit_ids(text: str, commits_to_redact: List[str] = None) -> str:
    """
    Redact commit IDs from text.
    If commits_to_redact is provided, only redact those specific commits.
    Removes entire lines containing 'Fixes:' tags that reference those commits,
    and redacts any remaining references elsewhere.
    Otherwise, redact all hex strings that look like commit hashes.

    Handles cases where a longer hash contains the 8-char prefix of a commit to redact.
    E.g., if redacting 'cfbce435...', will also redact 'cfbce4351327f0f1a52a'.

    Also redacts any word containing 4+ character prefixes of commits to redact,
    to catch short references like 'cfbce43' that don't match the 7-40 char pattern.
    """
    if commits_to_redact:
        # Build set of 8-character prefixes for efficient lookup
        prefixes_8char = set()
        for commit in commits_to_redact:
            if len(commit) >= 8:
                prefixes_8char.add(commit[:8].lower())

        # Build set of 4-character prefixes for catching short references
        prefixes_4char = set()
        for commit in commits_to_redact:
            if len(commit) >= 4:
                prefixes_4char.add(commit[:4].lower())

        # First, remove entire lines containing "Fixes:" tags with commits to redact
        lines = text.split('\n')
        filtered_lines = []

        for line in lines:
            line_lower = line.lower()
            if 'fixes:' in line_lower:
                # Check if any commit to redact (full or prefix) is in this line
                skip_line = False
                for commit in commits_to_redact:
                    # Check full hash
                    if commit.lower() in line_lower:
                        skip_line = True
                        break
                    # Check if any 8-char prefix appears in the line
                    if len(commit) >= 8:
                        prefix = commit[:8].lower()
                        if prefix in line_lower:
                            skip_line = True
                            break
                if skip_line:
                    continue  # Skip this entire line
            filtered_lines.append(line)

        redacted = '\n'.join(filtered_lines)

        # Then, redact any hex strings that contain an 8-char prefix of commits to redact
        # This handles cases like 'cfbce4351327f0f1a52a' when redacting 'cfbce435...'
        def replace_if_contains_prefix(match):
            hex_string = match.group(0).lower()
            for prefix in prefixes_8char:
                if prefix in hex_string:
                    return "[COMMIT_HASH]"
            return match.group(0)

        # Match any hex string that looks like a commit hash (7-40 chars)
        pattern = r'\b[a-fA-F0-9]{7,40}\b'
        redacted = re.sub(pattern, replace_if_contains_prefix, redacted)

        # Additionally, redact any word containing a 4+ char prefix of commits to redact
        # This catches short references like 'cfbce43' that don't match the 7-40 char pattern
        def replace_word_if_contains_short_prefix(match):
            word = match.group(0).lower()
            for prefix in prefixes_4char:
                if prefix in word:
                    return "[COMMIT_HASH]"
            return match.group(0)

        # Match words that contain hex characters (potential partial commit references)
        # This catches things like 'commit cfbce4' or references in brackets like '(cfbce4)'
        short_pattern = r'\b[a-fA-F0-9]{4,6}\b'
        redacted = re.sub(short_pattern, replace_word_if_contains_short_prefix, redacted)

        return redacted
    else:
        # Redact all potential commit hashes
        pattern = r'\b[a-fA-F0-9]{7,40}\b'
        return re.sub(pattern, '[COMMIT_HASH]', text)


def prepare_candidate_selection_directory(
    repo_path: Path,
    fix_commit: str,
    candidates: List[str],
    gt_bics: List[str],
    entry_id: str,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Optional[Path]:
    """
    Prepare directory with files for Claude Code candidate selection analysis.
    """
    work_dir = TEMP_DIR / f"entry_{entry_id}_szz_selection"

    # Clean up if exists
    if work_dir.exists():
        shutil.rmtree(work_dir)

    work_dir.mkdir(parents=True)
    candidates_dir = work_dir / "candidates"
    candidates_dir.mkdir()

    # All commits to redact (ground truth and candidates)
    commits_to_redact = list(set(gt_bics + candidates))

    # Get and redact fix commit message (unless excluded)
    if not without_fc_message:
        commit_message = get_commit_message(repo_path, fix_commit)
        redacted_message = redact_commit_ids(commit_message, commits_to_redact)
        (work_dir / "fix_commit_message.txt").write_text(redacted_message)

    # Get and redact fix commit diff (unless excluded)
    if not without_fc_diff:
        commit_diff = get_commit_diff(repo_path, fix_commit)
        redacted_diff = redact_commit_ids(commit_diff, commits_to_redact)
        (work_dir / "fix_commit_diff.txt").write_text(redacted_diff)

    # Create candidate diffs with neutral naming
    for idx, commit in enumerate(candidates, 1):
        # Get commit message and diff
        candidate_message = get_commit_message(repo_path, commit)
        candidate_diff = get_commit_diff(repo_path, commit)

        # Combine message and diff
        full_content = f"COMMIT MESSAGE:\n{candidate_message}\n\n---\n\nDIFF:\n{candidate_diff}"

        # Redact commit hashes
        redacted_content = redact_commit_ids(full_content, commits_to_redact)

        candidate_file = candidates_dir / f"candidate_{idx:02d}.diff"
        candidate_file.write_text(redacted_content)

    # Create instructions
    instructions = create_stage01_candidate_selection_instructions(len(candidates), without_fc_message, without_fc_diff)
    (work_dir / "INSTRUCTIONS.md").write_text(instructions)

    logging.info(f"Prepared candidate selection directory: {work_dir} ({len(candidates)} candidates)")
    return work_dir


def parse_selection_result(content: str, num_candidates: int) -> Tuple[Optional[int], bool, Optional[str], Optional[str]]:
    """
    Parse the result.txt file content.

    Returns: (selected_candidate_index, abstained, confidence, explanation)
    - selected_candidate_index: 1-indexed candidate number, or None if abstained/error
    - abstained: True if LLM chose "NONE"
    - confidence: HIGH, MEDIUM, LOW or None
    - explanation: Truncated explanation or None
    """
    selected = None
    abstained = False
    confidence = None
    explanation = None

    lines = content.split('\n')

    for i, line in enumerate(lines):
        line_upper = line.upper().strip()

        # Parse SELECTED line
        if line_upper.startswith("SELECTED:"):
            selection_part = line.split(":", 1)[1].strip().upper()

            if "NONE" in selection_part:
                abstained = True
            else:
                # Find candidate number
                match = re.search(r'CANDIDATE[_\s]*(\d+)', selection_part, re.IGNORECASE)
                if match:
                    try:
                        candidate_num = int(match.group(1))
                        if 1 <= candidate_num <= num_candidates:
                            selected = candidate_num
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

    return selected, abstained, confidence, explanation


def invoke_claude_selection(
    work_dir: Path,
    num_candidates: int,
    entry_id: str,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm"
) -> LLMSelectionResult:
    """
    Invoke an LLM agent to perform candidate selection.
    """
    prompt = "Read INSTRUCTIONS.md and follow exactly. Write result to result.txt"
    cmd, env, timeout = build_agent_command(agent, model, prompt, base_url, api_key)

    for attempt in range(MAX_RETRIES):
        try:
            # Log agent output to a file
            call_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            log_file = AGENT_LOGS_DIR / f"s01_{entry_id}_selection_{call_ts}.log"
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

            logging.info(f"[{entry_id}] Claude agent log: {log_file}")

            # Check if result.txt was created
            result_file = work_dir / "result.txt"
            if not result_file.exists():
                return LLMSelectionResult(
                    selected_commit=None,
                    abstained=False,
                    confidence=None,
                    explanation=None,
                    error="No result.txt created"
                )

            # Parse result
            result_content = result_file.read_text()
            selected_idx, abstained, confidence, explanation = parse_selection_result(
                result_content, num_candidates
            )

            if abstained:
                return LLMSelectionResult(
                    selected_commit=None,
                    abstained=True,
                    confidence=confidence,
                    explanation=explanation,
                    error=None
                )

            if selected_idx is None:
                return LLMSelectionResult(
                    selected_commit=None,
                    abstained=False,
                    confidence=confidence,
                    explanation=explanation,
                    error="Could not parse selection from result"
                )

            # Return the index (we'll map to commit hash in the caller)
            return LLMSelectionResult(
                selected_commit=str(selected_idx),  # Temporarily store index as string
                abstained=False,
                confidence=confidence,
                explanation=explanation,
                error=None
            )

        except subprocess.TimeoutExpired:
            return LLMSelectionResult(
                selected_commit=None,
                abstained=False,
                confidence=None,
                explanation=None,
                error=f"Timeout after {CLAUDE_TIMEOUT}s"
            )
        except FileNotFoundError:
            return LLMSelectionResult(
                selected_commit=None,
                abstained=False,
                confidence=None,
                explanation=None,
                error="Claude CLI not found"
            )
        except Exception as e:
            if "overload" in str(e).lower():
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"API overloaded, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
            return LLMSelectionResult(
                selected_commit=None,
                abstained=False,
                confidence=None,
                explanation=None,
                error=str(e)
            )

    return LLMSelectionResult(
        selected_commit=None,
        abstained=False,
        confidence=None,
        explanation=None,
        error="Max retries exceeded"
    )


# =============================================================================
# MAIN PROCESSING
# =============================================================================
def process_entry(
    repo_path: Path,
    entry: Dict,
    model: str,
    idx: int,
    total: int,
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm",
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> FullResult:
    """Process a single dataset entry."""
    entry_id = entry["id"]
    fix_commit = entry["fix_commit_hash"]
    gt_bics = entry.get("bug_commit_hash", [])

    print(f"\n[{idx}/{total}] Processing entry {entry_id} (fix: {fix_commit[:8]})")
    logging.info(f"[{idx}/{total}] Processing entry {entry_id}")

    # Step 1: Run SZZ to get candidates
    szz_result = extract_szz_result(repo_path, entry)

    num_candidates = len(szz_result.unique_candidates)
    gt_in_candidates = szz_result.gt_in_candidates

    # Log SZZ results
    gt_status = "YES" if gt_in_candidates else "NO"
    print(f"  SZZ: {num_candidates} unique candidates, GT in candidates: {gt_status}")
    if gt_in_candidates:
        print(f"  Matching GT commits: {[c[:8] for c in szz_result.matching_gt_commits]}")
    logging.info(f"[{entry_id}] SZZ found {num_candidates} candidates, GT in candidates: {gt_in_candidates}")

    # Initialize result
    result = FullResult(
        entry_id=entry_id,
        fix_commit=fix_commit,
        ground_truth_bics=gt_bics,
        szz_candidates=szz_result.unique_candidates,
        gt_in_candidates=gt_in_candidates,
        matching_gt_commits=szz_result.matching_gt_commits,
        llm_selected_commit=None,
        llm_abstained=False,
        llm_confidence=None,
        llm_explanation=None,
        llm_correct=None,
        llm_error=None
    )

    if num_candidates == 0:
        print(f"  No candidates found, skipping LLM selection")
        logging.info(f"[{entry_id}] No candidates - skipping LLM selection")
        return result

    # Step 2: Prepare directory and invoke Claude
    print(f"  Preparing candidate selection for {num_candidates} candidates...")
    work_dir = prepare_candidate_selection_directory(
        repo_path,
        fix_commit,
        szz_result.unique_candidates,
        gt_bics,
        entry_id,
        without_fc_message=without_fc_message,
        without_fc_diff=without_fc_diff
    )

    if not work_dir:
        result.llm_error = "Failed to prepare analysis directory"
        return result

    agent_name = {"claude-code": "Claude", "openhands": "OpenHands"}[agent]
    print(f"  Invoking {agent_name} for candidate selection...")
    llm_result = invoke_claude_selection(work_dir, num_candidates, entry_id, model,
                                         agent=agent, base_url=base_url, api_key=api_key)

    result.llm_abstained = llm_result.abstained
    result.llm_confidence = llm_result.confidence
    result.llm_explanation = llm_result.explanation
    result.llm_error = llm_result.error

    if llm_result.error:
        print(f"  LLM ERROR: {llm_result.error}")
        logging.error(f"[{entry_id}] LLM error: {llm_result.error}")
        return result

    if llm_result.abstained:
        print(f"  LLM: Selected NONE (abstained), confidence: {llm_result.confidence}")
        logging.info(f"[{entry_id}] LLM abstained (selected NONE)")
        # Evaluate: abstaining is "correct" if GT is NOT in candidates
        result.llm_correct = not gt_in_candidates
        correctness = "CORRECT" if result.llm_correct else "INCORRECT"
        print(f"  Evaluation: {correctness} (GT in candidates: {gt_in_candidates})")
        return result

    # Map selected index to commit hash
    try:
        selected_idx = int(llm_result.selected_commit)
        selected_commit = szz_result.unique_candidates[selected_idx - 1]  # 1-indexed
        result.llm_selected_commit = selected_commit
    except (ValueError, IndexError) as e:
        result.llm_error = f"Invalid selection index: {llm_result.selected_commit}"
        logging.error(f"[{entry_id}] Invalid selection: {e}")
        return result

    # Evaluate correctness
    is_correct = is_commit_match(selected_commit, set(gt_bics))
    result.llm_correct = is_correct

    correctness = "CORRECT" if is_correct else "INCORRECT"
    print(f"  LLM: Selected candidate_{selected_idx:02d} ({selected_commit[:8]}), confidence: {llm_result.confidence}")
    print(f"  Evaluation: {correctness}")
    logging.info(f"[{entry_id}] LLM selected {selected_commit[:8]}, correct: {is_correct}")

    return result


def print_aggregate_statistics(results: List[FullResult]):
    """Print aggregate statistics."""
    print("\n" + "=" * 80)
    print("                    AGGREGATE STATISTICS")
    print("=" * 80)

    total = len(results)

    # SZZ Statistics
    print("\n### SZZ CANDIDATE EXTRACTION ###")
    print("-" * 40)

    entries_with_candidates = sum(1 for r in results if len(r.szz_candidates) > 0)
    total_candidates = sum(len(r.szz_candidates) for r in results)
    avg_candidates = total_candidates / total if total > 0 else 0

    gt_in_candidates_count = sum(1 for r in results if r.gt_in_candidates)
    gt_in_candidates_rate = gt_in_candidates_count / total if total > 0 else 0

    print(f"Total entries processed:         {total}")
    print(f"Entries with candidates:         {entries_with_candidates}")
    print(f"Total candidates found:          {total_candidates}")
    print(f"Average candidates per entry:    {avg_candidates:.2f}")
    print(f"")
    print(f"Ground truth in candidates:      {gt_in_candidates_count}/{total} ({gt_in_candidates_rate:.2%})")

    # Candidate count distribution
    if results:
        candidate_counts = [len(r.szz_candidates) for r in results]
        print(f"Min candidates:                  {min(candidate_counts)}")
        print(f"Max candidates:                  {max(candidate_counts)}")

    # LLM Statistics
    print("\n### LLM SELECTION ###")
    print("-" * 40)

    llm_processed = [r for r in results if r.llm_error is None and len(r.szz_candidates) > 0]
    llm_errors = sum(1 for r in results if r.llm_error is not None)
    llm_abstained = sum(1 for r in llm_processed if r.llm_abstained)
    llm_selected = [r for r in llm_processed if not r.llm_abstained and r.llm_selected_commit]

    print(f"LLM calls made:                  {len(llm_processed)}")
    print(f"LLM errors:                      {llm_errors}")
    print(f"LLM abstained (selected NONE):   {llm_abstained}")
    print(f"LLM made selection:              {len(llm_selected)}")

    # Correctness statistics
    if llm_processed:
        correct_selections = sum(1 for r in llm_selected if r.llm_correct is True)
        incorrect_selections = sum(1 for r in llm_selected if r.llm_correct is False)

        # For abstentions: correct if GT was not in candidates
        correct_abstentions = sum(1 for r in llm_processed if r.llm_abstained and r.llm_correct is True)
        incorrect_abstentions = sum(1 for r in llm_processed if r.llm_abstained and r.llm_correct is False)

        total_correct = correct_selections + correct_abstentions
        total_evaluated = len(llm_processed)
        accuracy = total_correct / total_evaluated if total_evaluated > 0 else 0

        print(f"\n### ACCURACY ###")
        print("-" * 40)
        print(f"Overall accuracy:                {total_correct}/{total_evaluated} ({accuracy:.2%})")
        print(f"")
        print(f"Selections:")
        print(f"  Correct selections:            {correct_selections}")
        print(f"  Incorrect selections:          {incorrect_selections}")
        if llm_selected:
            selection_accuracy = correct_selections / len(llm_selected)
            print(f"  Selection accuracy:            {selection_accuracy:.2%}")
        print(f"")
        print(f"Abstentions:")
        print(f"  Correct abstentions:           {correct_abstentions} (GT not in candidates)")
        print(f"  Incorrect abstentions:         {incorrect_abstentions} (GT was in candidates)")

        # Confidence distribution
        confidence_dist = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "Unknown": 0}
        for r in llm_processed:
            if r.llm_confidence:
                confidence_dist[r.llm_confidence] = confidence_dist.get(r.llm_confidence, 0) + 1
            else:
                confidence_dist["Unknown"] += 1

        print(f"\n### CONFIDENCE DISTRIBUTION ###")
        print("-" * 40)
        for level, count in confidence_dist.items():
            if count > 0:
                print(f"{level}:                           {count}")

    print("\n" + "=" * 80)


def save_results(
    results: List[FullResult],
    original_sample: List[Dict],
    timestamp: str,
    sample_file: str,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    without_fc_message: bool = False,
    without_fc_diff: bool = False
):
    """Save results to the results directory."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    output_file = RESULTS_DIR / f"szz_agent_stage_01_{timestamp}.json"

    # Calculate summary statistics
    total = len(results)
    gt_in_candidates_count = sum(1 for r in results if r.gt_in_candidates)

    llm_processed = [r for r in results if r.llm_error is None and len(r.szz_candidates) > 0]
    correct = sum(1 for r in llm_processed if r.llm_correct is True)
    accuracy = correct / len(llm_processed) if llm_processed else None

    # Build output data
    output_data = {
        "metadata": {
            "timestamp": timestamp,
            "sample_size": len(original_sample),
            "sample_file": sample_file,
            "model": model,
            "agent": agent,
            "without_fc_message": without_fc_message,
            "without_fc_diff": without_fc_diff,
        },
        "summary": {
            "total_entries": total,
            "gt_in_szz_candidates": gt_in_candidates_count,
            "gt_in_szz_candidates_rate": gt_in_candidates_count / total if total > 0 else 0,
            "llm_accuracy": accuracy,
        },
        "results": []
    }

    # Add individual results
    for i, result in enumerate(results):
        entry_data = {
            # Original data
            "id": result.entry_id,
            "fix_commit_hash": result.fix_commit,
            "bug_commit_hash": result.ground_truth_bics,

            # SZZ results
            "szz_candidates": result.szz_candidates,
            "szz_num_candidates": len(result.szz_candidates),
            "gt_in_szz_candidates": result.gt_in_candidates,
            "matching_gt_in_candidates": result.matching_gt_commits,

            # LLM results
            "llm_selected_commit": result.llm_selected_commit,
            "llm_abstained": result.llm_abstained,
            "llm_confidence": result.llm_confidence,
            "llm_explanation": result.llm_explanation,
            "llm_correct": result.llm_correct,
            "llm_error": result.llm_error,
        }

        # Copy any additional fields from original sample
        if i < len(original_sample):
            for key in original_sample[i]:
                if key not in entry_data:
                    entry_data[key] = original_sample[i][key]

        output_data["results"].append(entry_data)

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nResults saved to: {output_file}")
    logging.info(f"Results saved to: {output_file}")

    return output_file


# =============================================================================
# MAIN
# =============================================================================
def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SZZ algorithm with LLM-based candidate selection"
    )
    parser.add_argument(
        "--sample-file", "-s",
        type=str,
        default=str(DEFAULT_SAMPLE_FILE),
        help=f"Path to the sample dataset JSON file (default: {DEFAULT_SAMPLE_FILE})"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of entries to process (for testing)"
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
        "--skip-cleanup",
        action="store_true",
        help="Skip cleanup of temp directories after processing"
    )
    parser.add_argument(
        "--without-fc-message",
        action="store_true",
        help="Do not provide the fix commit message as context in the LLM prompts"
    )
    parser.add_argument(
        "--without-fc-diff",
        action="store_true",
        help="Do not provide the fix commit diff as context in the LLM prompts (SZZ still uses it internally)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = setup_logging()

    print("=" * 80)
    print("           SZZ with LLM Candidate Selection")
    print("=" * 80)
    print(f"Mode: Full (SZZ + LLM)")
    print(f"Log file: {log_file}")

    logging.info(f"Starting execution")
    logging.info(f"Agent: {args.agent}")
    logging.info(f"Model: {args.model}")

    # Check agent CLI exists
    if not verify_agent_cli(args.agent):
        return

    # Load sample data
    sample_file = Path(args.sample_file)
    if not sample_file.exists():
        print(f"\nERROR: Sample file not found: {sample_file}")
        logging.error(f"Sample file not found: {sample_file}")
        return

    print(f"\nLoading sample from {sample_file}...")
    logging.info(f"Loading sample from {sample_file}")

    with open(sample_file, 'r') as f:
        sample = json.load(f)

    print(f"Loaded {len(sample)} entries from sample file")

    # Apply limit if specified (for testing)
    if args.limit is not None and args.limit > 0:
        sample = sample[:args.limit]
        print(f"Limited to {len(sample)} entries")

    logging.info(f"Processing {len(sample)} entries")

    # Create repos directory if it doesn't exist
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure all repositories exist (clone if needed)
    failed_repos = ensure_repos_exist(sample, REPOS_DIR)
    if failed_repos:
        original_count = len(sample)
        sample = [e for e in sample if e.get("repo_name", "linux-kernel") not in failed_repos]
        print(f"\nFiltered out {original_count - len(sample)} entries due to missing repos")
        print(f"Proceeding with {len(sample)} entries")

    # Setup directories
    if not args.skip_cleanup and TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Process entries
    results: List[FullResult] = []

    for idx, entry in enumerate(sample, 1):
        repo_path = get_repo_path(entry, REPOS_DIR)
        result = process_entry(
            repo_path,
            entry,
            args.model,
            idx,
            len(sample),
            agent=args.agent,
            base_url=args.base_url,
            api_key=args.api_key,
            without_fc_message=args.without_fc_message,
            without_fc_diff=args.without_fc_diff
        )
        results.append(result)

        # Print running statistics every 10 entries
        if idx % 10 == 0 or idx == len(sample):
            gt_in_candidates = sum(1 for r in results if r.gt_in_candidates)
            print(f"\n  --- Running stats: {idx}/{len(sample)} processed, "
                  f"GT in candidates: {gt_in_candidates}/{idx} ({gt_in_candidates/idx:.1%}) ---")

    # Print aggregate statistics
    print_aggregate_statistics(results)

    # Save results
    save_results(results, sample, timestamp, args.sample_file,
                 model=args.model, agent=args.agent,
                 without_fc_message=args.without_fc_message,
                 without_fc_diff=args.without_fc_diff)

    # Cleanup
    if not args.skip_cleanup and TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
        print("\nCleaned up temp directory")
        logging.info("Cleaned up temp directory")

    print("\nDone!")
    logging.info("Execution completed")


if __name__ == "__main__":
    main()
