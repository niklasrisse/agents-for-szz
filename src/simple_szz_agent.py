#!/usr/bin/env python3
"""
Simple SZZ Agent: Directly select the bug-introducing commit from file history candidates.

This script combines and simplifies szz_agent_stage_01.py and szz_agent_stage_02.py:
1. Determines files touched by the fix commit
2. Builds candidate commit set from file histories (as in stage 02)
3. Directly presents ALL candidates to the LLM for selection (no binary search)

Usage:
    python simple_szz_agent.py                              # Full run with default dataset
    python simple_szz_agent.py --limit 10                   # Process only 10 entries
    python simple_szz_agent.py -d sampled_datasets/DS_GITHUB-j.json  # Use different dataset
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

from prompts import create_candidate_selection_instructions

# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# =============================================================================
# CONFIGURATION
# =============================================================================
REPOS_DIR = PROJECT_ROOT / "repos"
DEFAULT_DATASET = PROJECT_ROOT / "sampled_datasets/DS_LINUX-26_100_42.json"
TEMP_DIR = PROJECT_ROOT / "temp_analysis"
RESULTS_DIR = PROJECT_ROOT / "results"
LOGS_DIR = PROJECT_ROOT / "logs"
AGENT_LOGS_DIR = PROJECT_ROOT / "agent_logs"
RANDOM_SEED = 42
GIT_TIMEOUT = 300  # seconds
CLAUDE_TIMEOUT = 600  # 10 min per invocation
OPENHANDS_TIMEOUT = 1800  # 30 min per invocation
MAX_RETRIES = 1
RETRY_DELAY = 60  # seconds

# OpenRouter model aliases and settings
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL_MAP = {
    "kimi-k2.5": "moonshotai/kimi-k2.5",
    "minimax-m2.5": "minimax/minimax-m2.5",
    "glm-5": "z-ai/glm-5",
    "deepseek-v3.2": "deepseek/deepseek-v3.2",
    "moonshotai/kimi-k2.5": "moonshotai/kimi-k2.5",
    "minimax/minimax-m2.5": "minimax/minimax-m2.5",
    "qwen3.5-plus-02-15": "qwen/qwen3.5-plus-02-15",
    "qwen/qwen3.5-plus-02-15": "qwen/qwen3.5-plus-02-15",
    "claude-opus-4.5": "anthropic/claude-opus-4.5",
    "anthropic/claude-opus-4.5": "anthropic/claude-opus-4.5",
}


def resolve_openrouter_model(model: str, backend: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """If the model should go via OpenRouter, return (resolved_model, base_url, api_key).
    Otherwise return (model, None, None).

    Routes via OpenRouter when:
    - The model is in OPENROUTER_MODEL_MAP (always OpenRouter), or
    - backend="openrouter" is explicitly set (any model via OpenRouter)
    """
    if model in OPENROUTER_MODEL_MAP:
        resolved = OPENROUTER_MODEL_MAP[model]
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        return resolved, OPENROUTER_BASE_URL, api_key
    if backend == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        return model, OPENROUTER_BASE_URL, api_key
    return model, None, None


# Import evaluation utilities
from evaluation_utils import evaluate_results, print_summary as print_eval_summary


# =============================================================================
# AGENT INVOCATION HELPERS
# =============================================================================
def build_agent_command(agent: str, model: str, prompt: str, base_url: Optional[str] = None, api_key: str = "local-llm", backend: Optional[str] = None) -> Tuple[List[str], dict, int]:
    """Build the subprocess command, environment, and timeout for the chosen agent."""
    env = os.environ.copy()

    # Resolve OpenRouter models (overrides base_url and api_key if applicable)
    or_model, or_base_url, or_api_key = resolve_openrouter_model(model, backend=backend)
    if or_base_url:
        model = or_model
        base_url = or_base_url
        api_key = or_api_key

    if agent == "claude-code":
        cmd = [
            "claude", "-p", "--dangerously-skip-permissions",
            "--disallowedTools", "WebFetch,WebSearch",
            "--model", model, prompt
        ]
        return cmd, env, CLAUDE_TIMEOUT

    elif agent == "openhands":
        # Determine the correct litellm provider prefix
        if base_url and "openrouter" in base_url:
            # OpenRouter uses openai-compatible API, use openrouter/ prefix for litellm
            llm_model = f"openrouter/{model}"
        elif model.startswith(("openai/", "anthropic/", "litellm/", "openrouter/")):
            llm_model = model
        elif model.startswith("claude-"):
            llm_model = f"anthropic/{model}"
        else:
            llm_model = f"openai/{model}"
        env["LLM_MODEL"] = llm_model
        # Only set LLM_API_KEY if a real key was provided (not the default placeholder)
        if api_key and api_key != "local-llm":
            env["LLM_API_KEY"] = api_key
        elif llm_model.startswith("anthropic/"):
            # Use ANTHROPIC_API_KEY from environment if available
            anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if anthropic_key:
                env["LLM_API_KEY"] = anthropic_key
        else:
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
        return True


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
    """Clone a repository from GitHub to the repos directory."""
    repo_url = f"https://github.com/{repo_name}.git"
    repo_path = repos_dir / repo_name
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"  Cloning {repo_name} from {repo_url}...")
    try:
        result = subprocess.run(
            ["git", "clone", "--quiet", repo_url, str(repo_path)],
            capture_output=True, text=True, timeout=600
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
    """Ensure all repositories needed for the sample exist. Returns list of failed repos."""
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
    """Get the repository path for a given entry."""
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
    file_path: str


@dataclass
class TurnStats:
    """Token statistics for a single API turn."""
    turn_number: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    tool_calls: List[Dict] = field(default_factory=list)  # tool call dicts with name + input params

    def to_dict(self) -> Dict:
        return {
            "turn_number": self.turn_number,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "tool_calls": self.tool_calls,
        }


@dataclass
class CallStats:
    """Statistics from a single Claude Code invocation."""
    duration_s: Optional[float] = None
    duration_api_s: Optional[float] = None
    num_turns: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    total_cost_usd: Optional[float] = None
    # Tool usage
    total_tool_calls: int = 0
    tool_calls_by_name: Dict[str, int] = field(default_factory=dict)
    # Per-turn breakdown
    turns: List[TurnStats] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "duration_s": self.duration_s,
            "duration_api_s": self.duration_api_s,
            "num_turns": self.num_turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "total_cost_usd": self.total_cost_usd,
            "total_tool_calls": self.total_tool_calls,
            "tool_calls_by_name": self.tool_calls_by_name,
            "turns": [t.to_dict() for t in self.turns],
        }


@dataclass
class EntryResult:
    """Result for a single dataset entry."""
    entry_id: str
    fix_commit: str
    ground_truth_bics: List[str]
    total_candidates: int
    gt_in_candidates: bool
    selected_commit: Optional[str]
    selected_index: Optional[int]
    confidence: Optional[str]
    explanation: Optional[str]
    is_correct: Optional[bool]
    num_files_touched_by_fix: int = 0
    num_source_files_touched_by_fix: int = 0
    files_analyzed: List[str] = field(default_factory=list)
    call_stats: Optional[CallStats] = None
    error: Optional[str] = None


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> Path:
    """Setup logging to file and console."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"simple_szz_agent_{timestamp}.log"

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
            cmd, capture_output=True, text=True, timeout=timeout, errors='replace'
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
    """Get complete commit history for a file, tracking renames and file paths. Oldest first."""
    stdout, rc = git_cmd(
        repo_path, "log", "--follow", "--format=%H %ct", "--name-status", until_commit, "--", file_path
    )
    if rc != 0 or not stdout.strip():
        return []

    entries = []
    current_hash = None
    current_timestamp = None

    lines = stdout.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 40:
            try:
                current_hash = parts[0]
                current_timestamp = int(parts[1])
                i += 1
                continue
            except ValueError:
                pass

        if current_hash and current_timestamp:
            status_parts = line.split('\t')
            if len(status_parts) >= 2:
                status = status_parts[0]
                if status.startswith('R'):
                    old_path = status_parts[1]
                    entries.append(FileHistoryEntry(hash=current_hash, timestamp=current_timestamp, file_path=old_path))
                else:
                    entries.append(FileHistoryEntry(hash=current_hash, timestamp=current_timestamp, file_path=status_parts[1]))
            current_hash = None
            current_timestamp = None

        i += 1

    entries.sort(key=lambda e: e.timestamp)
    return entries


def build_file_rename_timeline(repo_path: Path, file_path: str, until_commit: str) -> List[Tuple[int, str]]:
    """Build a timeline of (timestamp, path) pairs for a file, tracking renames."""
    history = get_file_history_with_paths(repo_path, file_path, until_commit)
    if not history:
        return [(0, file_path)]

    timeline: List[Tuple[int, str]] = []
    for entry in history:
        if not timeline or timeline[-1][1] != entry.file_path:
            timeline.append((entry.timestamp, entry.file_path))

    return timeline


def get_file_path_at_timestamp(timeline: List[Tuple[int, str]], timestamp: int) -> Optional[str]:
    """Given a rename timeline, find the file path at a specific timestamp."""
    if not timeline:
        return None

    result_path = None
    for ts, path in timeline:
        if ts <= timestamp:
            result_path = path
        else:
            break

    return result_path


def build_file_path_mapping(repo_path: Path, fix_commit: str) -> Tuple[List[CommitInfo], Dict[str, Dict[str, str]]]:
    """Build candidate commit list and mapping of commit -> {current_file_path: path_at_that_commit}."""
    files = get_files_from_commit(repo_path, fix_commit)
    if not files:
        return [], {}

    binary_extensions = (
        '.o', '.ko', '.a', '.so', '.bin', '.elf', '.exe', '.dll',
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',
        '.pdf', '.doc', '.docx',
        '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z',
        '.pyc', '.pyo', '.class',
    )
    analyzable_files = [f for f in files if not f.endswith(binary_extensions)]

    if not analyzable_files:
        logging.warning(f"No analyzable files in fix commit {fix_commit}")
        return [], {}

    all_commits: Dict[str, CommitInfo] = {}
    file_timelines: Dict[str, List[Tuple[int, str]]] = {}

    for current_file_path in analyzable_files:
        history = get_file_history_with_paths(repo_path, current_file_path, fix_commit)
        for entry in history:
            if entry.hash not in all_commits:
                all_commits[entry.hash] = CommitInfo(hash=entry.hash, timestamp=entry.timestamp)
        file_timelines[current_file_path] = build_file_rename_timeline(repo_path, current_file_path, fix_commit)

    sorted_commits = sorted(all_commits.values(), key=lambda c: c.timestamp)
    if not sorted_commits:
        return [], {}

    path_mapping: Dict[str, Dict[str, str]] = {}
    for commit in sorted_commits:
        path_mapping[commit.hash] = {}
        for current_file_path in analyzable_files:
            timeline = file_timelines.get(current_file_path, [])
            path_at_commit = get_file_path_at_timestamp(timeline, commit.timestamp)
            if path_at_commit:
                path_mapping[commit.hash][current_file_path] = path_at_commit

    return sorted_commits, path_mapping


def get_commit_message(repo_path: Path, commit: str) -> str:
    """Get the full commit message for a commit."""
    stdout, rc = git_cmd(repo_path, "log", "-1", "--format=%B", commit)
    if rc != 0:
        return ""
    return stdout.strip()


def get_commit_diff(repo_path: Path, commit: str) -> str:
    """Get the diff of a commit."""
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


def find_all_bic_positions(bic_commits: List[str], candidates: List[CommitInfo]) -> List[int]:
    """Find positions of ALL BICs in candidate list."""
    bic_set = set(bic_commits)
    positions = []
    for idx, commit in enumerate(candidates):
        if is_commit_match(commit.hash, bic_set):
            positions.append(idx)
    return positions


# =============================================================================
# BIC REDACTION
# =============================================================================
def redact_bic_from_message(message: str, bic_commits: List[str]) -> str:
    """Remove BIC commit hashes from text."""
    prefixes_8char = set()
    for bic in bic_commits:
        if len(bic) >= 8:
            prefixes_8char.add(bic[:8].lower())

    prefixes_4char = set()
    for bic in bic_commits:
        if len(bic) >= 4:
            prefixes_4char.add(bic[:4].lower())

    lines = message.split('\n')
    filtered_lines = []

    for line in lines:
        line_lower = line.lower()
        if 'fixes:' in line_lower:
            skip_line = False
            for bic in bic_commits:
                if bic.lower() in line_lower:
                    skip_line = True
                    break
                if len(bic) >= 8:
                    prefix = bic[:8].lower()
                    if prefix in line_lower:
                        skip_line = True
                        break
            if skip_line:
                continue
        filtered_lines.append(line)

    redacted = '\n'.join(filtered_lines)

    def replace_if_contains_prefix(match):
        hex_string = match.group(0).lower()
        for prefix in prefixes_8char:
            if prefix in hex_string:
                return "[REDACTED_COMMIT]"
        return match.group(0)

    pattern = r'\b[a-fA-F0-9]{7,40}\b'
    redacted = re.sub(pattern, replace_if_contains_prefix, redacted)

    def replace_word_if_contains_short_prefix(match):
        word = match.group(0).lower()
        for prefix in prefixes_4char:
            if prefix in word:
                return "[REDACTED_COMMIT]"
        return match.group(0)

    short_pattern = r'\b[a-fA-F0-9]{4,6}\b'
    redacted = re.sub(short_pattern, replace_word_if_contains_short_prefix, redacted)

    return redacted


# =============================================================================
# CANDIDATE SELECTION
# =============================================================================
def prepare_candidate_selection_directory(
    repo_path: Path,
    fix_commit: str,
    candidate_commits: List[CommitInfo],
    bic_commits: List[str],
    entry_id: str,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Optional[Path]:
    """Prepare directory with files for LLM candidate selection analysis."""
    work_dir = TEMP_DIR / f"entry_{entry_id}_candidate_selection"

    if work_dir.exists():
        shutil.rmtree(work_dir)

    work_dir.mkdir(parents=True)
    candidates_dir = work_dir / "candidates"
    candidates_dir.mkdir()

    # Get and redact fix commit message
    if not without_fc_message:
        commit_message = get_commit_message(repo_path, fix_commit)
        redacted_message = redact_bic_from_message(commit_message, bic_commits)
        (work_dir / "fix_commit_message.txt").write_text(redacted_message)

    # Get and redact fix commit diff
    if not without_fc_diff:
        commit_diff = get_commit_diff(repo_path, fix_commit)
        redacted_diff = redact_bic_from_message(commit_diff, bic_commits)
        (work_dir / "fix_commit_diff.txt").write_text(redacted_diff)

    # Pre-compile regex for candidate commit hash redaction
    hash_prefixes = set()
    for c in candidate_commits:
        if len(c.hash) >= 7:
            hash_prefixes.add(re.escape(c.hash[:7]))

    if hash_prefixes:
        prefix_pattern = '|'.join(sorted(hash_prefixes, key=len, reverse=True))
        candidate_hash_re = re.compile(r'\b(?:' + prefix_pattern + r')[a-f0-9]*\b')
    else:
        candidate_hash_re = None

    # Create candidate diffs
    num_candidates = len(candidate_commits)
    for idx, commit in enumerate(candidate_commits, 1):
        candidate_diff = get_commit_diff(repo_path, commit.hash)
        redacted_candidate_diff = redact_bic_from_message(candidate_diff, bic_commits)

        if candidate_hash_re:
            redacted_candidate_diff = candidate_hash_re.sub("[COMMIT_HASH]", redacted_candidate_diff)

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
    """Parse result.txt from candidate selection.

    Returns: (selected_candidates, confidence, explanation)
    """
    selected = []
    confidence = None
    explanation = None

    lines = content.split('\n')

    for i, line in enumerate(lines):
        line_upper = line.upper().strip()

        if line_upper.startswith("SELECTED:"):
            selection_part = line.split(":", 1)[1].strip()
            matches = re.findall(r'candidate[_\s]*(\d+)', selection_part, re.IGNORECASE)
            for match in matches:
                try:
                    selected.append(int(match))
                except ValueError:
                    pass

        elif line_upper.startswith("CONFIDENCE:"):
            conf_part = line.split(":", 1)[1].strip().upper()
            for level in ["HIGH", "MEDIUM", "LOW"]:
                if level in conf_part:
                    confidence = level
                    break

        elif line_upper.startswith("EXPLANATION:"):
            explanation_lines = []
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j].strip():
                    explanation_lines.append(lines[j].strip())
            explanation = " ".join(explanation_lines)[:500]

    return selected, confidence, explanation


def parse_openhands_stats(raw_output: str) -> CallStats:
    """Parse OpenHands output to extract call statistics.

    Extracts stats from two sources:
    1. The conversation log (stdout) to get the Conversation ID
    2. The OpenHands conversation storage (~/.openhands/conversations/<id>/)
       - base_state.json: accumulated tokens, costs, per-turn latencies
       - events/: tool calls per turn

    Falls back to --json JSONL output if available.
    """
    stats = CallStats()

    # Try to extract conversation ID from the raw output
    # OpenHands prints: "Conversation ID: <hex_id>"
    conv_id = None
    # Strip ANSI codes for matching
    ansi_re = re.compile(r'\x1b\[[0-9;]*m')
    clean_output = ansi_re.sub('', raw_output)

    conv_match = re.search(r'Conversation ID:\s*([a-f0-9]+)', clean_output)
    if conv_match:
        conv_id = conv_match.group(1)

    if not conv_id:
        logging.warning("Could not extract OpenHands conversation ID from output")
        # Try JSONL parsing as fallback
        return _parse_openhands_jsonl(raw_output)

    # Read base_state.json from the conversation directory
    conv_dir = Path.home() / ".openhands" / "conversations" / conv_id
    base_state_path = conv_dir / "base_state.json"

    if not base_state_path.exists():
        logging.warning(f"OpenHands base_state.json not found at {base_state_path}")
        return _parse_openhands_jsonl(raw_output)

    try:
        with open(base_state_path) as f:
            base_state = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logging.warning(f"Could not read OpenHands base_state.json: {e}")
        return _parse_openhands_jsonl(raw_output)

    # Extract stats from base_state
    agent_metrics = base_state.get("stats", {}).get("usage_to_metrics", {}).get("agent", {})

    if not agent_metrics:
        logging.warning("No agent metrics found in OpenHands base_state.json")
        return stats

    # Accumulated token usage
    token_usage = agent_metrics.get("accumulated_token_usage", {})
    stats.input_tokens = token_usage.get("prompt_tokens") or None
    stats.output_tokens = token_usage.get("completion_tokens") or None
    stats.cache_read_input_tokens = token_usage.get("cache_read_tokens") or None
    stats.cache_creation_input_tokens = token_usage.get("cache_write_tokens") or None

    # Cost — try accumulated_cost first, then sum per-turn costs as fallback
    accumulated_cost = agent_metrics.get("accumulated_cost", 0)
    if accumulated_cost and accumulated_cost > 0:
        stats.total_cost_usd = accumulated_cost
    else:
        # Sum per-turn costs if available
        per_turn_costs = agent_metrics.get("costs", [])
        if per_turn_costs:
            total = sum(c.get("cost", 0) for c in per_turn_costs if isinstance(c, dict))
            if total > 0:
                stats.total_cost_usd = total

    # Per-turn latencies and token usages
    response_latencies = agent_metrics.get("response_latencies", [])
    token_usages = agent_metrics.get("token_usages", [])
    stats.num_turns = len(response_latencies)

    # Sum up API latencies to get total API duration
    if response_latencies:
        total_api_s = sum(rl.get("latency", 0) for rl in response_latencies)
        stats.duration_api_s = total_api_s

    # Build per-turn stats from token_usages and latencies
    for turn_idx in range(max(len(token_usages), len(response_latencies))):
        turn = TurnStats(turn_number=turn_idx + 1)
        if turn_idx < len(token_usages):
            tu = token_usages[turn_idx]
            turn.input_tokens = tu.get("prompt_tokens", 0)
            turn.output_tokens = tu.get("completion_tokens", 0)
            turn.cache_read_input_tokens = tu.get("cache_read_tokens", 0)
            turn.cache_creation_input_tokens = tu.get("cache_write_tokens", 0)
        stats.turns.append(turn)

    # Parse events to extract tool calls
    events_dir = conv_dir / "events"
    if events_dir.exists():
        event_files = sorted(
            [f for f in events_dir.iterdir() if f.name.startswith("event-") and f.suffix == ".json"]
        )
        # Map llm_response_id to turn index for associating tool calls with turns
        response_id_to_turn: Dict[str, int] = {}
        for turn_idx, rl in enumerate(response_latencies):
            rid = rl.get("response_id", "")
            if rid:
                response_id_to_turn[rid] = turn_idx

        for ef in event_files:
            try:
                with open(ef) as f:
                    event = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            if event.get("kind") != "ActionEvent" or event.get("source") != "agent":
                continue

            tool_name = event.get("tool_name", "")
            if not tool_name:
                continue

            tool_info = {"name": tool_name}

            # Extract tool call input details from the action dict
            action = event.get("action", {})
            if isinstance(action, dict):
                # Build input dict, excluding internal keys
                tool_input = {k: v for k, v in action.items() if k != "kind"}
                if tool_input:
                    tool_info["input"] = tool_input

            stats.total_tool_calls += 1
            stats.tool_calls_by_name[tool_name] = stats.tool_calls_by_name.get(tool_name, 0) + 1

            # Associate tool call with the right turn
            resp_id = event.get("llm_response_id", "")
            if resp_id in response_id_to_turn:
                turn_idx = response_id_to_turn[resp_id]
                if turn_idx < len(stats.turns):
                    stats.turns[turn_idx].tool_calls.append(tool_info)

    return stats


def _parse_openhands_jsonl(raw_output: str) -> CallStats:
    """Fallback: parse OpenHands --json JSONL output for basic stats."""
    stats = CallStats()

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = data.get("kind")
        if kind == "ActionEvent" and data.get("source") == "agent":
            tool_name = data.get("tool_name", "")
            if tool_name:
                stats.total_tool_calls += 1
                stats.tool_calls_by_name[tool_name] = stats.tool_calls_by_name.get(tool_name, 0) + 1

    return stats


def parse_claude_stream_stats(raw_output: str) -> CallStats:
    """Parse Claude Code stream-json (JSONL) output to extract detailed call statistics.

    Extracts:
    - Cumulative stats from the final 'result' line
    - Per-turn tool calls and token counts from 'assistant' messages

    Note: stream-json emits MULTIPLE lines per assistant message (same message.id),
    each containing different content blocks (text, tool_use). We must merge them
    to capture all tool calls for each turn.
    """
    stats = CallStats()

    # First pass: group assistant message lines by message id, preserving order
    turn_data: Dict[str, Dict] = {}  # msg_id -> merged turn data
    turn_order: List[str] = []  # ordered unique msg_ids
    result_data = None

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = data.get("type")

        if msg_type == "assistant":
            message = data.get("message", {})
            msg_id = message.get("id")
            if not msg_id:
                continue

            if msg_id not in turn_data:
                # First occurrence of this message id — capture usage from first line
                turn_order.append(msg_id)
                usage = message.get("usage", {})
                turn_data[msg_id] = {
                    "usage": usage,
                    "tool_calls": [],
                }

            # Collect tool_use blocks from ALL lines with this message id
            content = message.get("content", [])
            for block in content:
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_info = {"name": tool_name}
                    tool_input = block.get("input", {})
                    if tool_input:
                        tool_info["input"] = tool_input
                    turn_data[msg_id]["tool_calls"].append(tool_info)

        elif msg_type == "result":
            result_data = data

    # Build TurnStats from merged data
    for turn_number, msg_id in enumerate(turn_order, 1):
        td = turn_data[msg_id]
        usage = td["usage"]
        turn = TurnStats(
            turn_number=turn_number,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
            tool_calls=td["tool_calls"],
        )
        stats.turns.append(turn)

        # Aggregate tool calls
        for tool_info in td["tool_calls"]:
            tool_name = tool_info["name"]
            stats.total_tool_calls += 1
            stats.tool_calls_by_name[tool_name] = stats.tool_calls_by_name.get(tool_name, 0) + 1

    # Extract cumulative stats from the result line
    if result_data:
        raw_duration_ms = result_data.get("duration_ms")
        stats.duration_s = raw_duration_ms / 1000.0 if raw_duration_ms is not None else None
        raw_api_ms = result_data.get("duration_api_ms")
        stats.duration_api_s = raw_api_ms / 1000.0 if raw_api_ms is not None else None
        stats.num_turns = result_data.get("num_turns")
        stats.total_cost_usd = result_data.get("total_cost_usd")
        usage = result_data.get("usage", {})
        stats.input_tokens = usage.get("input_tokens")
        stats.output_tokens = usage.get("output_tokens")
        stats.cache_creation_input_tokens = usage.get("cache_creation_input_tokens")
        stats.cache_read_input_tokens = usage.get("cache_read_input_tokens")

    return stats


def invoke_candidate_selection(
    work_dir: Path,
    entry_id: str,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm",
    backend: Optional[str] = None
) -> Tuple[List[int], Optional[str], Optional[str], Optional[str], Optional[CallStats]]:
    """Invoke LLM agent for candidate selection.

    Returns: (selected_candidates, confidence, explanation, error, call_stats)
    """
    prompt = "Read INSTRUCTIONS.md and follow exactly. Write result to result.txt"

    cmd, env, timeout = build_agent_command(agent, model, prompt, base_url, api_key, backend=backend)
    if agent == "claude-code":
        # Insert --output-format stream-json --verbose before the prompt (last element)
        cmd = cmd[:-1] + ["--output-format", "stream-json", "--verbose"] + [cmd[-1]]

    for attempt in range(MAX_RETRIES):
        try:
            call_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            log_file = AGENT_LOGS_DIR / f"simple_{entry_id}_candsel_{call_ts}.log"
            AGENT_LOGS_DIR.mkdir(parents=True, exist_ok=True)

            result_file = work_dir / "result.txt"
            if result_file.exists():
                result_file.unlink()

            wall_start = time.monotonic()
            with open(log_file, 'w') as lf:
                result = subprocess.run(
                    cmd, stdout=lf, stderr=subprocess.STDOUT,
                    timeout=timeout, cwd=work_dir, env=env,
                )
            wall_duration_s = time.monotonic() - wall_start

            raw_output = log_file.read_text(errors='replace')
            logging.info(f"[{entry_id}] Agent log: {log_file}")

            # Extract stats from agent output
            call_stats = None
            if agent == "claude-code":
                call_stats = parse_claude_stream_stats(raw_output)
            elif agent == "openhands":
                call_stats = parse_openhands_stats(raw_output)

            # Set wall-clock duration for agents that don't report it natively
            if call_stats and call_stats.duration_s is None:
                call_stats.duration_s = wall_duration_s

            result_file = work_dir / "result.txt"
            if not result_file.exists():
                logging.error(f"No result.txt created. Raw output: {raw_output[:2000] if raw_output else 'None'}")
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"No result.txt created, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
                return [], None, None, "No result.txt created", call_stats

            result_content = result_file.read_text()
            selected, confidence, explanation = parse_candidate_selection_result(result_content)

            if not selected:
                return [], confidence, explanation, "Could not parse selection from result", call_stats

            time.sleep(2)
            return selected, confidence, explanation, None, call_stats

        except subprocess.TimeoutExpired:
            return [], None, None, f"Timeout after {timeout}s", None
        except FileNotFoundError:
            return [], None, None, f"{agent} CLI not found", None
        except Exception as e:
            if "overload" in str(e).lower():
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"API overloaded, waiting {RETRY_DELAY}s before retry {attempt + 2}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
            return [], None, None, str(e), None

    return [], None, None, "Max retries exceeded", None


# =============================================================================
# MAIN PROCESSING
# =============================================================================
def analyze_entry(
    repo_path: Path,
    entry: Dict,
    model: str = "claude-opus-4-5",
    agent: str = "claude-code",
    base_url: Optional[str] = None,
    api_key: str = "local-llm",
    without_fc_message: bool = False,
    without_fc_diff: bool = False,
    backend: Optional[str] = None
) -> EntryResult:
    """Analyze a single dataset entry: build candidates, invoke LLM for selection."""
    entry_id = entry["id"]
    fix_commit = entry["fix_commit_hash"]
    bic_commits = entry.get("bug_commit_hash", [])

    # Step 1: Build candidate set from file histories
    candidates, path_mapping = build_file_path_mapping(repo_path, fix_commit)
    all_files = get_files_from_commit(repo_path, fix_commit)

    # Count source vs all files
    binary_extensions = (
        '.o', '.ko', '.a', '.so', '.bin', '.elf', '.exe', '.dll',
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',
        '.pdf', '.doc', '.docx',
        '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z',
        '.pyc', '.pyo', '.class',
    )
    source_files = [f for f in all_files if not f.endswith(binary_extensions)]
    num_files = len(all_files)
    num_source_files = len(source_files)

    if not candidates:
        logging.error(f"[{entry_id}] No candidate commits found")
        return EntryResult(
            entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
            total_candidates=0, gt_in_candidates=False,
            selected_commit=None, selected_index=None,
            confidence=None, explanation=None, is_correct=None,
            num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
            files_analyzed=all_files, error="No candidate commits found"
        )

    # Check if ground truth is in candidates
    gt_indices = find_all_bic_positions(bic_commits, candidates)
    gt_in_candidates = len(gt_indices) > 0

    print(f"  {len(candidates)} candidates, GT in candidates: {'YES' if gt_in_candidates else 'NO'}")
    if gt_in_candidates:
        print(f"  GT BIC at indices: {gt_indices}")
    logging.info(f"[{entry_id}] {len(candidates)} candidates, GT in candidates: {gt_in_candidates}")

    # Early stop: if ground truth BIC is not in candidates, LLM cannot find it
    if not gt_in_candidates:
        print(f"  Early stop: ground truth BIC not in candidates")
        logging.info(f"[{entry_id}] Early stop: ground truth BIC not in candidates")
        return EntryResult(
            entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
            total_candidates=len(candidates), gt_in_candidates=False,
            selected_commit=None, selected_index=None,
            confidence=None, explanation=None, is_correct=False,
            num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
            files_analyzed=all_files, error="Early stop: ground truth BIC not in candidates"
        )

    # Step 2: Prepare candidate selection directory
    print(f"  Preparing candidate selection for {len(candidates)} candidates...")
    work_dir = prepare_candidate_selection_directory(
        repo_path, fix_commit, candidates, bic_commits, entry_id,
        without_fc_message=without_fc_message, without_fc_diff=without_fc_diff
    )

    if not work_dir:
        return EntryResult(
            entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
            total_candidates=len(candidates), gt_in_candidates=gt_in_candidates,
            selected_commit=None, selected_index=None,
            confidence=None, explanation=None, is_correct=None,
            num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
            files_analyzed=all_files, error="Failed to prepare candidate selection directory"
        )

    # Step 3: Invoke LLM for candidate selection
    agent_name = {"claude-code": "Claude", "openhands": "OpenHands"}[agent]
    print(f"  Invoking {agent_name} for candidate selection...")
    selected, confidence, explanation, error, call_stats = invoke_candidate_selection(
        work_dir, entry_id, model, agent=agent, base_url=base_url, api_key=api_key, backend=backend
    )

    # Log stats if available
    if call_stats and call_stats.duration_s is not None:
        tools_str = ", ".join(f"{name}:{count}" for name, count in sorted(call_stats.tool_calls_by_name.items()))
        cost_str = f"${call_stats.total_cost_usd:.4f}" if call_stats.total_cost_usd is not None else "N/A"
        output_tok_str = str(call_stats.output_tokens) if call_stats.output_tokens is not None else "N/A"
        print(f"  Stats: {call_stats.duration_s:.1f}s, {output_tok_str} output tokens, "
              f"{call_stats.num_turns} turns, {call_stats.total_tool_calls} tool calls, {cost_str}")
        if tools_str:
            print(f"  Tools: {tools_str}")

    if error:
        print(f"  LLM ERROR: {error}")
        logging.error(f"[{entry_id}] LLM error: {error}")
        return EntryResult(
            entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
            total_candidates=len(candidates), gt_in_candidates=gt_in_candidates,
            selected_commit=None, selected_index=None,
            confidence=confidence, explanation=explanation, is_correct=None,
            num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
            files_analyzed=all_files, call_stats=call_stats, error=error
        )

    if not selected:
        return EntryResult(
            entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
            total_candidates=len(candidates), gt_in_candidates=gt_in_candidates,
            selected_commit=None, selected_index=None,
            confidence=confidence, explanation=explanation, is_correct=None,
            num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
            files_analyzed=all_files, call_stats=call_stats, error="No candidate selected"
        )

    # Map selection to actual commit
    selected_window_idx = selected[0] - 1  # Convert 1-indexed to 0-indexed
    if selected_window_idx < 0 or selected_window_idx >= len(candidates):
        return EntryResult(
            entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
            total_candidates=len(candidates), gt_in_candidates=gt_in_candidates,
            selected_commit=None, selected_index=None,
            confidence=confidence, explanation=explanation, is_correct=None,
            num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
            files_analyzed=all_files, call_stats=call_stats, error=f"Invalid selection index: {selected[0]}"
        )

    found_bic = candidates[selected_window_idx]
    is_correct = is_commit_match(found_bic.hash, set(bic_commits))

    result_str = "CORRECT" if is_correct else "INCORRECT"
    print(f"  Selected candidate_{selected[0]:02d} (index {selected_window_idx}): {found_bic.hash[:8]}, confidence: {confidence}")
    print(f"  Result: {result_str}")
    logging.info(f"[{entry_id}] Selected {found_bic.hash[:8]}, correct: {is_correct}")

    return EntryResult(
        entry_id=entry_id, fix_commit=fix_commit, ground_truth_bics=bic_commits,
        total_candidates=len(candidates), gt_in_candidates=gt_in_candidates,
        selected_commit=found_bic.hash, selected_index=selected_window_idx,
        confidence=confidence, explanation=explanation, is_correct=is_correct,
        num_files_touched_by_fix=num_files, num_source_files_touched_by_fix=num_source_files,
        files_analyzed=all_files, call_stats=call_stats
    )


# =============================================================================
# RESULTS EXPORT
# =============================================================================
def build_combined_results(results: List[EntryResult]) -> List[Dict]:
    """Build results in baseline-compatible format for evaluation_utils."""
    combined = []
    for r in results:
        entry = {
            "id": r.entry_id,
            "fix_commit_hash": r.fix_commit,
            "ground_truth_bics": r.ground_truth_bics,
            "predicted_bics": [r.selected_commit] if r.selected_commit else [],
            "prediction_source": "simple_szz_agent",
        }
        combined.append(entry)
    return combined


def compute_aggregate_stats(results: List[EntryResult]) -> Dict:
    """Compute aggregated statistics across all entries."""
    import statistics

    def agg(values: List) -> Dict:
        """Compute min/max/mean/median for a list of numbers."""
        if not values:
            return {"min": None, "max": None, "mean": None, "median": None, "total": None, "count": 0}
        return {
            "min": min(values),
            "max": max(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "total": sum(values),
            "count": len(values),
        }

    # Collect per-entry stat values (only from entries that have call_stats)
    duration_s_vals = []
    duration_api_s_vals = []
    num_turns_vals = []
    input_tokens_vals = []
    output_tokens_vals = []
    cache_creation_vals = []
    cache_read_vals = []
    cost_vals = []
    total_tool_calls_vals = []

    # Aggregate tool calls by name across all entries
    all_tool_calls_by_name: Dict[str, int] = {}

    for r in results:
        if r.call_stats:
            s = r.call_stats
            if s.duration_s is not None:
                duration_s_vals.append(s.duration_s)
            if s.duration_api_s is not None:
                duration_api_s_vals.append(s.duration_api_s)
            if s.num_turns is not None:
                num_turns_vals.append(s.num_turns)
            if s.input_tokens is not None:
                input_tokens_vals.append(s.input_tokens)
            if s.output_tokens is not None:
                output_tokens_vals.append(s.output_tokens)
            if s.cache_creation_input_tokens is not None:
                cache_creation_vals.append(s.cache_creation_input_tokens)
            if s.cache_read_input_tokens is not None:
                cache_read_vals.append(s.cache_read_input_tokens)
            if s.total_cost_usd is not None:
                cost_vals.append(s.total_cost_usd)
            total_tool_calls_vals.append(s.total_tool_calls)
            for name, count in s.tool_calls_by_name.items():
                all_tool_calls_by_name[name] = all_tool_calls_by_name.get(name, 0) + count

    # Candidate and file counts (from all entries)
    candidate_counts = [r.total_candidates for r in results]
    file_counts = [r.num_files_touched_by_fix for r in results]
    source_file_counts = [r.num_source_files_touched_by_fix for r in results]

    return {
        "duration_s": agg(duration_s_vals),
        "duration_api_s": agg(duration_api_s_vals),
        "num_turns": agg(num_turns_vals),
        "input_tokens": agg(input_tokens_vals),
        "output_tokens": agg(output_tokens_vals),
        "cache_creation_input_tokens": agg(cache_creation_vals),
        "cache_read_input_tokens": agg(cache_read_vals),
        "total_cost_usd": agg(cost_vals),
        "total_tool_calls": agg(total_tool_calls_vals),
        "tool_calls_by_name": all_tool_calls_by_name,
        "num_candidates": agg(candidate_counts),
        "num_files_touched_by_fix": agg(file_counts),
        "num_source_files_touched_by_fix": agg(source_file_counts),
    }


def export_results(
    results: List[EntryResult],
    combined_results: List[Dict],
    dataset_path: str,
    model: str,
    agent: str,
    timestamp: str,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> Path:
    """Export results to JSON with per-entry and aggregated statistics."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = RESULTS_DIR / f"simple_szz_agent_{timestamp}.json"

    with_result = [r for r in results if r.is_correct is not None]
    correct = sum(1 for r in with_result if r.is_correct is True)
    incorrect = sum(1 for r in with_result if r.is_correct is False)

    # Per-entry detailed results with stats
    detail_entries = []
    for r in results:
        entry_data = {
            "entry_id": r.entry_id,
            "fix_commit": r.fix_commit,
            "ground_truth_bics": r.ground_truth_bics,
            "total_candidates": r.total_candidates,
            "gt_in_candidates": r.gt_in_candidates,
            "selected_commit": r.selected_commit,
            "selected_index": r.selected_index,
            "confidence": r.confidence,
            "explanation": r.explanation,
            "is_correct": r.is_correct,
            "num_files_touched_by_fix": r.num_files_touched_by_fix,
            "num_source_files_touched_by_fix": r.num_source_files_touched_by_fix,
            "files_analyzed": r.files_analyzed,
            "call_stats": r.call_stats.to_dict() if r.call_stats else None,
            "error": r.error,
        }
        detail_entries.append(entry_data)

    # Aggregated statistics
    agg_stats = compute_aggregate_stats(results)

    output_data = {
        "metadata": {
            "timestamp": timestamp,
            "dataset_file": dataset_path,
            "model": model,
            "agent": agent,
            "algorithm": "Simple SZZ Agent (direct candidate selection)",
            "without_fc_message": without_fc_message,
            "without_fc_diff": without_fc_diff,
        },
        "summary": {
            "total_entries": len(results),
            "entries_with_result": len(with_result),
            "correct": correct,
            "incorrect": incorrect,
            "accuracy": correct / (correct + incorrect) if (correct + incorrect) > 0 else None,
        },
        "aggregate_stats": agg_stats,
        "results": combined_results,
        "details": detail_entries,
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    return output_file


# =============================================================================
# MAIN
# =============================================================================
def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Simple SZZ Agent: Direct candidate selection from file history"
    )
    parser.add_argument(
        "--dataset", "-d",
        type=str,
        default=str(DEFAULT_DATASET),
        help=f"Path to the dataset JSON file (default: {DEFAULT_DATASET})"
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
        "--backend",
        type=str,
        choices=["default", "openrouter"],
        default="default",
        help="Backend to route LLM calls through (default: auto-detect; openrouter: force all models via OpenRouter)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Base URL for the LLM API (used with --agent=openhands)"
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
        help="Do not provide the fix commit message as context"
    )
    parser.add_argument(
        "--without-fc-diff",
        action="store_true",
        help="Do not provide the fix commit diff as context"
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    log_file = setup_logging()

    print("=" * 80)
    print("Simple SZZ Agent (Direct Candidate Selection)")
    print("=" * 80)

    print(f"Strategy: Direct candidate selection from file history")

    logging.info(f"Log file: {log_file}")
    logging.info(f"Using agent: {args.agent}")
    logging.info(f"Using model: {args.model}")
    # Check agent CLI exists
    if not verify_agent_cli(args.agent):
        return

    # Load dataset
    dataset_path = Path(args.dataset)
    logging.info(f"Loading data from {dataset_path}...")
    print(f"\nLoading data from {dataset_path}...")

    if not dataset_path.exists():
        print(f"\nERROR: Dataset file not found: {dataset_path}")
        logging.error(f"Dataset file not found: {dataset_path}")
        return

    with open(dataset_path, 'r') as f:
        dataset = json.load(f)

    # Handle both wrapped and flat format
    if isinstance(dataset, dict) and "results" in dataset:
        all_entries = dataset["results"]
        print(f"Loaded dataset with {len(all_entries)} entries (from results key)")
    else:
        all_entries = dataset
        print(f"Loaded dataset with {len(all_entries)} entries")

    # Create repos directory
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure all repositories exist (clone if needed)
    failed_repos = ensure_repos_exist(all_entries, REPOS_DIR)
    if failed_repos:
        original_count = len(all_entries)
        all_entries = [e for e in all_entries if e.get("repo_name", "linux-kernel") not in failed_repos]
        print(f"\nFiltered out {original_count - len(all_entries)} entries due to missing repos")
        print(f"Proceeding with {len(all_entries)} entries")

    # Shuffle for reproducibility
    random.seed(RANDOM_SEED)
    random.shuffle(all_entries)

    # Apply limit
    if args.limit is not None and args.limit > 0:
        all_entries = all_entries[:args.limit]
        print(f"Limited to {len(all_entries)} entries")
        logging.info(f"Limited to {len(all_entries)} entries")

    # Clear and create directories
    if not args.skip_cleanup and TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Process entries
    results: List[EntryResult] = []
    session_limit_hit = False

    for idx, entry in enumerate(all_entries, 1):
        if session_limit_hit:
            logging.warning("Session limit was hit, stopping processing")
            print("\n*** Session limit hit, stopping processing ***")
            break

        entry_id = entry["id"]
        fix_commit = entry["fix_commit_hash"]

        print(f"\n[{idx}/{len(all_entries)}] Processing entry {entry_id} (fix: {fix_commit[:8]})")
        logging.info(f"[{idx}/{len(all_entries)}] Processing entry {entry_id}")

        repo_path = get_repo_path(entry, REPOS_DIR)
        backend = args.backend if args.backend != "default" else None
        result = analyze_entry(
            repo_path, entry, args.model,
            agent=args.agent, base_url=args.base_url, api_key=args.api_key,
            without_fc_message=args.without_fc_message, without_fc_diff=args.without_fc_diff,
            backend=backend
        )
        results.append(result)

        if result.error and "SESSION_LIMIT" in (result.error or ""):
            session_limit_hit = True

    # Build combined results for evaluation
    combined_results = build_combined_results(results)

    # Evaluate and print results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\n" + "=" * 80)
    print("                    EVALUATION RESULTS")
    print("=" * 80)

    summary = evaluate_results(combined_results)
    print_eval_summary(summary, "Simple SZZ Agent")

    # Print additional statistics
    print("\n### DETAILED STATISTICS ###")
    print("-" * 50)

    with_result = [r for r in results if r.is_correct is not None]
    correct = sum(1 for r in with_result if r.is_correct is True)
    incorrect = sum(1 for r in with_result if r.is_correct is False)
    errors = sum(1 for r in results if r.error and "early stop" not in (r.error or "").lower())

    print(f"Total entries processed:         {len(results)}")
    print(f"Entries with result:             {len(with_result)}")
    if with_result:
        accuracy = correct / (correct + incorrect) if (correct + incorrect) > 0 else 0
        print(f"Correct:                         {correct} ({100*accuracy:.1f}%)")
        print(f"Incorrect:                       {incorrect}")
    print(f"Errors:                          {errors}")

    # Confidence distribution
    confidence_dist = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "Unknown": 0}
    for r in results:
        if r.confidence:
            confidence_dist[r.confidence] = confidence_dist.get(r.confidence, 0) + 1
        elif r.selected_commit:
            confidence_dist["Unknown"] += 1

    if any(v > 0 for v in confidence_dist.values()):
        print(f"\nConfidence distribution:")
        for level, count in confidence_dist.items():
            if count > 0:
                print(f"  {level}: {count}")

    # Aggregated stats
    agg_stats = compute_aggregate_stats(results)

    def print_agg(label: str, stats: Dict, unit: str = "", divisor: float = 1.0):
        if stats["count"] == 0:
            return
        fmt = lambda v: f"{v/divisor:.1f}{unit}" if v is not None else "N/A"
        print(f"  {label + ':':<35} min={fmt(stats['min'])}, max={fmt(stats['max'])}, mean={fmt(stats['mean'])}, median={fmt(stats['median'])}, total={fmt(stats['total'])}")

    print(f"\nCandidate & file statistics:")
    print_agg("Num candidates", agg_stats["num_candidates"])
    print_agg("Num files touched by fix", agg_stats["num_files_touched_by_fix"])
    print_agg("Num source files touched by fix", agg_stats["num_source_files_touched_by_fix"])

    if agg_stats["duration_s"]["count"] > 0:
        agent_label = {"claude-code": "Claude Code", "openhands": "OpenHands"}.get(args.agent, args.agent)
        print(f"\n{agent_label} call statistics:")
        print_agg("Duration", agg_stats["duration_s"], unit="s")
        print_agg("API duration", agg_stats["duration_api_s"], unit="s")
        print_agg("Num turns", agg_stats["num_turns"])
        print_agg("Total tool calls", agg_stats["total_tool_calls"])
        print_agg("Input tokens", agg_stats["input_tokens"])
        print_agg("Output tokens", agg_stats["output_tokens"])
        print_agg("Cache creation tokens", agg_stats["cache_creation_input_tokens"])
        print_agg("Cache read tokens", agg_stats["cache_read_input_tokens"])
        print_agg("Cost", agg_stats["total_cost_usd"], unit="$")

        if agg_stats["tool_calls_by_name"]:
            print(f"\n  Tool call totals across all entries:")
            for name, count in sorted(agg_stats["tool_calls_by_name"].items(), key=lambda x: -x[1]):
                print(f"    {name}: {count}")

    # Export results
    output_file = export_results(
        results, combined_results, str(dataset_path), args.model, args.agent, timestamp,
        without_fc_message=args.without_fc_message, without_fc_diff=args.without_fc_diff
    )
    print(f"\nResults saved to: {output_file}")

    # Cleanup
    if not args.skip_cleanup and TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
        print("Cleaned up temp directory")
        logging.info("Cleaned up temp directory")

    print("\nDone!")


if __name__ == "__main__":
    main()
