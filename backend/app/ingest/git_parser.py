from typing import List, Optional
import subprocess
import os
import re

from app.ingest.models import GitCommit, GitTouch


def get_module_path(file_path: str) -> str:
    """
    Derives the module path for a file using directory structure rules:
    - Root files map to '.'
    - Nested code in standard monorepo roots (src, packages, apps, lib) maps to 2nd-level dir
    - All other files map to the top-level directory
    """
    normalized = file_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")

    if len(parts) <= 1:
        return "."

    # Standard nested directories that benefit from 2nd-level module grouping
    if parts[0] in ("src", "packages", "apps", "lib") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"

    return parts[0]


def parse_git_log_output(raw_text: str) -> List[GitCommit]:
    """
    Parses git log output formatted with custom commit separators and numstat.
    """
    commits: List[GitCommit] = []
    if not raw_text or not raw_text.strip():
        return commits

    # Split by commit separator token
    raw_blocks = raw_text.split("COMMIT_SEP\t")

    for block in raw_blocks:
        stripped = block.strip()
        if not stripped:
            continue

        lines = stripped.splitlines()
        header_line = lines[0].strip()
        header_parts = header_line.split("\t")

        # Expect: sha, author_name, author_email, timestamp, subject
        if len(header_parts) < 5:
            continue

        sha = header_parts[0].strip()
        author_name = header_parts[1].strip()
        author_email = header_parts[2].strip()
        timestamp = header_parts[3].strip()
        subject = header_parts[4].strip() if len(header_parts) >= 5 else ""

        touches: List[GitTouch] = []

        # Subsequent lines are numstat records: <added>\t<deleted>\t<path>
        for line in lines[1:]:
            line_str = line.strip()
            if not line_str:
                continue

            numstat_parts = line_str.split("\t")
            if len(numstat_parts) >= 3:
                added_raw = numstat_parts[0].strip()
                deleted_raw = numstat_parts[1].strip()
                path = numstat_parts[2].strip()

                added = int(added_raw) if added_raw.isdigit() else 0
                deleted = int(deleted_raw) if deleted_raw.isdigit() else 0
                module = get_module_path(path)

                touches.append(
                    GitTouch(
                        path=path,
                        added=added,
                        deleted=deleted,
                        module=module,
                    )
                )

        commits.append(
            GitCommit(
                sha=sha,
                author_name=author_name,
                author_email=author_email,
                timestamp=timestamp,
                subject=subject,
                touches=touches,
            )
        )

    return commits


def parse_git_history(
    repo_path: str,
    max_commits: int = 2000,
    since: Optional[str] = None,
) -> List[GitCommit]:
    """
    Executes a bounded git log command in the target repo directory and parses commits.
    Enforces Rule 8 (honest Git history) and bounded history window.
    """
    if not os.path.isdir(repo_path):
        raise FileNotFoundError(f"Repository directory does not exist: {repo_path}")

    cmd = [
        "git",
        "-C",
        repo_path,
        "log",
        f"-n{max_commits}",
        "--no-renames",
        "--numstat",
        "--date=iso-strict",
        '--pretty=format:COMMIT_SEP%x09%H%x09%an%x09%ae%x09%ad%x09%s',
    ]

    if since:
        cmd.append(f"--since={since}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )

    return parse_git_log_output(result.stdout)
