from typing import List, Optional, Dict, Any
import logging

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.tools.models import ReviewerCandidate, ReviewersResponse

logger = logging.getLogger(__name__)


def reviewers_for(
    target_file: Optional[str] = None,
    target_module: Optional[str] = None,
    repo_name: str = "arth",
    exclude_emails: Optional[List[str]] = None,
    limit: int = 10,
    max_evidence: int = 20,
    graph_name: Optional[str] = None,
    db_manager: Optional[FalkorDBManager] = None,
) -> ReviewersResponse:
    """
    Deterministic, evidence-backed reviewer reasoning using graph relationships.
    Derives candidates exclusively from authorship history and recency.
    Enforces Trust Contract: No arbitrary LLM Cypher, no fake scores.
    """
    if bool(target_file) == bool(target_module):
        raise ValueError("Must provide exactly one of target_file or target_module")

    manager = db_manager or get_falkor_manager()
    graph = manager.get_graph(graph_name)

    excluded = exclude_emails or []
    target = target_file if target_file is not None else target_module
    target_type = "file" if target_file is not None else "module"

    # Retrieve graph freshness sync info if available
    sync_info: Optional[Dict[str, Any]] = None
    try:
        sync_query = """
        MATCH (r:Repository {name: $repo_name})-[:SNAPSHOT]->(s:Sync)
        RETURN s.id, s.head_sha, s.synced_at, s.commit_count
        ORDER BY s.synced_at DESC
        LIMIT 1
        """
        sync_res = graph.query(sync_query, {"repo_name": repo_name})
        if sync_res.result_set:
            s_row = sync_res.result_set[0]
            sync_info = {
                "sync_id": s_row[0],
                "head_sha": s_row[1],
                "synced_at": s_row[2],
                "commit_count": s_row[3],
            }
    except Exception as exc:
        logger.debug("Could not read sync freshness: %s", exc)

    if target_type == "file":
        query = """
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File)
        WHERE f.path = $target_path AND NOT (p.email IN $exclude_emails)
        WITH p, c, t
        ORDER BY c.ts DESC
        WITH p,
             count(DISTINCT c) AS commit_count,
             sum(t.added) AS lines_added,
             sum(t.deleted) AS lines_deleted,
             collect(c.sha) AS all_shas,
             collect(c.ts) AS all_ts
        RETURN p.name AS person_name,
               p.email AS person_email,
               commit_count,
               lines_added,
               lines_deleted,
               all_ts[0] AS last_commit_at,
               all_shas[0] AS last_commit_sha,
               all_shas AS evidence_commits
        ORDER BY commit_count DESC, lines_added + lines_deleted DESC, last_commit_at DESC
        LIMIT $limit
        """
        params = {
            "target_path": target,
            "exclude_emails": excluded,
            "limit": limit,
        }
    else:
        query = """
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File)-[:IN_MODULE]->(m:Module)
        WHERE m.path = $target_module AND NOT (p.email IN $exclude_emails)
        WITH p, c, t
        ORDER BY c.ts DESC
        WITH p,
             count(DISTINCT c) AS commit_count,
             sum(t.added) AS lines_added,
             sum(t.deleted) AS lines_deleted,
             collect(DISTINCT c.sha) AS all_shas,
             collect(DISTINCT c.ts) AS all_ts
        RETURN p.name AS person_name,
               p.email AS person_email,
               commit_count,
               lines_added,
               lines_deleted,
               all_ts[0] AS last_commit_at,
               all_shas[0] AS last_commit_sha,
               all_shas AS evidence_commits
        ORDER BY commit_count DESC, lines_added + lines_deleted DESC, last_commit_at DESC
        LIMIT $limit
        """
        params = {
            "target_module": target,
            "exclude_emails": excluded,
            "limit": limit,
        }

    res = graph.query(query, params)
    candidates: List[ReviewerCandidate] = []

    if res and res.result_set:
        for row in res.result_set:
            person_name = str(row[0]) if row[0] is not None else ""
            person_email = str(row[1]) if row[1] is not None else ""
            commit_count = int(row[2]) if row[2] is not None else 0
            lines_added = int(row[3]) if row[3] is not None else 0
            lines_deleted = int(row[4]) if row[4] is not None else 0
            last_commit_at = str(row[5]) if row[5] is not None else ""
            last_commit_sha = str(row[6]) if row[6] is not None else ""
            raw_shas = row[7] if isinstance(row[7], list) else []

            # Slice evidence commits in application layer to guarantee bound
            evidence = [str(sha) for sha in raw_shas[:max_evidence]]

            candidates.append(
                ReviewerCandidate(
                    person_name=person_name,
                    person_email=person_email,
                    commit_count=commit_count,
                    lines_added=lines_added,
                    lines_deleted=lines_deleted,
                    last_commit_at=last_commit_at,
                    last_commit_sha=last_commit_sha,
                    target=target,
                    target_type=target_type,
                    evidence_commits=evidence,
                )
            )

    return ReviewersResponse(
        target=target,
        target_type=target_type,
        repo_name=repo_name,
        candidates=candidates,
        total_candidates=len(candidates),
        sync_info=sync_info,
    )
