"""
Ingest sample benchmark repositories (ARTH and encode/databases) into FalkorDB.
Used for fresh-clone initialization and judge evaluation.
"""
import os
import sys

# Ensure backend modules are on Python path
sys.path.insert(0, os.path.abspath("backend"))

from app.db.falkor import FalkorDBManager
from app.ingest.git_parser import parse_git_history
from app.ingest.graph_loader import GitGraphLoader


def ingest_sample_repositories():
    db = FalkorDBManager()
    health = db.check_health()
    if not health.get("connected"):
        print(f"ERROR: Cannot connect to FalkorDB at {db.host}:{db.port}. Ensure FalkorDB is running.")
        sys.exit(1)

    loader = GitGraphLoader(db)
    repos = [
        ("arth", "data/repos/arth"),
        ("databases", "data/repos/databases"),
    ]

    for repo_name, repo_path in repos:
        if not os.path.exists(repo_path):
            print(f"WARNING: Repository path '{repo_path}' does not exist.")
            print(f"  Please clone it first: git clone https://github.com/... {repo_path}")
            continue

        print(f"Ingesting '{repo_name}' from '{repo_path}'...")
        commits = parse_git_history(repo_path=repo_path, max_commits=2000)
        summary = loader.load_history(
            repo_name=repo_name,
            commits=commits,
            repo_path=repo_path,
            graph_name="anvaya",
        )
        print(f"  Successfully ingested '{repo_name}': {summary.commit_count} commits, "
              f"{summary.file_count} files, {summary.person_count} contributors.")

    print("\nCanonical ingestion complete. Production graph 'anvaya' is ready.")


if __name__ == "__main__":
    ingest_sample_repositories()
