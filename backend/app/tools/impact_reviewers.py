from typing import List, Optional, Dict, Any, Set
import logging

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.evidence.models import Claim, Evidence, RefusalCode
from app.evidence.verifier import EvidenceVerifier
from app.memory.models import ExcludedCandidate
from app.tools.impact import change_impact
from app.tools.models import (
    ImpactReviewerCandidate,
    ImpactReviewersResponse,
)

logger = logging.getLogger(__name__)


def impact_reviewers(
    target_file: str,
    repo_name: str = "databases",
    limit: int = 5,
    max_evidence: int = 30,
    graph_name: Optional[str] = None,
    db_manager: Optional[FalkorDBManager] = None,
) -> ImpactReviewersResponse:
    """
    Deterministic synthesis of blast-radius intelligence (change_impact) and
    evidence-backed reviewer routing (reviewers_for) respecting persistent memory constraints.

    Workflow:
    target_file -> historical co-change files -> contributor authorship -> graph memory filtering -> verified ranking
    """
    if not target_file or not isinstance(target_file, str) or not target_file.strip():
        raise ValueError("Must provide a valid non-empty 'target_file'")

    target = target_file.replace("\\", "/").strip("/")
    clamped_limit = min(max(1, limit), 50)
    clamped_max_ev = min(max(1, max_evidence), 100)

    manager = db_manager or get_falkor_manager()

    # 1. Kill-switch check
    try:
        health = manager.check_health()
        if not health.get("connected"):
            return ImpactReviewersResponse(
                refused=True,
                reason_code=RefusalCode.NO_PATH,
                reason=f"FalkorDB connection unavailable: {health.get('error', 'Connection refused')}",
                target_file=target,
                repo_name=repo_name,
            )
        graph = manager.get_graph(graph_name)
    except Exception as exc:
        return ImpactReviewersResponse(
            refused=True,
            reason_code=RefusalCode.NO_PATH,
            reason=f"FalkorDB connection failed: {str(exc)}",
            target_file=target,
            repo_name=repo_name,
        )

    # 2. Run change_impact on target file
    impact_res = change_impact(
        target_file=target,
        repo_name=repo_name,
        limit=clamped_limit,
        max_evidence=clamped_max_ev,
        graph_name=graph_name,
        db_manager=manager,
    )

    if impact_res.refused:
        return ImpactReviewersResponse(
            refused=True,
            reason_code=impact_res.reason_code,
            reason=impact_res.reason,
            target_file=target,
            repo_name=repo_name,
            sync_info=impact_res.sync_info,
        )

    # 3. Gather all relevant paths (target file + top co-changing files)
    co_change_paths = [f.path for f in impact_res.impacted_files]
    all_paths = [target] + co_change_paths

    # 4. Query contributors who touched ANY of all_paths within this repository
    contrib_q = """
    MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File {repo: $repo_name})
    WHERE f.path IN $all_paths
    RETURN p.name AS person_name,
           p.email AS person_email,
           f.path AS file_path,
           count(DISTINCT c) AS c_count,
           collect(DISTINCT c.sha)[0..10] AS shas,
           max(c.ts) AS last_ts
    """
    res = graph.query(contrib_q, {"all_paths": all_paths, "repo_name": repo_name})

    # Aggregate contributions per person email
    person_data: Dict[str, Dict[str, Any]] = {}
    if res and res.result_set:
        for row in res.result_set:
            p_name = str(row[0]) if row[0] is not None else ""
            p_email = str(row[1]) if row[1] is not None else ""
            f_path = str(row[2]) if row[2] is not None else ""
            c_cnt = int(row[3]) if row[3] is not None else 0
            raw_shas = [str(s) for s in row[4]] if isinstance(row[4], list) else []
            last_ts = str(row[5]) if row[5] is not None else ""

            if p_email not in person_data:
                person_data[p_email] = {
                    "name": p_name,
                    "email": p_email,
                    "direct_commits": 0,
                    "co_change_commits": 0,
                    "relevant_files": set(),
                    "evidence_commits": [],
                    "last_commit_at": "",
                    "last_commit_sha": "",
                }

            entry = person_data[p_email]
            entry["relevant_files"].add(f_path)
            for s in raw_shas:
                if s not in entry["evidence_commits"]:
                    entry["evidence_commits"].append(s)

            if f_path == target:
                entry["direct_commits"] += c_cnt
            else:
                entry["co_change_commits"] += c_cnt

            if not entry["last_commit_at"] or last_ts > entry["last_commit_at"]:
                entry["last_commit_at"] = last_ts
                if raw_shas:
                    entry["last_commit_sha"] = raw_shas[0]

    # 5. Check active persistent memory constraints in FalkorDB (M7 integration)
    excluded_candidates: List[ExcludedCandidate] = []
    memory_evidence: List[Evidence] = []
    active_constraints: Dict[str, Dict[str, Any]] = {}

    try:
        mem_query = """
        MATCH (c:Constraint {active: true, type: 'reviewer_unavailable'})
        WHERE c.scope = $scope OR c.scope = $raw_scope
        RETURN c.id, c.subject, c.scope, c.source_action_id
        """
        mem_res = graph.query(
            mem_query,
            {"scope": f"repo:{repo_name}", "raw_scope": repo_name},
        )
        if mem_res and mem_res.result_set:
            for m_row in mem_res.result_set:
                c_id = str(m_row[0])
                c_subj = str(m_row[1])
                c_scope = str(m_row[2])
                c_action_id = str(m_row[3]) if m_row[3] else ""
                active_constraints[c_subj] = {
                    "id": c_id,
                    "subject": c_subj,
                    "scope": c_scope,
                    "source_action_id": c_action_id,
                }
    except Exception as exc:
        logger.debug("Memory constraints read note: %s", exc)

    # Filter out constrained candidates
    eligible_raw = []
    for p_email, data in person_data.items():
        matched_c = active_constraints.get(data["name"]) or active_constraints.get(p_email)
        if matched_c:
            ev_id = f"ev_constraint_{matched_c['id']}"
            ev_obj = Evidence(
                id=ev_id,
                kind="constraint",
                ref=matched_c["id"],
                properties={
                    "type": "reviewer_unavailable",
                    "subject": data["name"],
                    "scope": matched_c["scope"],
                    "source_action_id": matched_c["source_action_id"],
                },
            )
            memory_evidence.append(ev_obj)
            excluded_candidates.append(
                ExcludedCandidate(
                    person=data["name"],
                    reason="reviewer_unavailable",
                    evidence_ids=[ev_id],
                    constraint_id=matched_c["id"],
                )
            )
        else:
            eligible_raw.append(data)

    # 6. Check refusal if no candidates remain
    if not eligible_raw:
        if excluded_candidates:
            excluded_names = ", ".join(e.person for e in excluded_candidates)
            return ImpactReviewersResponse(
                refused=True,
                reason_code=RefusalCode.NO_PATH,
                reason=f"All candidate reviewers for '{target}' in '{repo_name}' are currently excluded by active graph constraints: {excluded_names}.",
                target_file=target,
                repo_name=repo_name,
                impacted_files_count=len(co_change_paths),
                impacted_files=impact_res.impacted_files,
                excluded_candidates=excluded_candidates,
                memory_evidence=memory_evidence,
                sync_info=impact_res.sync_info,
            )
        else:
            return ImpactReviewersResponse(
                refused=True,
                reason_code=RefusalCode.NO_PATH,
                reason=f"No contributor authorship history found for '{target}' or its co-changing files in repository '{repo_name}'.",
                target_file=target,
                repo_name=repo_name,
                impacted_files_count=len(co_change_paths),
                impacted_files=impact_res.impacted_files,
                sync_info=impact_res.sync_info,
            )

    # 7. Deterministic Ranking:
    # 1. Direct commits on target (primary signal)
    # 2. Total relevant commits across blast radius (secondary signal)
    # 3. Last commit recency
    eligible_sorted = sorted(
        eligible_raw,
        key=lambda x: (
            x["direct_commits"],
            x["direct_commits"] + x["co_change_commits"],
            x["last_commit_at"],
        ),
        reverse=True,
    )[:clamped_limit]

    # 8. Build structured candidates, claims, and evidence
    candidates: List[ImpactReviewerCandidate] = []
    evidence_list: List[Evidence] = []
    ev_map: Dict[str, Evidence] = {}
    claims: List[Claim] = []

    for idx, d in enumerate(eligible_sorted):
        total_rel = d["direct_commits"] + d["co_change_commits"]
        rel_files_list = sorted(list(d["relevant_files"]))

        # Query full commit details for candidate's relevant touches to ensure 100% evidence verification
        touch_detail_q = """
        MATCH (p:Person {email: $email})-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File {repo: $repo_name})
        WHERE f.path IN $rel_files
        RETURN c.sha, c.ts, f.path
        ORDER BY c.ts DESC
        """
        detail_res = graph.query(touch_detail_q, {"email": d["email"], "rel_files": rel_files_list, "repo_name": repo_name})

        cand_ev_ids: List[str] = []
        if detail_res and detail_res.result_set:
            for d_row in detail_res.result_set:
                sha = str(d_row[0])
                c_ts = str(d_row[1]) if d_row[1] else ""
                f_touched = str(d_row[2]) if d_row[2] else ""

                ev_id = f"ev_commit_{sha[:12]}"
                if ev_id not in ev_map:
                    ev_obj = Evidence(
                        id=ev_id,
                        kind="commit",
                        ref=sha,
                        properties={"sha": sha, "ts": c_ts, "path": f_touched, "author": d["name"]},
                    )
                    evidence_list.append(ev_obj)
                    ev_map[ev_id] = ev_obj

                if ev_id not in cand_ev_ids:
                    cand_ev_ids.append(ev_id)

        # Candidate signals
        signals_cand = [
            f"{d['direct_commits']} commit(s) touching '{target}' directly.",
            f"{d['co_change_commits']} commit(s) touching {len(rel_files_list) - (1 if target in rel_files_list else 0)} co-changing file(s).",
            f"Last active on {d['last_commit_at']}.",
        ]

        candidate_obj = ImpactReviewerCandidate(
            person_name=d["name"],
            person_email=d["email"],
            direct_commits=d["direct_commits"],
            co_change_commits=d["co_change_commits"],
            total_relevant_commits=total_rel,
            relevant_files=rel_files_list,
            evidence_commits=[ev_map[eid].ref for eid in cand_ev_ids[:clamped_max_ev]],
            signals=signals_cand,
            last_commit_at=d["last_commit_at"],
            last_commit_sha=d["last_commit_sha"],
        )
        candidates.append(candidate_obj)

        # Build Claims backed by Evidence
        if cand_ev_ids:
            claim_commits = Claim(
                claim_id=f"claim_{idx + 1}_commits",
                claim_type="commit_count",
                subject=d["name"],
                predicate="authored_relevant_commits",
                value=len(cand_ev_ids),
                evidence_ids=cand_ev_ids,
            )
            claims.append(claim_commits)

            if d["last_commit_at"]:
                claim_recency = Claim(
                    claim_id=f"claim_{idx + 1}_recency",
                    claim_type="recency",
                    subject=d["name"],
                    predicate="last_committed_at",
                    value=d["last_commit_at"],
                    evidence_ids=cand_ev_ids,
                )
                claims.append(claim_recency)

    # 9. Overall factual signals
    overall_signals = [
        f"Analyzed {len(co_change_paths)} historically co-changing file(s) for '{target}'.",
        f"Identified {len(candidates)} eligible candidate reviewer(s) backed by graph authorship evidence.",
    ]
    if candidates:
        top = candidates[0]
        overall_signals.append(
            f"Primary recommendation: {top.person_name} ({top.direct_commits} direct commit(s) on target, {top.co_change_commits} co-change commit(s) across {len(top.relevant_files)} file(s))."
        )
    if excluded_candidates:
        overall_signals.append(
            f"{len(excluded_candidates)} candidate(s) excluded by active persistent graph memory constraints."
        )

    # 10. Verify claims using EvidenceVerifier
    verifier = EvidenceVerifier()
    verification = verifier.verify(claims, evidence_list)

    if not verification.verified:
        return ImpactReviewersResponse(
            refused=True,
            reason_code=RefusalCode.LOW_EVIDENCE,
            reason=f"Evidence verification failed: {'; '.join(verification.rejection_reasons)}",
            target_file=target,
            repo_name=repo_name,
            impacted_files_count=len(co_change_paths),
            candidates=[],
            excluded_candidates=excluded_candidates,
            impacted_files=impact_res.impacted_files,
            signals=[f"Evidence verification failed for candidate claims: {'; '.join(verification.rejection_reasons)}"],
            claims=claims,
            evidence=evidence_list,
            memory_evidence=memory_evidence,
            verification=verification,
            sync_info=impact_res.sync_info,
        )

    return ImpactReviewersResponse(
        refused=False,
        target_file=target,
        repo_name=repo_name,
        impacted_files_count=len(co_change_paths),
        candidates=candidates,
        excluded_candidates=excluded_candidates,
        impacted_files=impact_res.impacted_files,
        signals=overall_signals,
        claims=claims,
        evidence=evidence_list,
        memory_evidence=memory_evidence,
        verification=verification,
        sync_info=impact_res.sync_info,
    )
