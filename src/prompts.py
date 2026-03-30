"""
Prompt-building functions for all SZZ agent scripts.

This module centralizes the LLM instruction generation used by:
- simple_szz_agent.py
- szz_agent_stage_01.py
- szz_agent_stage_02.py
"""


# =============================================================================
# SIMPLE SZZ AGENT / SZZ AGENT STAGE 02 (shared prompt)
# =============================================================================
def create_candidate_selection_instructions(
    num_candidates: int,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> str:
    """Create the instructions file for candidate selection task.

    Used by simple_szz_agent.py and szz_agent_stage_02.py (hybrid candidate selection).
    """

    files_provided = "## Files Provided\n"

    if not without_fc_message:
        files_provided += """
### fix_commit_message.txt
The commit message from the fix commit, which describes what bug was fixed.
"""

    if not without_fc_diff:
        files_provided += """
### fix_commit_diff.txt
The diff (patch) of the fix commit, showing exactly what code changes were made to fix the bug.
This is crucial for understanding the precise nature of the fix.
"""

    files_provided += f"""
### candidates/
Contains {num_candidates} candidate commit diffs, named `candidate_01.diff` through `candidate_{num_candidates:02d}.diff`.
These are ordered chronologically (oldest to newest).

**IMPORTANT**: The candidate file names are neutral (candidate_01, candidate_02, etc.) and do NOT reveal any information about which commit introduced the bug. You must analyze the actual code changes to make your determination.
"""

    if not without_fc_message and not without_fc_diff:
        understand_bug = "1. **Understand the bug**: Read `fix_commit_message.txt` and carefully analyze `fix_commit_diff.txt` to understand:"
    elif not without_fc_message:
        understand_bug = "1. **Understand the bug**: Read `fix_commit_message.txt` to understand:"
    elif not without_fc_diff:
        understand_bug = "1. **Understand the bug**: Carefully analyze `fix_commit_diff.txt` to understand:"
    else:
        understand_bug = "1. **Understand the bug**: Analyze the candidate diffs to understand:"

    return f"""# Bug-Introducing Commit Identification Task

## Objective
Identify which of the {num_candidates} candidate commits introduced the bug that was later fixed.

{files_provided}
## Instructions

{understand_bug}
   - What bug was being fixed
   - What code patterns or logic errors caused the bug
   - What the correct behavior should be

2. **Analyze each candidate**: For each candidate diff in `candidates/`:
   - Examine what code changes were introduced
   - Determine if this commit could have introduced the bug
   - Look for the specific problematic code patterns that the fix addresses

3. **Identify the bug-introducing commit**: Determine which candidate(s) introduced the bug.
   - The bug-introducing commit is the FIRST commit that introduced the problematic code
   - Sometimes the bug could have been introduced by one of several commits
   - If you're confident about a single commit, select that one
   - If multiple commits could have introduced the bug, select the most likely one

4. **Use static analysis only**: Do not execute any code. Make your determination based purely on code analysis.

## Output

Write your result to `result.txt` in the following format:

```
SELECTED: candidate_XX

CONFIDENCE: HIGH|MEDIUM|LOW

EXPLANATION:
[Your detailed explanation of why you selected this candidate as the bug-introducing commit.
Explain what the bug was and how this commit introduced it.]
```

**Format Notes**:
- For SELECTED, use the format `candidate_XX` where XX is the two-digit number (e.g., `candidate_05`)
- If you believe multiple candidates could have introduced the bug, select the most likely one
- CONFIDENCE should be HIGH if you're very confident, MEDIUM if reasonably sure, LOW if uncertain

## Important Notes
- Focus on finding the commit that INTRODUCED the bug, not just commits that modified related code
- Earlier candidates may not contain the buggy code at all (the code might not exist yet)
- Later candidates may have the bug because it was already present (inherited from earlier commits)
- The key is finding where the problematic code was FIRST introduced
"""


# =============================================================================
# SZZ AGENT STAGE 01
# =============================================================================
def create_stage01_candidate_selection_instructions(
    num_candidates: int,
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> str:
    """Create the instructions file for stage 01 candidate selection task.

    Unlike the shared version, this prompt allows the agent to select "NONE"
    if none of the candidates introduced the bug.
    """

    # Build files provided section
    files_provided = "## Files Provided\n"

    if not without_fc_message:
        files_provided += """
### fix_commit_message.txt
The commit message from the fix commit, which describes what bug was fixed.
"""

    if not without_fc_diff:
        files_provided += """
### fix_commit_diff.txt
The diff (patch) of the fix commit, showing exactly what code changes were made to fix the bug.
This is crucial for understanding the precise nature of the fix.
"""

    files_provided += f"""
### candidates/
Contains {num_candidates} candidate commit diffs, named `candidate_01.diff` through `candidate_{num_candidates:02d}.diff`.
Each file also contains the commit message at the top.
"""

    if not without_fc_message and not without_fc_diff:
        understand_bug = "1. **Understand the bug**: Read `fix_commit_message.txt` and carefully analyze `fix_commit_diff.txt` to understand:"
    elif not without_fc_message:
        understand_bug = "1. **Understand the bug**: Read `fix_commit_message.txt` to understand:"
    elif not without_fc_diff:
        understand_bug = "1. **Understand the bug**: Carefully analyze `fix_commit_diff.txt` to understand:"
    else:
        understand_bug = "1. **Understand the bug**: Analyze the candidate diffs to understand:"

    return f"""# Bug-Introducing Commit Identification Task

## Objective
Identify which of the {num_candidates} candidate commits introduced the bug that was later fixed.

**IMPORTANT**: It is possible that NONE of the candidate commits are the actual bug-introducing commit.
If after careful analysis you determine that none of the candidates introduced the bug, you should select "NONE".

{files_provided}
## Instructions

{understand_bug}
   - What bug was being fixed
   - What code patterns or logic errors caused the bug
   - What the correct behavior should be

2. **Analyze each candidate**: For each candidate diff in `candidates/`:
   - Examine what code changes were introduced
   - Determine if this commit could have introduced the bug
   - Look for the specific problematic code patterns that the fix addresses

3. **Identify the bug-introducing commit**: Determine which candidate introduced the bug.
   - The bug-introducing commit is the FIRST commit that introduced the problematic code
   - If you're confident about a single commit, select that one
   - If you determine that NONE of the candidates introduced the bug, select "NONE"

4. **Use static analysis only**: Do not execute any code. Make your determination based purely on code analysis.

## Output

Write your result to `result.txt` in the following format:

```
SELECTED: candidate_XX

CONFIDENCE: HIGH|MEDIUM|LOW

EXPLANATION:
[Your detailed explanation of why you selected this candidate as the bug-introducing commit,
or why you believe none of the candidates are the bug-introducing commit.]
```

**Format Notes**:
- For SELECTED, use the format `candidate_XX` where XX is the two-digit number (e.g., `candidate_05`)
- If none of the candidates introduced the bug, use: `SELECTED: NONE`
- CONFIDENCE should be HIGH if you're very confident, MEDIUM if reasonably sure, LOW if uncertain

## Important Notes
- Focus on finding the commit that INTRODUCED the bug, not just commits that modified related code
- It's okay to select NONE if you don't find evidence that any candidate introduced the bug
- Be thorough but decisive in your analysis
"""


# =============================================================================
# SZZ AGENT STAGE 02 (binary search prompt)
# =============================================================================
def create_binary_search_instructions(
    without_fc_message: bool = False,
    without_fc_diff: bool = False
) -> str:
    """Create the instructions file for the binary search bug presence detection task.

    Used by szz_agent_stage_02.py during the binary search phase.
    """

    # Build files provided section
    files_provided = "## Files Provided\n"

    if not without_fc_diff:
        files_provided += """
### files/buggy/
Contains the buggy version of the code (immediately before the fix was applied).
This version **definitely contains the bug**.

### files/fixed/
Contains the fixed version of the code (after the fix was applied).
This version **does not contain the bug**.
"""

    files_provided += """
### files/old/
Contains an older version of the code from the project's history.
Your task is to determine whether this version contains the bug or not.
"""

    if not without_fc_message:
        files_provided += """
### fix_commit_message.txt
The commit message from the fix commit, which may provide context about what bug was fixed.
"""

    if not without_fc_diff:
        files_provided += """
### fix_commit_diff.txt
The diff (patch) of the fix commit, showing exactly what code changes were made to fix the bug.
This is useful for understanding the precise nature of the fix.
"""

    # Build instructions section
    if not without_fc_diff:
        understand_bug = "1. **Understand the bug**: Compare `files/buggy/` with `files/fixed/` and review `fix_commit_diff.txt` to understand exactly what bug was fixed."
    elif not without_fc_message:
        understand_bug = "1. **Understand the bug**: Read `fix_commit_message.txt` and analyze `files/old/` to understand what bug may be present."
    else:
        understand_bug = "1. **Understand the bug**: Analyze `files/old/` to determine if it contains a bug."

    return f"""# Bug Presence Detection Task

## Objective
Determine whether a specific bug exists in the "old" version of the code.

{files_provided}
## Instructions

{understand_bug}

2. **Analyze the old version**: Examine `files/old/` to determine if this version contains the same bug.

3. **Use static analysis only**: Do not execute any code. Make your determination based purely on code analysis.

4. **Consider these scenarios**:
   - The bug might not exist in `old/` if the buggy code was introduced later
   - The bug might exist in `old/` if the buggy code already existed
   - If the relevant code section doesn't exist in `old/`, the bug cannot be present

## Output

Write your result to `result.txt` in the following format:

```
VERDICT: BUG_PRESENT
or
VERDICT: BUG_NOT_PRESENT

CONFIDENCE: HIGH|MEDIUM|LOW

EXPLANATION:
[Your detailed explanation of why the bug is or is not present in the old version]
```

## Important Notes
- Focus on the specific bug that was fixed, not other potential issues
- If you cannot determine the bug from the available information, explain why and use your best judgment
- Be precise in your analysis - this is a binary determination
"""
