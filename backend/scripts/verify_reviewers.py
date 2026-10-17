#!/usr/bin/env python3
"""
Independent verification script for Milestone 3 (reviewers_for).
Compares FalkorDB graph query results against raw git log ground truth.
Enforces Trust Contract Rule: Claims must match ground truth 100%.
"""

import sys
import os
import subprocess

# Ensure backend package is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.falkor import FalkorDBManager
from app.tools.reviewers import reviewers_for


def verify_file(target_file: str, repo_path: str = "data/repos/arth"):
    print("=" * 60)
    print(f"VERIFYING: {target_file}")
    print("=" * 60)

    if not os.path.isdir(repo_path):
        print(f"Error: Repo path not found: {repo_path}")
        sys.exit(1)

    # 1. Compute raw Git ground truth
    cmd = [
        "git",
        "-C",
        repo_path,
        "log",
        "--no-renames",
        "--numstat",
        "--date=iso-strict",
        '--pretty=format:COMMIT_INFO%x09%H%x09%an%x09%ae%x09%ad',
        "--",
        target_file,
    ]
    raw_git = subprocess.run(cmd, capture_output=True, text=True, check=True)

    git_commits = []
    git_authors = set()
    git_added = 0
    git_deleted = 0
    latest_sha = ""
    latest_ts = ""

    current_commit = None
    for line in raw_git.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT_INFO\t"):
            parts = line.split("\t")
            sha = parts[1]
            an = parts[2]
            ae = parts[3]
            ad = parts[4]
            current_commit = sha
            git_commits.append(sha)
            git_authors.add((an, ae))
            if not latest_sha:
                latest_sha = sha
                latest_ts = ad
        elif current_commit and "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2] == target_file:
                add_str, del_str = parts[0], parts[1]
                git_added += int(add_str) if add_str.isdigit() else 0
                git_deleted += int(del_str) if del_str.isdigit() else 0

    print(f"Raw Git Ground Truth:")
    print(f"  Total Commits   : {len(git_commits)}")
    print(f"  Distinct Authors: {len(git_authors)}")
    print(f"  Lines Added     : {git_added}")
    print(f"  Lines Deleted   : {git_deleted}")
    print(f"  Latest SHA      : {latest_sha}")

    # 2. Query FalkorDB graph
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        print("Error: FalkorDB is not connected!")
        sys.exit(1)

    result = reviewers_for(target_file=target_file, repo_name="arth", db_manager=db)

    print("\nFalkorDB Graph Result:")
    if not result.candidates:
        print("  No candidates found in graph!")
        sys.exit(1)

    top = result.candidates[0]
    print(f"  Top Candidate   : {top.person_name} <{top.person_email}>")
    print(f"  Graph Commits   : {top.commit_count}")
    print(f"  Graph Added     : {top.lines_added}")
    print(f"  Graph Deleted   : {top.lines_deleted}")
    print(f"  Graph Latest SHA: {top.last_commit_sha}")
    print(f"  Evidence SHAs   : {len(top.evidence_commits)} items")

    # 3. Direct comparison & assertions
    print("\nComparison Check:")
    m1 = len(git_commits) == top.commit_count
    m2 = git_added == top.lines_added
    m3 = git_deleted == top.lines_deleted
    m4 = latest_sha == top.last_commit_sha
    m5 = set(top.evidence_commits).issubset(set(git_commits))

    print(f"  - Commit Count Matches : {'PASS' if m1 else 'FAIL'}")
    print(f"  - Lines Added Matches  : {'PASS' if m2 else 'FAIL'}")
    print(f"  - Lines Deleted Matches: {'PASS' if m3 else 'FAIL'}")
    print(f"  - Latest SHA Matches   : {'PASS' if m4 else 'FAIL'}")
    print(f"  - Evidence SHAs Valid  : {'PASS' if m5 else 'FAIL'}")

    if m1 and m2 and m3 and m4 and m5:
        print("\nSUCCESS: FalkorDB graph result 100% matches raw Git ground truth!")
        return True
    else:
        print("\nFAILURE: Discrepancy detected between Git ground truth and FalkorDB graph!")
        return False


if __name__ == "__main__":
    test_files = ["render.yaml", "backend/app/api/v1/internal.py"]
    all_ok = True
    for f in test_files:
        if not verify_file(f):
            all_ok = False

    sys.exit(0 if all_ok else 1)
