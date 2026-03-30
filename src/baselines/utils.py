import subprocess
from pathlib import Path
from typing import Dict, List, Set


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
