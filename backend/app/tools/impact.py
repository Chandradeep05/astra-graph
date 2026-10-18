from typing import List, Optional, Dict, Any
import logging

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.ingest.git_parser import get_module_path
from app.evidence.models import Claim, Evidence, RefusalCode
from app.evidence.verifier import EvidenceVerifier
from app.tools.models import (
    ChangeImpactResponse,
    ImpactedFile,
    RelatedPerson,
    ConnectedModule,
)

logger = logging.getLogger(__name__)


def change_impact(
    target_file: str,
    repo_name: str = "arth",
    limit: int = 10,
    max_evidence: int = 30,
    graph_name: Optional[str] = None,
    db_manager: Optional[FalkorDBManager] = None,
) -> ChangeImpactResponse:
    """
    Deterministic blast-radius and historical change impact reasoning.
    Uses parameterized Cypher to query (:File)-[:CO_CHANGES]-(:File) and commit history in FalkorDB.
    Enforces Anvaya's Trust Contract:
    - Bounded traversal (limit <= 50, max_evidence <= 100, depth 2)
    - Rejects arbitrary Cypher (purely parameterized queries)
    - Returns structured claims and evidence objects
    - Verifies all claims with EvidenceVerifier
    - Refuses with NO_PATH if file does not exist in graph
    - Returns empty impacted_files with factual signals if file has no co-change history
    """
    if not target_file or not isinstance(target_file, str) or not target_file.strip():
        raise ValueError("Must provide a valid non-empty 'target_file'")

    target = target_file.replace("\\", "/").strip("/")

    # Bounded parameter clamping
    clamped_limit = min(max(1, limit), 50)
    clamped_max_ev = min(max(1, max_evidence), 100)

    manager = db_manager or get_falkor_manager()

    # Health / Kill-switch check
    try:
        health = manager.check_health()
        if not health.get("connected"):
            return ChangeImpactResponse(
                refused=True,
                reason_code=RefusalCode.NO_PATH,
                reason=f"FalkorDB connection unavailable: {health.get('error', 'Connection refused')}",
                target_file=target,
                repo_name=repo_name,
            )
        graph = manager.get_graph(graph_name)
    except Exception as exc:
        return ChangeImpactResponse(
            refused=True,
            reason_code=RefusalCode.NO_PATH,
            reason=f"FalkorDB connection failed: {str(exc)}",
            target_file=target,
            repo_name=repo_name,
        )

    # 1. Fetch graph freshness / sync stamp
    sync_info: Optional[Dict[str, Any]] = None
    try:
        sync_query = """
        MATCH (r:Repository {name: $repo_name})-[:SNAPSHOT]->(s:Sync)
        RETURN s.id, s.head_sha, s.synced_at, s.commit_count
        ORDER BY s.synced_at DESC
        LIMIT 1
        """
        sync_res = graph.query(sync_query, {"repo_name": repo_name})
        if sync_res and sync_res.result_set:
            s_row = sync_res.result_set[0]
            sync_info = {
                "sync_id": s_row[0],
                "head_sha": s_row[1],
                "synced_at": s_row[2],
                "commit_count": s_row[3],
            }
    except Exception as exc:
        logger.debug("Could not read sync freshness: %s", exc)

    # 2. Verify target file exists in FalkorDB
    file_check_query = """
    MATCH (f:File {repo: $repo_name, path: $target_path})
    RETURN f.path LIMIT 1
    """
    try:
        file_res = graph.query(file_check_query, {"target_path": target, "repo_name": repo_name})
    except Exception as exc:
        return ChangeImpactResponse(
            refused=True,
            reason_code=RefusalCode.NO_PATH,
            reason=f"FalkorDB query failed: {str(exc)}",
            target_file=target,
            repo_name=repo_name,
            sync_info=sync_info,
        )

    if not file_res or not file_res.result_set:
        return ChangeImpactResponse(
            refused=True,
            reason_code=RefusalCode.NO_PATH,
            reason=f"Target file '{target}' not found in graph repository '{repo_name}'.",
            target_file=target,
            repo_name=repo_name,
            sync_info=sync_info,
        )

    # 3. Query historical co-changes via CO_CHANGES relationship (undirected match within repo)
    co_changes_query = """
    MATCH (f:File {repo: $repo_name, path: $target_path})-[cc:CO_CHANGES]-(other:File {repo: $repo_name})
    RETURN other.path AS other_path, cc.count AS count, cc.last_ts AS last_ts
    ORDER BY count DESC, last_ts DESC
    LIMIT $limit
    """
    co_res = graph.query(co_changes_query, {"target_path": target, "repo_name": repo_name, "limit": clamped_limit})

    # 4. Query related people who have historically touched the target file
    people_query = """
    MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHES]->(f:File {repo: $repo_name, path: $target_path})
    RETURN p.name AS person_name,
           count(DISTINCT c) AS commit_count,
           collect(DISTINCT c.sha)[0..10] AS evidence_shas
    ORDER BY commit_count DESC
    LIMIT 10
    """
    people_res = graph.query(people_query, {"target_path": target, "repo_name": repo_name})
    related_people: List[RelatedPerson] = []
    if people_res and people_res.result_set:
        for p_row in people_res.result_set:
            person_name = str(p_row[0]) if p_row[0] else ""
            c_cnt = int(p_row[1]) if p_row[1] else 0
            raw_shas = p_row[2] if isinstance(p_row[2], list) else []
            related_people.append(
                RelatedPerson(
                    person=person_name,
                    shared_commit_count=c_cnt,
                    evidence_shas=[str(s) for s in raw_shas],
                )
            )

    # Case: Known file with no co-change evidence
    if not co_res or not co_res.result_set:
        signals = [
            f"File '{target}' exists in repository '{repo_name}', but has no historical co-change relationships with other files."
        ]
        if related_people:
            signals.append(f"{len(related_people)} contributor(s) historically modified target file.")

        return ChangeImpactResponse(
            refused=False,
            target_file=target,
            repo_name=repo_name,
            impacted_files=[],
            related_people=related_people,
            connected_modules=[],
            signals=signals,
            claims=[],
            evidence=[],
            verification=None,
            sync_info=sync_info,
        )

    # 5. For each impacted file, fetch shared commit evidence
    impacted_files: List[ImpactedFile] = []
    evidence_list: List[Evidence] = []
    ev_map: Dict[str, Evidence] = {}
    claims: List[Claim] = []

    shared_commits_query = """
    MATCH (c:Commit)-[:TOUCHES]->(f:File {repo: $repo_name, path: $target_path}),
          (c)-[:TOUCHES]->(other:File {repo: $repo_name, path: $other_path})
    RETURN c.sha, c.ts
    ORDER BY c.ts DESC
    LIMIT $max_shas
    """

    for idx, row in enumerate(co_res.result_set):
        other_path = str(row[0])
        co_count = int(row[1]) if row[1] is not None else 0
        last_ts = str(row[2]) if row[2] is not None else ""

        # Fetch supporting commit SHAs
        sh_res = graph.query(
            shared_commits_query,
            {
                "target_path": target,
                "other_path": other_path,
                "repo_name": repo_name,
                "max_shas": clamped_max_ev,
            },
        )
        shared_shas: List[str] = []
        file_ev_ids: List[str] = []

        if sh_res and sh_res.result_set:
            for sh_row in sh_res.result_set:
                sha = str(sh_row[0])
                c_ts = str(sh_row[1]) if sh_row[1] else ""
                shared_shas.append(sha)

                ev_id = f"ev_commit_{sha[:10]}"
                if ev_id not in ev_map:
                    ev_obj = Evidence(
                        id=ev_id,
                        kind="commit",
                        ref=sha,
                        properties={"sha": sha, "ts": c_ts, "path": other_path},
                    )
                    evidence_list.append(ev_obj)
                    ev_map[ev_id] = ev_obj
                file_ev_ids.append(ev_id)

        impacted_files.append(
            ImpactedFile(
                path=other_path,
                co_change_count=co_count,
                last_co_change=last_ts,
                shared_commit_count=len(shared_shas),
                evidence_shas=shared_shas,
            )
        )

        # Build verifiable Claims
        if file_ev_ids:
            claim_count = Claim(
                claim_id=f"claim_co_change_{idx + 1}",
                claim_type="co_change_count",
                subject=other_path,
                predicate="co_changes_with_target",
                value=len(file_ev_ids),
                evidence_ids=file_ev_ids,
            )
            claims.append(claim_count)

            claim_recency = Claim(
                claim_id=f"claim_recency_{idx + 1}",
                claim_type="last_co_change",
                subject=other_path,
                predicate="last_co_changed_at",
                value=last_ts,
                evidence_ids=file_ev_ids,
            )
            claims.append(claim_recency)

    # 6. Aggregate Connected Modules
    module_agg: Dict[str, Dict[str, int]] = {}
    for imp in impacted_files:
        mod = get_module_path(imp.path)
        if mod not in module_agg:
            module_agg[mod] = {"file_count": 0, "co_change_count": 0}
        module_agg[mod]["file_count"] += 1
        module_agg[mod]["co_change_count"] += imp.co_change_count

    connected_modules = [
        ConnectedModule(
            module=mod,
            file_count=data["file_count"],
            co_change_count=data["co_change_count"],
        )
        for mod, data in sorted(
            module_agg.items(),
            key=lambda x: (x[1]["co_change_count"], x[1]["file_count"]),
            reverse=True,
        )
    ]

    # 7. Construct factual signals
    signals: List[str] = [
        f"Identified {len(impacted_files)} historically co-changing file(s) across {len(connected_modules)} module(s)."
    ]
    if impacted_files:
        top_file = impacted_files[0]
        signals.append(
            f"Strongest co-change link: '{top_file.path}' with {top_file.co_change_count} shared commit(s) (last co-change: {top_file.last_co_change})."
        )
    if related_people:
        signals.append(
            f"{len(related_people)} contributor(s) historically modified target file (primary author: {related_people[0].person})."
        )

    # 8. Verify claims using EvidenceVerifier
    verifier = EvidenceVerifier()
    verification = verifier.verify(claims, evidence_list)

    return ChangeImpactResponse(
        refused=False,
        target_file=target,
        repo_name=repo_name,
        impacted_files=impacted_files,
        related_people=related_people,
        connected_modules=connected_modules,
        signals=signals,
        claims=claims,
        evidence=evidence_list,
        verification=verification,
        sync_info=sync_info,
    )
