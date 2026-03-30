"""Shared evaluation utilities for all baseline scripts.

This module implements the macro-averaged recall, precision, and F1 metrics
as described in SZZ evaluation literature:

    recall = (1/N) * Σ (|correct_ci ∩ identified_ci| / |correct_ci|)
    precision = (1/N) * Σ (|correct_ci ∩ identified_ci| / |identified_ci|)
    F1 = 2 * (recall × precision) / (recall + precision)

Where:
    - correct_ci = ground truth bug-inducing commits for fix commit ci
    - identified_ci = predicted bug-inducing commits for fix commit ci
    - N = total number of bug-fixing commits with ground truth
"""

from typing import Dict, List, Set


def is_commit_match(commit_hash: str, ground_truth: Set[str]) -> bool:
    """Check if a commit matches any ground truth using prefix matching."""
    for gt in ground_truth:
        if commit_hash.startswith(gt) or gt.startswith(commit_hash):
            return True
    return False


def count_matching_commits(predicted_bics: List[str], gt_bics: Set[str]) -> int:
    """Count how many predicted commits match ground truth."""
    matches = 0
    matched_gt = set()  # Track which GT commits have been matched
    for pred in predicted_bics:
        for gt in gt_bics:
            if gt not in matched_gt and (pred.startswith(gt) or gt.startswith(pred)):
                matches += 1
                matched_gt.add(gt)
                break
    return matches


def evaluate_results(results: List[Dict]) -> Dict:
    """Evaluate results using macro-averaged metrics.

    For each bug-fixing commit ci:
        - recall_ci = |correct ∩ identified| / |correct|
        - precision_ci = |correct ∩ identified| / |identified|

    Final metrics are averaged across all N commits:
        - recall = (1/N) * Σ recall_ci
        - precision = (1/N) * Σ precision_ci
        - F1 = 2 * (recall × precision) / (recall + precision)
    """
    recall_sum = 0.0
    precision_sum = 0.0
    n_entries = 0

    entries_with_predictions = 0
    total_correct_matches = 0

    for entry in results:
        gt_bics = set(entry.get("ground_truth_bics", []))
        predicted_bics = entry.get("predicted_bics", [])

        # Skip entries without ground truth
        if not gt_bics:
            entry["recall"] = None
            entry["precision"] = None
            entry["correct"] = False
            entry["gt_in_predictions"] = False
            continue

        n_entries += 1

        # Count matching commits (intersection)
        num_matches = count_matching_commits(predicted_bics, gt_bics)

        # Calculate per-entry recall: |correct ∩ identified| / |correct|
        entry_recall = num_matches / len(gt_bics)
        recall_sum += entry_recall
        entry["recall"] = entry_recall

        # Calculate per-entry precision: |correct ∩ identified| / |identified|
        if len(predicted_bics) > 0:
            entry_precision = num_matches / len(predicted_bics)
            entries_with_predictions += 1
        else:
            entry_precision = 0.0
        precision_sum += entry_precision
        entry["precision"] = entry_precision

        # Track if any prediction matches (for backward compatibility)
        entry["correct"] = num_matches > 0
        entry["gt_in_predictions"] = num_matches > 0
        entry["num_matches"] = num_matches

        if num_matches > 0:
            total_correct_matches += 1

    # Calculate macro-averaged metrics
    recall = recall_sum / n_entries if n_entries > 0 else 0
    precision = precision_sum / n_entries if n_entries > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "total_entries": len(results),
        "entries_with_ground_truth": n_entries,
        "entries_with_predictions": entries_with_predictions,
        "entries_with_correct_prediction": total_correct_matches,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
    }


def print_summary(summary: Dict, algorithm_name: str = "SZZ") -> None:
    """Print evaluation summary."""
    print("\n" + "=" * 70)
    print(f"                    {algorithm_name} EVALUATION RESULTS")
    print("=" * 70)
    print(f"\nTotal entries:               {summary['total_entries']}")
    print(f"Entries with ground truth:   {summary['entries_with_ground_truth']}")
    print(f"Entries with predictions:    {summary['entries_with_predictions']}")
    print(f"Entries with correct pred:   {summary['entries_with_correct_prediction']}")
    print(f"\nMacro-averaged Metrics:")
    print(f"  Precision:                 {summary['precision']:.4f}")
    print(f"  Recall:                    {summary['recall']:.4f}")
    print(f"  F1 Score:                  {summary['f1_score']:.4f}")
    print("=" * 70)
