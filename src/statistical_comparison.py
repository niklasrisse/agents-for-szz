#!/usr/bin/env python3
"""Statistical comparison of BIC prediction methods using Wilcoxon signed-rank test.

Compares the main method (szz_agent_stage_02) against all other baselines
in a results directory, using F1 score as the metric.

Usage:
    python statistical_comparison.py DS_LINUX
    python statistical_comparison.py DS_GITHUB-j
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
from scipy import stats

from evaluation_utils import evaluate_results


def load_results(filepath: Path) -> Tuple[List[Dict], Dict]:
    """Load results from a JSON file.

    Returns:
        - List of result entries
        - Metadata dict
    """
    with open(filepath, 'r') as f:
        data = json.load(f)

    if isinstance(data, dict) and "results" in data:
        metadata = data.get("metadata", {})
        return data["results"], metadata
    else:
        return data, {}


def calculate_entry_f1(entry: Dict) -> Optional[float]:
    """Calculate F1 for a single entry."""
    gt_bics = set(entry.get("ground_truth_bics", []))
    predicted_bics = entry.get("predicted_bics", [])

    if not gt_bics:
        return None

    from evaluation_utils import count_matching_commits
    num_matches = count_matching_commits(predicted_bics, gt_bics)

    recall = num_matches / len(gt_bics)

    if len(predicted_bics) > 0:
        precision = num_matches / len(predicted_bics)
    else:
        precision = 0.0

    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    return f1


def align_results(results_a: List[Dict], results_b: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Align results by entry ID to ensure paired comparison."""
    lookup_a = {e.get("id"): e for e in results_a}
    lookup_b = {e.get("id"): e for e in results_b}

    common_ids = sorted(set(lookup_a.keys()) & set(lookup_b.keys()))

    aligned_a = [lookup_a[id_] for id_ in common_ids]
    aligned_b = [lookup_b[id_] for id_ in common_ids]

    return aligned_a, aligned_b


def extract_f1_pairs(
    results_a: List[Dict],
    results_b: List[Dict]
) -> Tuple[np.ndarray, np.ndarray]:
    """Extract paired F1 values from two result sets."""
    values_a = []
    values_b = []

    for entry_a, entry_b in zip(results_a, results_b):
        f1_a = calculate_entry_f1(entry_a)
        f1_b = calculate_entry_f1(entry_b)

        if f1_a is not None and f1_b is not None:
            values_a.append(f1_a)
            values_b.append(f1_b)

    return np.array(values_a), np.array(values_b)


def wilcoxon_signed_rank_test(
    values_a: np.ndarray,
    values_b: np.ndarray
) -> Dict:
    """Perform Wilcoxon Signed-Rank Test."""
    differences = values_a - values_b

    n_ties = np.sum(differences == 0)
    n_total = len(differences)
    tie_percentage = 100 * n_ties / n_total if n_total > 0 else 0

    non_zero_diff = differences[differences != 0]
    n_non_zero = len(non_zero_diff)

    result = {
        "statistic": None,
        "p_value": None,
        "n_pairs": int(n_total),
        "n_non_zero": int(n_non_zero),
        "n_ties": int(n_ties),
        "tie_percentage": float(tie_percentage),
    }

    if n_non_zero == 0:
        return result

    try:
        stat_result = stats.wilcoxon(values_a, values_b, alternative='two-sided', zero_method='wilcox')
        result["statistic"] = float(stat_result.statistic)
        result["p_value"] = float(stat_result.pvalue)
    except Exception:
        pass

    return result


def rank_biserial_correlation(values_a: np.ndarray, values_b: np.ndarray) -> Tuple[Optional[float], str]:
    """Calculate rank-biserial correlation (effect size for Wilcoxon test)."""
    differences = values_a - values_b
    non_zero_diff = differences[differences != 0]
    n = len(non_zero_diff)

    if n == 0:
        return None, "undefined"

    abs_diff = np.abs(non_zero_diff)
    ranks = stats.rankdata(abs_diff)

    positive_mask = non_zero_diff > 0
    negative_mask = non_zero_diff < 0

    sum_positive_ranks = np.sum(ranks[positive_mask])
    sum_negative_ranks = np.sum(ranks[negative_mask])
    total_rank_sum = np.sum(ranks)

    if total_rank_sum > 0:
        r = (sum_positive_ranks - sum_negative_ranks) / total_rank_sum
    else:
        r = 0.0

    abs_r = abs(r)
    if abs_r < 0.1:
        interpretation = "negligible"
    elif abs_r < 0.3:
        interpretation = "small"
    elif abs_r < 0.5:
        interpretation = "medium"
    else:
        interpretation = "large"

    return float(r), interpretation


def compare_methods(
    main_results: List[Dict],
    main_metadata: Dict,
    baseline_results: List[Dict],
    baseline_metadata: Dict
) -> Dict:
    """Compare main method against a baseline."""
    # Align results
    aligned_main, aligned_baseline = align_results(main_results, baseline_results)

    if len(aligned_main) == 0:
        return None

    # Extract F1 pairs
    f1_main, f1_baseline = extract_f1_pairs(aligned_main, aligned_baseline)

    if len(f1_main) == 0:
        return None

    # Calculate F1 scores using evaluation_utils (same as baseline scripts)
    main_eval = evaluate_results(aligned_main)
    baseline_eval = evaluate_results(aligned_baseline)
    avg_f1_main = main_eval["f1_score"]
    avg_f1_baseline = baseline_eval["f1_score"]

    # Run Wilcoxon test
    wilcoxon_result = wilcoxon_signed_rank_test(f1_main, f1_baseline)

    # Calculate effect size
    r, r_interpretation = rank_biserial_correlation(f1_main, f1_baseline)

    # Determine result at p=0.01
    p_value = wilcoxon_result["p_value"]
    if p_value is None:
        result_p01 = "Test could not be performed"
    elif p_value < 0.01:
        result_p01 = "REJECT H0 - Significant difference"
    else:
        result_p01 = "FAIL TO REJECT H0 - No significant difference"

    return {
        "main_algorithm": main_metadata.get("algorithm", "unknown"),
        "main_model": main_metadata.get("model"),
        "main_avg_f1": avg_f1_main,
        "baseline_algorithm": baseline_metadata.get("algorithm", "unknown"),
        "baseline_model": baseline_metadata.get("model"),
        "baseline_avg_f1": avg_f1_baseline,
        "n_paired_samples": wilcoxon_result["n_pairs"],
        "H0": "Median difference in F1 = 0",
        "H1": "Median difference in F1 != 0",
        "p_value": p_value,
        "result_p_0.01": result_p01,
        "rank_biserial_r": r,
        "rank_biserial_interpretation": r_interpretation,
        "tie_percentage": wilcoxon_result["tie_percentage"],
    }


def find_result_files(directory: Path) -> Tuple[Optional[Path], List[Path]]:
    """Find main method and baseline files in directory.

    Returns:
        - Path to main method file (szz_agent_stage_02*)
        - List of baseline file paths (excluding szz_agent_stage_01*)
    """
    json_files = list(directory.glob("*.json"))

    main_file = None
    baseline_files = []

    for f in json_files:
        name = f.name
        if name.startswith("szz_agent_stage_02"):
            main_file = f
        elif name.startswith("szz_agent_stage_01"):
            # Skip stage 01 files
            continue
        elif name == "statistical_comparison.json":
            # Skip output file
            continue
        else:
            baseline_files.append(f)

    return main_file, baseline_files


def run_directory_comparison(directory_name: str) -> List[Dict]:
    """Run statistical comparison for all baselines in a directory."""
    base_path = Path(__file__).resolve().parent.parent / "results_for_paper"
    directory = base_path / directory_name

    if not directory.exists():
        print(f"ERROR: Directory not found: {directory}")
        sys.exit(1)

    main_file, baseline_files = find_result_files(directory)

    if main_file is None:
        print(f"ERROR: No szz_agent_stage_02* file found in {directory}")
        sys.exit(1)

    if not baseline_files:
        print(f"ERROR: No baseline files found in {directory}")
        sys.exit(1)

    print(f"Directory: {directory}")
    print(f"Main method: {main_file.name}")
    print(f"Baselines: {len(baseline_files)} files")
    print()

    # Load main method results
    main_results, main_metadata = load_results(main_file)
    print(f"Loaded {len(main_results)} entries from main method")

    # Compare with each baseline
    comparisons = []

    for baseline_file in sorted(baseline_files):
        print(f"Comparing with: {baseline_file.name}")

        baseline_results, baseline_metadata = load_results(baseline_file)

        comparison = compare_methods(
            main_results, main_metadata,
            baseline_results, baseline_metadata
        )

        if comparison:
            comparisons.append(comparison)
            p_val = comparison['p_value']
            p_str = f"{p_val:.4f}" if p_val is not None else "N/A"
            print(f"  Main F1: {comparison['main_avg_f1']:.4f}, "
                  f"Baseline F1: {comparison['baseline_avg_f1']:.4f}, "
                  f"p={p_str}")
        else:
            print(f"  WARNING: Could not compare (no common entries)")

    # Save results
    output_file = directory / "statistical_comparison.json"
    with open(output_file, 'w') as f:
        json.dump(comparisons, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    return comparisons


def print_summary(comparisons: List[Dict]):
    """Print a summary table of comparisons."""
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    print(f"\n{'Baseline':<30} {'Main F1':>10} {'Base F1':>10} {'p-value':>12} {'Effect':>10} {'Result (p=0.01)':<30}")
    print("-" * 100)

    for c in comparisons:
        baseline = c['baseline_algorithm'][:28]
        main_f1 = f"{c['main_avg_f1']:.4f}"
        base_f1 = f"{c['baseline_avg_f1']:.4f}"
        p_val = f"{c['p_value']:.4f}" if c['p_value'] else "N/A"
        effect = c['rank_biserial_interpretation']
        result = "SIGNIFICANT" if c['p_value'] and c['p_value'] < 0.01 else "Not sig."

        print(f"{baseline:<30} {main_f1:>10} {base_f1:>10} {p_val:>12} {effect:>10} {result:<30}")

    print("=" * 100)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Statistical comparison of BIC prediction methods",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python statistical_comparison.py DS_LINUX
    python statistical_comparison.py DS_GITHUB-j
        """
    )
    parser.add_argument(
        "directory",
        type=str,
        help="Directory name in results_for_paper/ (e.g., DS_LINUX)"
    )

    args = parser.parse_args()

    comparisons = run_directory_comparison(args.directory)

    if comparisons:
        print_summary(comparisons)


if __name__ == "__main__":
    main()
