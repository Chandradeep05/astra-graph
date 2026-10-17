from typing import List, Optional, Dict, Any
import datetime
import logging

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.tools.models import (
    ReviewerCandidate,
    ReviewersResponse,
    VerifiedReviewersResponse,
)
from app.evidence.models import (
    Claim,
    Evidence,
    RefusalCode,
)
from app.evidence.verifier import EvidenceVerifier

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
    Deterministic reviewer reasoning using graph relationships.
    Derives candidates exclusively from authorship history and recency.
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


def get_verified_reviewers(
    target_file: Optional[str] = None,
    target_module: Optional[str] = None,
    repo_name: str = "arth",
    exclude_emails: Optional[List[str]] = None,
    limit: int = 10,
    max_evidence: int = 20,
    max_sync_age_days: Optional[int] = None,
    graph_name: Optional[str] = None,
    db_manager: Optional[FalkorDBManager] = None,
) -> VerifiedReviewersResponse:
    """
    Executes reviewer intelligence wrapped with the Trust Contract:
    1. Checks graph freshness / sync staleness rules.
    2. Executes deterministic Cypher traversal.
    3. If zero supporting path exists, REFUSES immediately before any model call.
    4. Constructs structured Evidence and Claims.
    5. Runs EvidenceVerifier to ensure 100% evidentiary backing.
    """
    target = target_file if target_file is not None else target_module
    target_type = "file" if target_file is not None else "module"

    if not target:
        raise ValueError("Must provide either target_file or target_module")

    manager = db_manager or get_falkor_manager()
    graph = manager.get_graph(graph_name)

    # 1. Freshness check
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

            if max_sync_age_days is not None:
                try:
                    synced_dt = datetime.datetime.fromisoformat(s_row[2].replace("Z", "+00:00"))
                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                    if (now_dt - synced_dt).days > max_sync_age_days:
                        return VerifiedReviewersResponse(
                            refused=True,
                            reason_code=RefusalCode.STALE_DATA,
                            reason=f"Graph data is stale. Last synchronized on {s_row[2]} (> {max_sync_age_days} days ago). Please re-sync repository.",
                            target=target,
                            target_type=target_type,
                            repo_name=repo_name,
                            sync_info=sync_info,
                        )
                except Exception as exc:
                    logger.debug("Could not parse sync date: %s", exc)
    except Exception as exc:
        logger.debug("Sync info check note: %s", exc)

    # 2. Run graph traversal
    base_response = reviewers_for(
        target_file=target_file,
        target_module=target_module,
        repo_name=repo_name,
        exclude_emails=exclude_emails,
        limit=limit,
        max_evidence=max_evidence,
        graph_name=graph_name,
        db_manager=manager,
    )

    # 3. Refusal before generation: No path found
    if base_response.total_candidates == 0:
        return VerifiedReviewersResponse(
            refused=True,
            reason_code=RefusalCode.NO_PATH,
            reason=f"No commit history found touching target {target_type} '{target}' in repository '{repo_name}'.",
            target=target,
            target_type=target_type,
            repo_name=repo_name,
            sync_info=base_response.sync_info or sync_info,
        )

    # 4. Build structured Evidence objects from graph facts
    evidence_list: List[Evidence] = []
    claims_list: List[Claim] = []

    for idx, cand in enumerate(base_response.candidates):
        # Query individual commit touch details for this candidate to construct granular evidence
        if target_type == "file":
            touch_q = """
            MATCH (p:Person {email: $email})-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File {path: $target})
            RETURN c.sha, c.ts, t.added, t.deleted
            ORDER BY c.ts DESC
            LIMIT $max_ev
            """
        else:
            touch_q = """
            MATCH (p:Person {email: $email})-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File)-[:IN_MODULE]->(m:Module {path: $target})
            RETURN c.sha, c.ts, sum(t.added) AS added, sum(t.deleted) AS deleted
            ORDER BY c.ts DESC
            LIMIT $max_ev
            """

        t_res = graph.query(touch_q, {"email": cand.person_email, "target": target, "max_ev": max_evidence})
        cand_ev_ids = []

        if t_res and t_res.result_set:
            for t_row in t_res.result_set:
                sha = str(t_row[0])
                ts = str(t_row[1]) if t_row[1] is not None else ""
                added = int(t_row[2]) if t_row[2] is not None else 0
                deleted = int(t_row[3]) if t_row[3] is not None else 0

                ev_id = f"ev_commit_{sha[:12]}"
                evidence_list.append(
                    Evidence(
                        id=ev_id,
                        kind="commit",
                        ref=sha,
                        properties={
                            "sha": sha,
                            "ts": ts,
                            "added": added,
                            "deleted": deleted,
                            "author": cand.person_name,
                            "email": cand.person_email,
                            "target": target,
                        },
                    )
                )
                cand_ev_ids.append(ev_id)

        # 5. Build structured claims backed by these evidence IDs
        claims_list.append(
            Claim(
                claim_id=f"claim_{idx+1}_commits",
                claim_type="commit_count",
                subject=cand.person_name,
                predicate="made_commits_touching",
                value=cand.commit_count,
                evidence_ids=cand_ev_ids,
            )
        )
        claims_list.append(
            Claim(
                claim_id=f"claim_{idx+1}_added",
                claim_type="lines_added",
                subject=cand.person_name,
                predicate="lines_added",
                value=cand.lines_added,
                evidence_ids=cand_ev_ids,
            )
        )
        claims_list.append(
            Claim(
                claim_id=f"claim_{idx+1}_deleted",
                claim_type="lines_deleted",
                subject=cand.person_name,
                predicate="lines_deleted",
                value=cand.lines_deleted,
                evidence_ids=cand_ev_ids,
            )
        )
        if cand.last_commit_at:
            claims_list.append(
                Claim(
                    claim_id=f"claim_{idx+1}_recency",
                    claim_type="recency",
                    subject=cand.person_name,
                    predicate="last_touched_at",
                    value=cand.last_commit_at,
                    evidence_ids=cand_ev_ids[:1],
                )
            )
        claims_list.append(
            Claim(
                claim_id=f"claim_{idx+1}_recommendation",
                claim_type="candidate_recommendation",
                subject=cand.person_name,
                predicate="recommended_reviewer_for",
                value=target,
                evidence_ids=cand_ev_ids,
            )
        )

    # 6. Verify claims using the deterministic verifier
    verifier = EvidenceVerifier()
    verification = verifier.verify(claims_list, evidence_list)

    return VerifiedReviewersResponse(
        refused=False,
        target=target,
        target_type=target_type,
        repo_name=repo_name,
        candidates=base_response.candidates,
        claims=claims_list,
        evidence=evidence_list,
        verification=verification,
        sync_info=base_response.sync_info or sync_info,
    )
