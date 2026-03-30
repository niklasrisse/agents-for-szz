#!/usr/bin/env python3
"""
Generate three illustrative examples of how the agent uses grep patterns to
identify bug-introducing commits.

Example A -- pattern derived from the fix commit **message**:
  Entry 4838: fix replaces fsleep with udelay; agent greps for "fsleep".

Example B -- pattern derived from the fix commit **removed lines**:
  Entry 3437: fix removes "blk->size >> 2"; agent greps for that expression.

Example C -- pure-addition fix where **standard SZZ would fail**:
  Entry 5208: fix adds a NULL check in smc_ib_is_sg_need_sync(); no lines
  removed, so git blame has nothing to operate on. The agent greps for the
  function name and finds the BIC that introduced it.

For each example the script prints:
  - Fix commit hash & message (subject + body, trimmed)
  - The relevant removed/added line from the fix diff
  - The grep pattern the agent used
  - The bug-introducing commit hash & message (subject + body, trimmed)
  - The matching added line from the BIC diff

All data is extracted from the results JSON and the linux-kernel repo so
the output is fully reproducible.
"""

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

# =============================================================================
# CONFIG
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_FILE = str(SCRIPT_DIR / "results" / "simple_szz_agent_20260211_212858.json")
REPO_PATH = SCRIPT_DIR.parent.parent / "repos" / "linux-kernel"

EXAMPLES = [
    {
        "label": "Example A: Grep pattern derived from fix commit message",
        "entry_id": "4838",
        "grep_pattern": "fsleep",
        "source": "fix_commit_message",
    },
    {
        "label": "Example B: Grep pattern derived from fix commit removed lines",
        "entry_id": "3437",
        "grep_pattern": "blk->size >> 2",
        "source": "diff_removed_lines",
    },
    {
        "label": "Example C: Pure-addition fix (standard SZZ would fail)",
        "entry_id": "5208",
        "grep_pattern": "smc_ib_is_sg_need_sync",
        "source": "fix_commit_message (function name)",
        "note": "Fix is a pure addition (3 insertions, 0 deletions). "
                "git blame has no removed lines to operate on, so standard "
                "SZZ cannot identify the bug-introducing commit.",
    },
]

# =============================================================================
# GIT HELPERS
# =============================================================================

def git_cmd(repo_path: Path, *args) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args),
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def get_commit_message(repo_path: Path, commit_hash: str) -> str:
    return git_cmd(repo_path, "log", "-1", "--format=%B", commit_hash).strip()


def get_commit_subject(repo_path: Path, commit_hash: str) -> str:
    return git_cmd(repo_path, "log", "-1", "--format=%s", commit_hash).strip()


def get_commit_diff(repo_path: Path, commit_hash: str) -> str:
    return git_cmd(repo_path, "show", "--format=", "--patch", commit_hash)


def get_commit_diffstat(repo_path: Path, commit_hash: str) -> str:
    return git_cmd(repo_path, "show", "--format=", "--stat", commit_hash).strip()


def trim_message(msg: str, max_lines: int = 8) -> str:
    """Return the first max_lines non-tag lines of a commit message."""
    lines = []
    for line in msg.splitlines():
        # Stop at sign-off / metadata tags
        if re.match(r'^(Signed-off-by|Reviewed-by|Acked-by|Cc|Closes|Patchwork|Message-ID):', line):
            break
        lines.append(line)
    # Trim trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["[...]"]
    return "\n".join(lines)


# =============================================================================
# DIFF HELPERS
# =============================================================================

def find_diff_lines(diff_text: str, pattern: str, line_type: str):
    """Find lines in a diff that contain `pattern`.

    line_type: '+' for added lines, '-' for removed lines, or '*' for both.
    Returns a list of (raw_line, file_path) tuples.
    """
    results = []
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 4:
                current_file = re.sub(r'^[ab]/', '', parts[3])
        if line_type == '+' and line.startswith('+') and not line.startswith('+++'):
            if pattern in line:
                results.append((line, current_file))
        elif line_type == '-' and line.startswith('-') and not line.startswith('---'):
            if pattern in line:
                results.append((line, current_file))
        elif line_type == '*':
            if pattern in line:
                results.append((line, current_file))
    return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    with open(RESULTS_FILE) as f:
        data = json.load(f)

    # Build lookup by entry_id
    detail_by_id = {}
    result_by_id = {}
    for d, r in zip(data["details"], data["results"]):
        detail_by_id[d["entry_id"]] = d
        result_by_id[d["entry_id"]] = r

    for ex in EXAMPLES:
        entry_id = ex["entry_id"]
        detail = detail_by_id[entry_id]
        result = result_by_id[entry_id]

        fix_hash = detail["fix_commit"]
        bic_hashes = result["ground_truth_bics"]
        predicted_bics = result["predicted_bics"]
        grep_pattern = ex["grep_pattern"]

        # Verify correct prediction
        assert result["correct"], f"Entry {entry_id} is not a correct prediction"
        assert result["precision"] == 1.0 and result["recall"] == 1.0

        # Verify the grep pattern exists in the agent's tool calls
        found_pattern = False
        for turn in detail["call_stats"]["turns"]:
            for tc in turn["tool_calls"]:
                if tc["name"] == "Grep":
                    if tc["input"].get("pattern") == grep_pattern:
                        found_pattern = True
        assert found_pattern, f"Pattern '{grep_pattern}' not found in tool calls for entry {entry_id}"

        # Fetch git data
        fix_msg = get_commit_message(REPO_PATH, fix_hash)
        fix_diff = get_commit_diff(REPO_PATH, fix_hash)
        bic_hash = bic_hashes[0]
        bic_msg = get_commit_message(REPO_PATH, bic_hash)
        bic_diff = get_commit_diff(REPO_PATH, bic_hash)

        # Find matching lines
        fix_removed = find_diff_lines(fix_diff, grep_pattern, '-')
        fix_added = find_diff_lines(fix_diff, grep_pattern, '+')
        bic_added = find_diff_lines(bic_diff, grep_pattern, '+')

        # Fetch diffstat for the fix
        fix_diffstat = get_commit_diffstat(REPO_PATH, fix_hash)

        # Print
        print("=" * 80)
        print(ex["label"])
        print("=" * 80)
        print()
        print(f"Entry ID:           {entry_id}")
        print(f"Pattern source:     {ex['source']}")
        print(f"Grep pattern:       \"{grep_pattern}\"")
        if "note" in ex:
            print()
            print(f"  NOTE: {ex['note']}")
        print()
        print(f"--- Fix commit: {fix_hash[:12]} ---")
        print()
        print(textwrap.indent(trim_message(fix_msg), "  "))
        print()
        print(f"  Diffstat: {fix_diffstat}")
        print()

        if fix_removed:
            print(f"  Removed line(s) containing pattern (in {fix_removed[0][1]}):")
            for line, _ in fix_removed:
                print(f"    {line}")
        if fix_added:
            print(f"  Added line(s) containing pattern (in {fix_added[0][1]}):")
            for line, _ in fix_added:
                print(f"    {line}")
        if not fix_removed and not fix_added:
            print("  (Pattern not in diff lines -- found in commit message only)")
        print()

        print(f"--- Bug-introducing commit: {bic_hash[:12]} ---")
        print()
        print(textwrap.indent(trim_message(bic_msg), "  "))
        print()

        if bic_added:
            print(f"  Matching added line(s) (in {bic_added[0][1]}):")
            for line, _ in bic_added:
                print(f"    {line}")
        print()
        print(f"  Prediction correct: {result['correct']}  "
              f"(precision={result['precision']:.1f}, recall={result['recall']:.1f})")
        print()
        print()


if __name__ == "__main__":
    main()
