#!/usr/bin/env python3
"""
Analyze grep tool calls from the simple SZZ agent results.

For each grep call, determines the likely source of the search pattern by checking
whether it appears in the fix commit message, fix commit diff (and which part),
file paths, etc.
"""

import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# =============================================================================
# CONFIG
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_FILE = str(SCRIPT_DIR / "results" / "simple_szz_agent_20260211_212858.json")
REPO_PATH = SCRIPT_DIR.parent.parent / "repos" / "linux-kernel"


# =============================================================================
# GIT HELPERS
# =============================================================================

def git_cmd(repo_path: Path, *args) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args),
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def get_commit_message(repo_path: Path, commit_hash: str) -> str:
    return git_cmd(repo_path, "log", "-1", "--format=%B", commit_hash).strip()


def get_commit_diff(repo_path: Path, commit_hash: str) -> str:
    return git_cmd(repo_path, "show", "--format=", "--patch", commit_hash)


# =============================================================================
# DIFF PARSING
# =============================================================================

def parse_diff(diff_text: str):
    """Parse a unified diff into categorized lines.

    Returns a dict with:
        - 'added': lines starting with '+'  (not +++)
        - 'removed': lines starting with '-' (not ---)
        - 'context': context lines (starting with ' ')
        - 'hunk_headers': @@ lines (contain function names)
        - 'file_paths': file paths from diff headers (diff --git, ---, +++)
        - 'full_text': the entire diff as-is
    """
    result = {
        'added': [],
        'removed': [],
        'context': [],
        'hunk_headers': [],
        'file_paths': [],
        'full_text': diff_text,
    }

    for line in diff_text.splitlines():
        if line.startswith('diff --git'):
            # Extract file paths: diff --git a/path b/path
            parts = line.split()
            for p in parts[2:]:
                # Strip a/ or b/ prefix
                cleaned = re.sub(r'^[ab]/', '', p)
                result['file_paths'].append(cleaned)
        elif line.startswith('--- ') or line.startswith('+++ '):
            path = line[4:]
            cleaned = re.sub(r'^[ab]/', '', path)
            if cleaned != '/dev/null':
                result['file_paths'].append(cleaned)
        elif line.startswith('@@'):
            result['hunk_headers'].append(line)
        elif line.startswith('+'):
            result['added'].append(line[1:])  # strip the '+' prefix
        elif line.startswith('-'):
            result['removed'].append(line[1:])  # strip the '-' prefix
        elif line.startswith(' '):
            result['context'].append(line[1:])  # strip the space prefix

    return result


# =============================================================================
# GREP PATTERN EXTRACTION
# =============================================================================

def extract_grep_patterns_from_tool(tool_call):
    """Extract search patterns from a Grep tool call."""
    pattern = tool_call['input'].get('pattern', '')
    return pattern


def extract_grep_patterns_from_bash(command: str):
    """Extract search patterns from a bash grep command.

    Returns the pattern string or None if not a grep command.
    """
    if 'grep' not in command.lower():
        return None

    # Try to extract pattern from grep command
    # Handle patterns like: grep "pattern" file, grep -l "pattern" file,
    # grep -A 50 -B 5 "pattern" file, etc.

    # Strategy: find the first quoted string after 'grep' and its flags
    # or the first non-flag argument

    # Split by pipes and process each grep command separately
    patterns = []

    # Find all grep invocations in the command
    # Split on pipes and semicolons
    parts = re.split(r'\|', command)

    for part in parts:
        part = part.strip()
        if not re.search(r'\bgrep\b', part):
            continue

        # Try to extract quoted pattern
        # Match double-quoted or single-quoted strings
        # First, skip past 'grep' and its flags
        grep_match = re.search(r'\bgrep\s+(.*)', part)
        if not grep_match:
            continue

        after_grep = grep_match.group(1)

        # Remove flag arguments: -l, -i, -n, -c, -r, -A N, -B N, -C N, etc.
        # Iteratively strip flags
        remaining = after_grep
        while remaining:
            remaining = remaining.strip()
            # Match flags like -l, -i, -n, -c, -r, -h, -w, -E, -P
            flag_match = re.match(r'^-[a-zA-Z]+\s+', remaining)
            if flag_match:
                flag = flag_match.group(0).strip()
                remaining = remaining[flag_match.end():]
                # If the flag takes an argument (-A, -B, -C, -m, -e)
                if flag in ('-A', '-B', '-C', '-m'):
                    # Skip the numeric argument
                    num_match = re.match(r'^\d+\s+', remaining)
                    if num_match:
                        remaining = remaining[num_match.end():]
                continue
            # Check combined flags with numeric arg like -A50, -B5
            combined_match = re.match(r'^-[ABC]\d+\s+', remaining)
            if combined_match:
                remaining = remaining[combined_match.end():]
                continue
            break

        # Now remaining should start with the pattern
        # Extract quoted or unquoted pattern
        if remaining.startswith('"'):
            quote_match = re.match(r'"([^"]*)"', remaining)
            if quote_match:
                patterns.append(quote_match.group(1))
        elif remaining.startswith("'"):
            quote_match = re.match(r"'([^']*)'", remaining)
            if quote_match:
                patterns.append(quote_match.group(1))
        else:
            # Unquoted - take until whitespace
            word_match = re.match(r'(\S+)', remaining)
            if word_match:
                patterns.append(word_match.group(1))

    if patterns:
        return '|'.join(patterns) if len(patterns) > 1 else patterns[0]
    return None


def split_pattern_alternatives(pattern: str) -> list:
    """Split a grep pattern into individual search terms.

    Handles both '|' (ERE/ripgrep) and '\\|' (BRE) alternation.
    """
    # First replace \| with | then split on |
    normalized = pattern.replace('\\|', '|')
    terms = [t.strip() for t in normalized.split('|') if t.strip()]
    return terms


# =============================================================================
# PATTERN MATCHING
# =============================================================================

def strip_diff_line_prefix_regex(term: str):
    """Strip grep regex prefixes that target diff line markers (+, -, @@).

    Returns (cleaned_term, targeted_line_type) where targeted_line_type is
    'added', 'removed', 'hunk_header', or None.
    """
    # Patterns like ^\+, ^+, ^\-  that target diff added/removed lines
    # Also handle ^--- /dev/null, ^\+\+\+ b/ for file path lines
    targeted = None

    # Check for +++ or --- targeting (file path lines in diff)
    m = re.match(r'^\^?\\?\+\\?\+\\?\+\s*', term)
    if m:
        return term[m.end():], 'file_path_header'

    m = re.match(r'^\^?---\s*', term)
    if m:
        return term[m.end():], 'file_path_header'

    # Check for ^+ or ^\+ prefix (targeting added lines)
    m = re.match(r'^\^?\\?\+\.?\*?', term)
    if m and m.end() > 0 and m.group(0) not in ('', term):
        cleaned = term[m.end():]
        if cleaned:  # Only if there's something left
            return cleaned, 'added'

    # Check for ^- or ^\- prefix (targeting removed lines)
    m = re.match(r'^\^?\\?-\.?\*?', term)
    if m and m.end() > 0 and m.group(0) not in ('', term):
        cleaned = term[m.end():]
        if cleaned:
            return cleaned, 'removed'

    return term, None


def clean_regex_to_literal(term: str) -> str:
    """Convert a regex pattern to a more literal form for substring matching.

    Strips common regex syntax to extract the core identifiers being searched.
    """
    # Remove common regex constructs
    cleaned = term
    cleaned = cleaned.replace('\\(', '(').replace('\\)', ')')
    cleaned = cleaned.replace('\\{', '{').replace('\\}', '}')
    cleaned = cleaned.replace('\\.', '.')
    cleaned = cleaned.replace('\\n', '\n')
    cleaned = cleaned.replace('\\s+', ' ').replace('\\s*', '')
    cleaned = cleaned.replace('\\s', ' ')
    cleaned = cleaned.replace('\\b', '')
    cleaned = re.sub(r'\.\*\??', ' ', cleaned)  # .* or .*? -> space
    cleaned = re.sub(r'\.\+\??', ' ', cleaned)  # .+ or .+? -> space
    cleaned = cleaned.replace('^', '').replace('$', '')
    cleaned = cleaned.replace('\\', '')

    return cleaned


def pattern_appears_in_text(pattern_term: str, text: str) -> bool:
    """Check if a single pattern term appears in text.

    Handles simple regex patterns by trying literal match first,
    then regex match, then cleaned-up literal matching.
    """
    if not text:
        return False

    # Strip regex anchors for literal matching
    literal = pattern_term.replace('^', '').replace('$', '')

    # Try literal (case-insensitive)
    if literal.lower() in text.lower():
        return True

    # Try as regex
    try:
        if re.search(pattern_term, text, re.IGNORECASE):
            return True
    except re.error:
        pass

    # Try with cleaned-up regex -> literal conversion
    cleaned = clean_regex_to_literal(pattern_term)
    # Split the cleaned pattern into significant tokens (at least 4 chars)
    tokens = [t for t in cleaned.split() if len(t) >= 4]
    if tokens:
        # If all significant tokens appear in text, count it as a match
        text_lower = text.lower()
        if all(t.lower() in text_lower for t in tokens):
            return True

    return False


def classify_pattern_source(pattern: str, commit_message: str, parsed_diff: dict):
    """Classify where a grep pattern likely came from.

    Returns a set of source labels.
    """
    terms = split_pattern_alternatives(pattern)
    sources = set()

    # Combine all diff line categories into searchable text blocks
    added_text = '\n'.join(parsed_diff['added'])
    removed_text = '\n'.join(parsed_diff['removed'])
    context_text = '\n'.join(parsed_diff['context'])
    hunk_header_text = '\n'.join(parsed_diff['hunk_headers'])
    file_paths_text = '\n'.join(parsed_diff['file_paths'])
    full_diff_text = parsed_diff['full_text']

    # Also extract just the file names (basenames) and directory/subsystem names
    path_components = set()
    for fp in parsed_diff['file_paths']:
        parts = fp.split('/')
        for part in parts:
            path_components.add(part)
            # Also add the filename without extension
            if '.' in part:
                path_components.add(part.rsplit('.', 1)[0])

    path_components_text = '\n'.join(path_components)

    # Extract function names from hunk headers: @@ ... @@ function_name
    function_names = []
    for hh in parsed_diff['hunk_headers']:
        # Hunk headers look like: @@ -578,9 +578,6 @@ static void dpp3_power_on_blnd_lut(
        fn_match = re.search(r'@@.*@@\s*(.*)', hh)
        if fn_match:
            function_names.append(fn_match.group(1).strip())
    function_names_text = '\n'.join(function_names)

    for term in terms:
        term_matched = False

        # First, check if the pattern has a diff line-type prefix (^\+, ^\-, etc.)
        stripped_term, targeted_type = strip_diff_line_prefix_regex(term)

        # Use the stripped term for content matching if a prefix was found
        match_term = stripped_term if targeted_type else term

        # 1. Fix commit message
        if pattern_appears_in_text(match_term, commit_message):
            sources.add('fix_commit_message')
            term_matched = True

        # 2. Diff - removed lines
        if pattern_appears_in_text(match_term, removed_text):
            sources.add('diff_removed_lines')
            term_matched = True

        # 3. Diff - added lines
        if pattern_appears_in_text(match_term, added_text):
            sources.add('diff_added_lines')
            term_matched = True

        # 4. Diff - context lines
        if pattern_appears_in_text(match_term, context_text):
            sources.add('diff_context_lines')
            term_matched = True

        # 5. Diff - hunk headers (function names in @@ lines)
        if pattern_appears_in_text(match_term, hunk_header_text):
            sources.add('diff_hunk_headers')
            term_matched = True

        # 6. Diff - function names extracted from hunk headers
        if pattern_appears_in_text(match_term, function_names_text):
            sources.add('diff_function_names')
            term_matched = True

        # 7. File paths in diff
        if pattern_appears_in_text(match_term, file_paths_text):
            sources.add('diff_file_paths')
            term_matched = True

        # 8. File path components (directory names, subsystems, basenames)
        if pattern_appears_in_text(match_term, path_components_text):
            sources.add('diff_path_components')
            term_matched = True

        # 9. Looks like a commit hash pattern (hex string >= 7 chars)
        clean_term = match_term.replace('^', '').replace('$', '').replace('\\b', '')
        if re.match(r'^[a-fA-F0-9]{7,40}$', clean_term):
            sources.add('commit_hash_pattern')
            term_matched = True

        # 10. Check against the full raw diff text as a fallback
        #     (catches patterns targeting raw diff syntax like "diff.*path",
        #      "^--- /dev/null", etc.)
        if not term_matched and pattern_appears_in_text(term, full_diff_text):
            sources.add('diff_raw_match')
            term_matched = True

        if not term_matched:
            sources.add('could_not_be_determined')

    return sources


# =============================================================================
# MAIN ANALYSIS
# =============================================================================

def main():
    results_path = Path(RESULTS_FILE)

    with open(results_path) as f:
        data = json.load(f)

    print(f"Loaded results: {results_path.name}")
    print(f"Total entries: {len(data['details'])}")
    print()

    # Collect all grep calls with their context
    all_grep_calls = []
    entries_with_greps = 0
    entries_skipped = 0

    for detail in data['details']:
        call_stats = detail.get('call_stats')
        if call_stats is None:
            entries_skipped += 1
            continue

        turns = call_stats.get('turns')
        if turns is None:
            entries_skipped += 1
            continue

        entry_greps = []

        for turn in turns:
            for tc in turn['tool_calls']:
                if tc['name'] == 'Grep':
                    pattern = extract_grep_patterns_from_tool(tc)
                    if pattern:
                        entry_greps.append({
                            'type': 'Grep_tool',
                            'pattern': pattern,
                            'entry_id': detail['entry_id'],
                            'fix_commit': detail['fix_commit'],
                            'is_correct': detail['is_correct'],
                        })
                elif tc['name'] == 'Bash':
                    command = tc['input'].get('command', '')
                    pattern = extract_grep_patterns_from_bash(command)
                    if pattern:
                        entry_greps.append({
                            'type': 'Bash_grep',
                            'pattern': pattern,
                            'entry_id': detail['entry_id'],
                            'fix_commit': detail['fix_commit'],
                            'is_correct': detail['is_correct'],
                        })

        if entry_greps:
            entries_with_greps += 1
            all_grep_calls.extend(entry_greps)

    print(f"Entries with grep calls: {entries_with_greps}")
    print(f"Entries skipped (no call_stats): {entries_skipped}")
    print(f"Total grep calls found: {len(all_grep_calls)}")
    print(f"  - Grep tool calls: {sum(1 for g in all_grep_calls if g['type'] == 'Grep_tool')}")
    print(f"  - Bash grep calls: {sum(1 for g in all_grep_calls if g['type'] == 'Bash_grep')}")
    print()

    # Get unique fix commits to fetch messages and diffs
    fix_commits = set(g['fix_commit'] for g in all_grep_calls)
    print(f"Unique fix commits to analyze: {len(fix_commits)}")

    # Cache commit messages and diffs
    commit_cache = {}
    repo_path = REPO_PATH

    for i, commit_hash in enumerate(fix_commits):
        msg = get_commit_message(repo_path, commit_hash)
        diff = get_commit_diff(repo_path, commit_hash)
        parsed = parse_diff(diff)
        commit_cache[commit_hash] = {
            'message': msg,
            'diff_raw': diff,
            'diff_parsed': parsed,
        }
        if (i + 1) % 20 == 0:
            print(f"  Fetched {i + 1}/{len(fix_commits)} commits...")

    print(f"  Fetched all {len(fix_commits)} commits.")
    print()

    # ==========================================================================
    # Classify each grep call
    # ==========================================================================

    # Per-grep-call classification
    source_counts = defaultdict(int)
    # Track which sources co-occur
    source_combo_counts = defaultdict(int)
    # Track by correct/incorrect
    source_counts_correct = defaultdict(int)
    source_counts_incorrect = defaultdict(int)
    # Track grep calls that couldn't be determined at all
    undetermined_greps = []
    # Count unique patterns per entry
    entry_pattern_counts = defaultdict(set)

    for grep_call in all_grep_calls:
        commit_hash = grep_call['fix_commit']
        cache = commit_cache[commit_hash]
        pattern = grep_call['pattern']

        sources = classify_pattern_source(
            pattern,
            cache['message'],
            cache['diff_parsed']
        )

        grep_call['sources'] = sources
        entry_pattern_counts[grep_call['entry_id']].add(pattern)

        # Count individual sources
        for src in sources:
            source_counts[src] += 1
            if grep_call['is_correct']:
                source_counts_correct[src] += 1
            else:
                source_counts_incorrect[src] += 1

        # Count source combination
        combo = tuple(sorted(sources))
        source_combo_counts[combo] += 1

        # Track undetermined
        if sources == {'could_not_be_determined'}:
            undetermined_greps.append(grep_call)

    # ==========================================================================
    # Print results
    # ==========================================================================

    total = len(all_grep_calls)

    print("=" * 80)
    print("AGGREGATE RESULTS: Source classification of grep patterns")
    print("=" * 80)
    print()
    print(f"Total grep calls analyzed: {total}")
    print()

    # Sort sources by count
    print("--- Source categories (a single grep can match multiple categories) ---")
    print()
    sorted_sources = sorted(source_counts.items(), key=lambda x: -x[1])
    for src, count in sorted_sources:
        pct = count / total * 100
        print(f"  {src:40s}  {count:4d}  ({pct:5.1f}%)")

    print()

    # Higher-level groupings
    diff_sources_set = {'diff_removed_lines', 'diff_added_lines',
                        'diff_context_lines', 'diff_hunk_headers',
                        'diff_function_names', 'diff_file_paths',
                        'diff_path_components', 'diff_raw_match'}
    in_message = source_counts.get('fix_commit_message', 0)
    in_diff_any = sum(1 for g in all_grep_calls
                      if g['sources'] & diff_sources_set)
    in_either = sum(1 for g in all_grep_calls
                    if g['sources'] & (diff_sources_set | {'fix_commit_message'}))
    undetermined_only = sum(1 for g in all_grep_calls
                           if g['sources'] == {'could_not_be_determined'})

    print("--- High-level summary ---")
    print()
    print(f"  {'Pattern found in fix commit message:':50s}  {in_message:4d}  ({in_message/total*100:5.1f}%)")
    print(f"  {'Pattern found in fix commit diff (any part):':50s}  {in_diff_any:4d}  ({in_diff_any/total*100:5.1f}%)")
    print(f"  {'Pattern found in message OR diff:':50s}  {in_either:4d}  ({in_either/total*100:5.1f}%)")
    print(f"  {'Could not be determined (no match anywhere):':50s}  {undetermined_only:4d}  ({undetermined_only/total*100:5.1f}%)")
    print()

    # Breakdown for diff matches
    print("--- Diff line type breakdown (for greps matching the diff) ---")
    print()
    diff_sources = [
        ('diff_removed_lines', 'Removed lines (-)'),
        ('diff_added_lines', 'Added lines (+)'),
        ('diff_context_lines', 'Context lines (unchanged)'),
        ('diff_hunk_headers', 'Hunk headers (@@ ... @@)'),
        ('diff_function_names', 'Function names (from hunk headers)'),
        ('diff_file_paths', 'File paths (full)'),
        ('diff_path_components', 'Path components (dirs, basenames)'),
        ('diff_raw_match', 'Raw diff text (fallback match)'),
    ]
    for key, label in diff_sources:
        count = source_counts.get(key, 0)
        pct = count / total * 100
        print(f"  {label:50s}  {count:4d}  ({pct:5.1f}%)")

    print()

    # Correct vs incorrect breakdown
    print("--- Correct vs incorrect predictions ---")
    print()
    correct_greps = sum(1 for g in all_grep_calls if g['is_correct'])
    incorrect_greps = total - correct_greps
    print(f"  Grep calls from correct predictions: {correct_greps}")
    print(f"  Grep calls from incorrect predictions: {incorrect_greps}")
    print()

    print(f"  {'Source':40s}  {'Correct':>8s}  {'Incorrect':>10s}")
    print(f"  {'-'*40}  {'-'*8}  {'-'*10}")
    all_sources = sorted(set(list(source_counts_correct.keys()) + list(source_counts_incorrect.keys())))
    for src in all_sources:
        c = source_counts_correct.get(src, 0)
        ic = source_counts_incorrect.get(src, 0)
        print(f"  {src:40s}  {c:8d}  {ic:10d}")

    print()

    # Most common source combinations
    print("--- Most common source combinations (top 15) ---")
    print()
    sorted_combos = sorted(source_combo_counts.items(), key=lambda x: -x[1])
    for combo, count in sorted_combos[:15]:
        pct = count / total * 100
        combo_str = ' + '.join(combo)
        print(f"  {count:4d}  ({pct:5.1f}%)  {combo_str}")

    print()

    # Show undetermined examples
    if undetermined_greps:
        print(f"--- Examples of undetermined grep patterns (showing up to 20) ---")
        print()
        shown = set()
        count = 0
        for g in undetermined_greps:
            key = (g['entry_id'], g['pattern'])
            if key not in shown:
                shown.add(key)
                print(f"  Entry {g['entry_id']} (fix={g['fix_commit'][:12]}): {g['pattern'][:100]}")
                count += 1
                if count >= 20:
                    break
        if len(undetermined_greps) > 20:
            print(f"  ... and {len(undetermined_greps) - 20} more")

    print()

    # Per-entry stats
    print("--- Per-entry statistics ---")
    print()
    entries_with_only_determined = 0
    entries_with_some_undetermined = 0
    entries_with_all_undetermined = 0

    for detail in data['details']:
        entry_id = detail['entry_id']
        entry_calls = [g for g in all_grep_calls if g['entry_id'] == entry_id]
        if not entry_calls:
            continue
        all_undet = all(g['sources'] == {'could_not_be_determined'} for g in entry_calls)
        any_undet = any(g['sources'] == {'could_not_be_determined'} for g in entry_calls)
        if all_undet:
            entries_with_all_undetermined += 1
        elif any_undet:
            entries_with_some_undetermined += 1
        else:
            entries_with_only_determined += 1

    print(f"  Entries where ALL greps were classifiable:        {entries_with_only_determined}")
    print(f"  Entries where SOME greps were unclassifiable:     {entries_with_some_undetermined}")
    print(f"  Entries where ALL greps were unclassifiable:      {entries_with_all_undetermined}")
    print()

    # Average greps per entry
    grep_counts = defaultdict(int)
    for g in all_grep_calls:
        grep_counts[g['entry_id']] += 1
    counts_list = list(grep_counts.values())
    avg_greps = sum(counts_list) / len(counts_list) if counts_list else 0
    print(f"  Avg grep calls per entry (entries with greps): {avg_greps:.1f}")
    print(f"  Min: {min(counts_list)}, Max: {max(counts_list)}, Median: {sorted(counts_list)[len(counts_list)//2]}")

    print()

    # Pattern length statistics
    print("--- Grep pattern length statistics (in characters) ---")
    print()
    pattern_lengths = [len(g['pattern']) for g in all_grep_calls]
    pattern_lengths_sorted = sorted(pattern_lengths)
    n = len(pattern_lengths)
    mean_len = sum(pattern_lengths) / n
    median_len = (pattern_lengths_sorted[n // 2] if n % 2 == 1
                  else (pattern_lengths_sorted[n // 2 - 1] + pattern_lengths_sorted[n // 2]) / 2)
    variance = sum((l - mean_len) ** 2 for l in pattern_lengths) / n
    std_len = math.sqrt(variance)
    min_len = pattern_lengths_sorted[0]
    max_len = pattern_lengths_sorted[-1]
    p25 = pattern_lengths_sorted[n // 4]
    p75 = pattern_lengths_sorted[3 * n // 4]

    print(f"  Count:               {n}")
    print(f"  Mean:                {mean_len:.1f}")
    print(f"  Median:              {median_len:.1f}")
    print(f"  Std deviation:       {std_len:.1f}")
    print(f"  Min:                 {min_len}")
    print(f"  Max:                 {max_len}")
    print(f"  25th percentile:     {p25}")
    print(f"  75th percentile:     {p75}")

    print()

    # Plain string vs regex classification
    print("--- Plain string vs regular expression ---")
    print()
    # Regex metacharacters (unescaped): . * + ? ^ $ | [ ] ( ) { }
    # We check whether the pattern contains any unescaped regex metachar.
    regex_meta = re.compile(r'(?<!\\)[.*+?^$|()\[\]{}]')
    n_regex = sum(1 for g in all_grep_calls if regex_meta.search(g['pattern']))
    n_plain = n - n_regex
    print(f"  Plain strings:       {n_plain:4d}  ({n_plain/n*100:5.1f}%)")
    print(f"  Regular expressions: {n_regex:4d}  ({n_regex/n*100:5.1f}%)")


if __name__ == '__main__':
    main()
