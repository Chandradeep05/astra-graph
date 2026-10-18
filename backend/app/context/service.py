from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.context.models import (
    ArchitectureContext,
    ConstraintContext,
    ContextPackage,
    ContributorContext,
    DependencyContext,
    FileContext,
    FreshnessContext,
    HistoricalContext,
    ImpactContext,
    ModuleContext,
    ReviewerContext,
    SubjectType,
)
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.evidence.models import Claim, Evidence, RefusalCode, VerificationResult
from app.evidence.verifier import EvidenceVerifier

logger = logging.getLogger(__name__)


class ContextPackageService:
    """
    Deterministic Graph Context Package Service.
    Generates bounded, machine-readable, evidence-backed repository and subject context
    packages exclusively from FalkorDB knowledge graph relationships.

    Invariant:
    - Never uses an ungrounded LLM to author graph facts.
    - Fails closed when FalkorDB is unreachable (kill switch).
    - Strictly bounds traversal depth, node count, and evidence records.
    - Verifies all factual claims with EvidenceVerifier.
    """

    def __init__(
        self,
        db_manager: Optional[FalkorDBManager] = None,
        graph_name: Optional[str] = None,
    ) -> None:
        self.db_manager = db_manager or get_falkor_manager()
        self.graph_name = graph_name
        self.verifier = EvidenceVerifier()

    def get_context_package(
        self,
        repository: str = "arth",
        subject: Optional[str] = None,
        subject_type: Optional[str] = None,
        depth: int = 3,
        max_files: int = 20,
        max_contributors: int = 10,
        max_evidence: int = 30,
    ) -> ContextPackage:
        """
        Main entrypoint: generates a bounded, structured context package.
        """
        # Clamp parameters to sensible, non-exploding bounds
        bounded_depth = min(max(1, depth), 5)
        bounded_max_files = min(max(1, max_files), 100)
        bounded_max_contrib = min(max(1, max_contributors), 50)
        bounded_max_evidence = min(max(1, max_evidence), 100)

        repo_name = (repository or "arth").strip()

        # 1. Kill-Switch / Graph Centrality Check
        try:
            health = self.db_manager.check_health()
            if not health.get("connected"):
                return self._refuse(
                    repository=repo_name,
                    subject=subject or repo_name,
                    subject_type=SubjectType.REPOSITORY,
                    reason_code=RefusalCode.NO_PATH,
                    reason=f"FalkorDB connection unavailable: {health.get('error', 'Connection refused')}",
                    limitations=[
                        "FalkorDB is offline; fail-closed trust contract forbids returning stale or unverified context.",
                        "No in-memory cache or speculative hallucination is permitted."
                    ],
                )
            graph = self.db_manager.get_graph(self.graph_name)
        except Exception as exc:
            return self._refuse(
                repository=repo_name,
                subject=subject or repo_name,
                subject_type=SubjectType.REPOSITORY,
                reason_code=RefusalCode.NO_PATH,
                reason=f"FalkorDB health check failed: {str(exc)}",
                limitations=["FalkorDB connection failure; safe fail-closed refusal enforced."],
            )

        # 2. Check Repository Existence
        repo_exists = self._check_repository_exists(graph, repo_name)
        if not repo_exists:
            return self._refuse(
                repository=repo_name,
                subject=subject or repo_name,
                subject_type=SubjectType.REPOSITORY,
                reason_code=RefusalCode.NO_PATH,
                reason=f"Repository '{repo_name}' not found in FalkorDB knowledge graph.",
                limitations=["Repository does not exist in ingested graph database."],
            )

        # 3. Read Graph Freshness & Snapshot Metadata
        freshness = self._get_freshness_context(graph, repo_name)

        # 4. Resolve Subject and SubjectType
        resolved_subject, resolved_type = self._resolve_subject(
            graph, repo_name, subject, subject_type
        )

        if resolved_type == SubjectType.FILE:
            return self._build_file_context(
                graph=graph,
                repo_name=repo_name,
                target_file=resolved_subject,
                freshness=freshness,
                depth=bounded_depth,
                max_files=bounded_max_files,
                max_contributors=bounded_max_contrib,
                max_evidence=bounded_max_evidence,
            )
        elif resolved_type == SubjectType.MODULE:
            return self._build_module_context(
                graph=graph,
                repo_name=repo_name,
                target_module=resolved_subject,
                freshness=freshness,
                depth=bounded_depth,
                max_files=bounded_max_files,
                max_contributors=bounded_max_contrib,
                max_evidence=bounded_max_evidence,
            )
        else:
            return self._build_repository_context(
                graph=graph,
                repo_name=repo_name,
                freshness=freshness,
                depth=bounded_depth,
                max_files=bounded_max_files,
                max_contributors=bounded_max_contrib,
                max_evidence=bounded_max_evidence,
            )

    # -------------------------------------------------------------------------
    # Helper: Check Repository
    # -------------------------------------------------------------------------

    def _check_repository_exists(self, graph: Any, repo_name: str) -> bool:
        """Verifies if repository node exists in graph."""
        res = graph.query(
            "MATCH (r:Repository) WHERE toLower(r.name) = toLower($name) RETURN r.name",
            {"name": repo_name},
        )
        return bool(res and res.result_set)

    # -------------------------------------------------------------------------
    # Helper: Freshness
    # -------------------------------------------------------------------------

    def _get_freshness_context(self, graph: Any, repo_name: str) -> FreshnessContext:
        """Retrieves snapshot metadata from latest Sync node linked to Repository."""
        query = """
        MATCH (r:Repository)-[:SNAPSHOT]->(s:Sync)
        WHERE toLower(r.name) = toLower($repo_name)
        RETURN s.id, s.head_sha, s.synced_at, s.commit_count
        ORDER BY s.synced_at DESC
        LIMIT 1
        """
        res = graph.query(query, {"repo_name": repo_name})
        now_iso = datetime.now(timezone.utc).isoformat()

        if res and res.result_set:
            row = res.result_set[0]
            sync_id = str(row[0]) if row[0] else ""
            head_sha = str(row[1]) if row[1] else ""
            synced_at = str(row[2]) if row[2] else ""
            commit_count = int(row[3]) if row[3] else 0

            # Check if synced_at is stale (> 30 days)
            is_stale = False
            if synced_at:
                try:
                    sync_dt = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
                    diff_days = (datetime.now(timezone.utc) - sync_dt).total_seconds() / 86400
                    if diff_days > 30:
                        is_stale = True
                except Exception:
                    pass

            return FreshnessContext(
                sync_id=sync_id,
                head_sha=head_sha,
                synced_at=synced_at,
                commit_count=commit_count,
                generated_at=now_iso,
                history_window="bounded",
                is_stale=is_stale,
            )

        return FreshnessContext(generated_at=now_iso, history_window="bounded", is_stale=False)

    # -------------------------------------------------------------------------
    # Helper: Resolve Subject
    # -------------------------------------------------------------------------

    def _resolve_subject(
        self,
        graph: Any,
        repo_name: str,
        subject: Optional[str],
        subject_type: Optional[str],
    ) -> Tuple[str, SubjectType]:
        """Resolves target subject and determines whether it is repository, file, or module."""
        if not subject or subject.strip().lower() in (repo_name.lower(), "", "all", "repo", "repository"):
            return repo_name, SubjectType.REPOSITORY

        subj = subject.replace("\\", "/").strip().strip("/")

        if subject_type:
            st = subject_type.lower()
            if st in ("file", "files"):
                return subj, SubjectType.FILE
            if st in ("module", "modules"):
                return subj, SubjectType.MODULE
            if st in ("repo", "repository"):
                return repo_name, SubjectType.REPOSITORY

        # Detect based on path pattern
        if "/" in subj or "." in subj:
            return subj, SubjectType.FILE

        # Check if subject matches an existing module in this repository
        mod_check = graph.query(
            "MATCH (m:Module {repo: $repo_name, path: $p}) RETURN m.path", {"p": subj, "repo_name": repo_name}
        )
        if mod_check and mod_check.result_set:
            return subj, SubjectType.MODULE

        # Default to file check
        return subj, SubjectType.FILE

    # -------------------------------------------------------------------------
    # Builder: Repository Context
    # -------------------------------------------------------------------------

    def _build_repository_context(
        self,
        graph: Any,
        repo_name: str,
        freshness: FreshnessContext,
        depth: int,
        max_files: int,
        max_contributors: int,
        max_evidence: int,
    ) -> ContextPackage:
        claims: List[Claim] = []
        evidence: List[Evidence] = []
        truncated = False
        truncation_reasons: List[str] = []

        # 1. Modules
        mod_q = """
        MATCH (r:Repository {name: $repo_name})-[:CONTAINS]->(m:Module)
        OPTIONAL MATCH (f:File {repo: $repo_name})-[:IN_MODULE]->(m)
        WITH m, count(DISTINCT f) AS f_cnt, collect(DISTINCT f.path)[0..5] AS samples
        ORDER BY f_cnt DESC
        RETURN m.path, f_cnt, samples
        """
        mod_res = graph.query(mod_q, {"repo_name": repo_name})
        modules_list: List[ModuleContext] = []

        if mod_res and mod_res.result_set:
            for row in mod_res.result_set:
                m_path = str(row[0]) if row[0] else "."
                f_cnt = int(row[1]) if row[1] else 0
                samples = [str(x) for x in row[2]] if isinstance(row[2], list) else []

                # Fetch top 2 contributors for module
                c_mod_q = """
                MATCH (m:Module {repo: $repo_name, path: $m_path})
                MATCH (f:File {repo: $repo_name})-[:IN_MODULE]->(m)
                MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHES]->(f)
                RETURN p.name, count(DISTINCT c) AS c_cnt
                ORDER BY c_cnt DESC
                LIMIT 2
                """
                c_mod_res = graph.query(c_mod_q, {"m_path": m_path, "repo_name": repo_name})
                top_m_contribs = (
                    [str(r[0]) for r in c_mod_res.result_set if r[0]]
                    if c_mod_res and c_mod_res.result_set
                    else []
                )

                modules_list.append(
                    ModuleContext(
                        name=m_path if m_path != "." else "root",
                        path=m_path,
                        file_count=f_cnt,
                        top_contributors=top_m_contribs,
                        sample_files=samples,
                    )
                )

        # 2. Important Files (Ranked by commit touches & churn)
        files_q = """
        MATCH (f:File {repo: $repo_name})-[:IN_MODULE]->(m:Module {repo: $repo_name})
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f)
        WITH f, m, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted,
             max(c.ts) AS last_ts, collect(DISTINCT p.name)[0..3] AS authors
        ORDER BY c_cnt DESC, added + deleted DESC
        RETURN f.path, m.path, c_cnt, added, deleted, last_ts, authors
        """
        files_res = graph.query(files_q, {"repo_name": repo_name})
        important_files: List[FileContext] = []

        if files_res and files_res.result_set:
            raw_file_count = len(files_res.result_set)
            if raw_file_count > max_files:
                truncated = True
                truncation_reasons.append(f"files ({raw_file_count} > {max_files})")

            for row in files_res.result_set[:max_files]:
                f_path = str(row[0]) if row[0] else ""
                f_mod = str(row[1]) if row[1] else ""
                c_cnt = int(row[2]) if row[2] else 0
                added = int(row[3]) if row[3] else 0
                deleted = int(row[4]) if row[4] else 0
                last_ts = str(row[5]) if row[5] else ""
                authors = [str(x) for x in row[6]] if isinstance(row[6], list) else []

                # Count co-changing files within repository
                cc_q = "MATCH (f:File {repo: $repo_name, path: $p})-[cc:CO_CHANGES]-(other:File {repo: $repo_name}) RETURN count(other)"
                cc_res = graph.query(cc_q, {"p": f_path, "repo_name": repo_name})
                co_change_count = int(cc_res.result_set[0][0]) if cc_res and cc_res.result_set else 0

                important_files.append(
                    FileContext(
                        path=f_path,
                        module=f_mod,
                        commit_count=c_cnt,
                        lines_added=added,
                        lines_deleted=deleted,
                        top_authors=authors,
                        co_change_count=co_change_count,
                        last_commit_at=last_ts,
                    )
                )

        # 3. Contributors within repository
        contrib_q = """
        MATCH (f:File {repo: $repo_name})
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f)
        WITH p, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted,
             collect(DISTINCT f.path)[0..10] AS files, max(c.ts) AS last_ts
        ORDER BY c_cnt DESC
        RETURN p.name, p.email, c_cnt, added, deleted, files, last_ts
        """
        contrib_res = graph.query(contrib_q, {"repo_name": repo_name})
        contributors: List[ContributorContext] = []

        # 4. Active Persistent Memory Constraints in FalkorDB
        constraints = self._get_active_constraints(graph, repo_name)
        constrained_names = {c.subject for c in constraints}

        if contrib_res and contrib_res.result_set:
            raw_contrib_count = len(contrib_res.result_set)
            if raw_contrib_count > max_contributors:
                truncated = True
                truncation_reasons.append(f"contributors ({raw_contrib_count} > {max_contributors})")

            for idx, row in enumerate(contrib_res.result_set[:max_contributors]):
                p_name = str(row[0]) if row[0] else ""
                p_email = str(row[1]) if row[1] else ""
                c_cnt = int(row[2]) if row[2] else 0
                added = int(row[3]) if row[3] else 0
                deleted = int(row[4]) if row[4] else 0
                files = [str(x) for x in row[5]] if isinstance(row[5], list) else []
                last_ts = str(row[6]) if row[6] else ""

                is_constrained = (p_name in constrained_names or p_email in constrained_names)

                contributors.append(
                    ContributorContext(
                        name=p_name,
                        email=p_email,
                        commit_count=c_cnt,
                        lines_added=added,
                        lines_deleted=deleted,
                        files_touched=files,
                        last_active=last_ts,
                        is_constrained=is_constrained,
                        constraint_reason="reviewer_unavailable" if is_constrained else None,
                    )
                )

                # Generate Claim & Evidence for top 3 contributors
                if idx < 3 and len(evidence) < max_evidence:
                    ev_id = f"ev_contrib_{idx + 1}_{p_email.replace('@', '_').replace('.', '_')}"
                    evidence.append(
                        Evidence(
                            id=ev_id,
                            kind="commit",
                            ref=last_ts or p_email,
                            properties={
                                "author": p_name,
                                "email": p_email,
                                "commits": c_cnt,
                                "added": added,
                                "deleted": deleted,
                                "ts": last_ts,
                            },
                        )
                    )
                    claims.append(
                        Claim(
                            claim_id=f"claim_contrib_{idx + 1}",
                            claim_type="historical_contributor",
                            subject=p_name,
                            predicate="authored_commits",
                            value=f"{c_cnt} commits",
                            evidence_ids=[ev_id],
                        )
                    )
                    if last_ts:
                        claims.append(
                            Claim(
                                claim_id=f"claim_contrib_recency_{idx + 1}",
                                claim_type="recency",
                                subject=p_name,
                                predicate="last_active",
                                value=last_ts,
                                evidence_ids=[ev_id],
                            )
                        )

        # 5. Frequent Co-Changes & Cross-Module Dependencies within repository
        deps_q_full = """
        MATCH (f1:File {repo: $repo_name})-[cc:CO_CHANGES]->(f2:File {repo: $repo_name})
        MATCH (f1)-[:IN_MODULE]->(m1:Module {repo: $repo_name})
        MATCH (f2)-[:IN_MODULE]->(m2:Module {repo: $repo_name})
        WHERE cc.count >= 2
        WITH DISTINCT f1, f2, m1, m2, cc
        ORDER BY cc.count DESC
        LIMIT 30
        RETURN f1.path, f2.path, m1.path, m2.path, cc.count, cc.last_ts
        """
        deps_res = graph.query(deps_q_full, {"repo_name": repo_name})
        dependencies: List[DependencyContext] = []
        cross_module_connections: List[Dict[str, Any]] = []
        frequent_co_changes: List[Dict[str, Any]] = []

        if deps_res and deps_res.result_set:
            for row in deps_res.result_set[:max_files]:
                f1 = str(row[0])
                f2 = str(row[1])
                m1 = str(row[2])
                m2 = str(row[3])
                cnt = int(row[4])
                last_ts = str(row[5])

                ev_ref = f"cc_{f1}_{f2}"
                dependencies.append(
                    DependencyContext(
                        source=f1,
                        target=f2,
                        relationship_type="CO_CHANGES",
                        strength=cnt,
                        evidence_ref=ev_ref,
                    )
                )

                frequent_co_changes.append(
                    {
                        "file1": f1,
                        "file2": f2,
                        "co_change_count": cnt,
                        "last_co_change": last_ts,
                    }
                )

                if m1 != m2 and len(cross_module_connections) < 10:
                    cross_module_connections.append(
                        {
                            "source_module": m1,
                            "target_module": m2,
                            "shared_files": [f1, f2],
                            "co_change_strength": cnt,
                        }
                    )

                # Attach top co-change claim
                if len(claims) < 5 and len(evidence) < max_evidence:
                    ev_id = f"ev_cochange_{len(claims) + 1}"
                    evidence.append(
                        Evidence(
                            id=ev_id,
                            kind="co_change",
                            ref=f"{f1}<->{f2}",
                            properties={"count": cnt, "last_ts": last_ts},
                        )
                    )
                    claims.append(
                        Claim(
                            claim_id=f"claim_cochange_{len(claims) + 1}",
                            claim_type="co_change",
                            subject=f1,
                            predicate="co_changes_with",
                            value=f2,
                            evidence_ids=[ev_id],
                        )
                    )

        # 6. Add Constraint Claims & Evidence
        for idx, c in enumerate(constraints):
            if len(evidence) < max_evidence:
                ev_id = f"ev_constraint_{c.id}"
                evidence.append(
                    Evidence(
                        id=ev_id,
                        kind="constraint",
                        ref=c.id,
                        properties={
                            "type": c.type,
                            "subject": c.subject,
                            "scope": c.scope,
                            "source_action_id": c.source_action_id or "",
                        },
                    )
                )
                claims.append(
                    Claim(
                        claim_id=f"claim_constraint_{idx + 1}",
                        claim_type="constraint",
                        subject=c.subject,
                        predicate="active_constraint",
                        value=c.scope,
                        evidence_ids=[ev_id],
                    )
                )

        # 7. Architecture Context
        mod_names = [m.name for m in modules_list]
        arch_summary = (
            f"Repository '{repo_name}' contains {len(modules_list)} primary modules: "
            f"{', '.join(mod_names)}. Structured according to Git commit file containment and co-change coupling."
        )
        architecture = ArchitectureContext(
            type="graph_derived_structure",
            modules=modules_list,
            cross_module_connections=cross_module_connections,
            summary=arch_summary,
            limitations=(
                "Derived strictly from Git directory structure and historical co-changes; "
                "runtime microservice topologies and dynamic execution paths are not modeled."
            ),
        )

        # 8. Historical Context
        top_churn = [
            {"path": f.path, "commits": f.commit_count, "lines_added": f.lines_added, "lines_deleted": f.lines_deleted}
            for f in important_files[:5]
        ]
        historical_context = HistoricalContext(
            total_commits=freshness.commit_count,
            first_commit_at="",
            last_commit_at=important_files[0].last_commit_at if important_files else "",
            top_churn_files=top_churn,
            frequent_co_changes=frequent_co_changes[:5],
        )

        # 9. Verification
        verification = self.verifier.verify(claims, evidence)

        # 10. Summary
        contrib_summary = ", ".join(f"{c.name} ({c.commit_count} commits)" for c in contributors[:3])
        summary = (
            f"Repository '{repo_name}' Context Package (snapshot {freshness.head_sha[:10] if freshness.head_sha else 'unknown'}, "
            f"{freshness.commit_count} commits across {len(modules_list)} modules). "
            f"Major modules: {', '.join(mod_names[:5])}. Top contributors: {contrib_summary}."
        )
        if constraints:
            summary += f" Active persistent memory constraints: {len(constraints)}."

        # 11. Limitations
        limitations = [
            "Derived exclusively from FalkorDB Git history and co-change relationships.",
            "Runtime architecture, process execution graphs, and dynamic import paths are not represented in the Git graph.",
            "Files with zero commits in the analyzed history window are not included.",
            "Co-changes reflect historical commit coupling, not AST-level import semantics.",
            "No private repository secrets or tokens were accessed.",
        ]

        truncation_reason = (
            f"Results bounded by limits: {', '.join(truncation_reasons)}" if truncated else None
        )

        return ContextPackage(
            repository=repo_name,
            subject=repo_name,
            subject_type=SubjectType.REPOSITORY,
            freshness=freshness,
            summary=summary,
            architecture=architecture,
            modules=modules_list,
            important_files=important_files,
            dependencies=dependencies,
            contributors=contributors,
            historical_context=historical_context,
            constraints=constraints,
            evidence=evidence,
            claims=claims,
            limitations=limitations,
            verification=verification,
            refusal=False,
            truncated=truncated,
            truncation_reason=truncation_reason,
            depth=depth,
        )

    # -------------------------------------------------------------------------
    # Builder: File Context
    # -------------------------------------------------------------------------

    def _build_file_context(
        self,
        graph: Any,
        repo_name: str,
        target_file: str,
        freshness: FreshnessContext,
        depth: int,
        max_files: int,
        max_contributors: int,
        max_evidence: int,
    ) -> ContextPackage:
        target = target_file.replace("\\", "/").strip("/")
        claims: List[Claim] = []
        evidence: List[Evidence] = []
        truncated = False
        truncation_reasons: List[str] = []

        # 1. Verify File Exists in Graph
        f_check_q = """
        MATCH (f:File {repo: $repo_name, path: $p})
        OPTIONAL MATCH (f)-[:IN_MODULE]->(m:Module {repo: $repo_name})
        RETURN f.path, m.path
        """
        f_res = graph.query(f_check_q, {"p": target, "repo_name": repo_name})
        if not f_res or not f_res.result_set:
            return self._refuse(
                repository=repo_name,
                subject=target,
                subject_type=SubjectType.FILE,
                reason_code=RefusalCode.NO_PATH,
                reason=f"File '{target}' not found in FalkorDB knowledge graph.",
                limitations=["Target file has no recorded commits in the knowledge graph."],
            )

        row_f = f_res.result_set[0]
        mod_path = str(row_f[1]) if row_f[1] else "root"

        # 2. File Authorship & Contributor Lineage within repository
        contrib_q = """
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File {repo: $repo_name, path: $p})
        WITH p, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted,
             collect(c.sha)[0..10] AS shas, max(c.ts) AS last_ts
        ORDER BY c_cnt DESC
        RETURN p.name, p.email, c_cnt, added, deleted, shas, last_ts
        """
        contrib_res = graph.query(contrib_q, {"p": target, "repo_name": repo_name})
        contributors: List[ContributorContext] = []

        # 3. Memory Constraints
        constraints = self._get_active_constraints(graph, repo_name)
        constrained_names = {c.subject for c in constraints}

        recommended_reviewers: List[Dict[str, Any]] = []
        excluded_reviewers: List[Dict[str, Any]] = []

        total_file_commits = 0
        last_commit_at = ""

        if contrib_res and contrib_res.result_set:
            raw_contrib_count = len(contrib_res.result_set)
            if raw_contrib_count > max_contributors:
                truncated = True
                truncation_reasons.append(f"contributors ({raw_contrib_count} > {max_contributors})")

            for idx, row in enumerate(contrib_res.result_set[:max_contributors]):
                p_name = str(row[0]) if row[0] else ""
                p_email = str(row[1]) if row[1] else ""
                c_cnt = int(row[2]) if row[2] else 0
                added = int(row[3]) if row[3] else 0
                deleted = int(row[4]) if row[4] else 0
                shas = [str(x) for x in row[5]] if isinstance(row[5], list) else []
                last_ts = str(row[6]) if row[6] else ""

                total_file_commits += c_cnt
                if not last_commit_at or last_ts > last_commit_at:
                    last_commit_at = last_ts

                is_constrained = (p_name in constrained_names or p_email in constrained_names)

                contributors.append(
                    ContributorContext(
                        name=p_name,
                        email=p_email,
                        commit_count=c_cnt,
                        lines_added=added,
                        lines_deleted=deleted,
                        files_touched=[target],
                        last_active=last_ts,
                        is_constrained=is_constrained,
                        constraint_reason="reviewer_unavailable" if is_constrained else None,
                    )
                )

                reviewer_record = {
                    "person": p_name,
                    "email": p_email,
                    "direct_commits": c_cnt,
                    "lines_added": added,
                    "lines_deleted": deleted,
                    "last_commit_at": last_ts,
                }

                if is_constrained:
                    matched_c = next((c for c in constraints if c.subject in (p_name, p_email)), None)
                    reviewer_record["reason"] = "reviewer_unavailable"
                    reviewer_record["constraint_id"] = matched_c.id if matched_c else ""
                    excluded_reviewers.append(reviewer_record)
                else:
                    recommended_reviewers.append(reviewer_record)

                # Grounding Claim & Evidence for top contributor
                if idx == 0 and len(evidence) < max_evidence:
                    ev_id = f"ev_target_auth_{p_email.replace('@', '_').replace('.', '_')}"
                    evidence.append(
                        Evidence(
                            id=ev_id,
                            kind="commit",
                            ref=shas[0] if shas else (last_ts or p_email),
                            properties={
                                "author": p_name,
                                "email": p_email,
                                "commits": c_cnt,
                                "target": target,
                                "ts": last_ts,
                            },
                        )
                    )
                    claims.append(
                        Claim(
                            claim_id="claim_file_author_1",
                            claim_type="historical_contributor",
                            subject=p_name,
                            predicate="authored_file_commits",
                            value=f"{c_cnt} commits",
                            evidence_ids=[ev_id],
                        )
                    )
                    if last_ts:
                        claims.append(
                            Claim(
                                claim_id="claim_file_author_recency_1",
                                claim_type="recency",
                                subject=p_name,
                                predicate="last_touched_at",
                                value=last_ts,
                                evidence_ids=[ev_id],
                            )
                        )

        # 4. Blast-Radius Co-Changes within repository
        cc_q = """
        MATCH (f:File {repo: $repo_name, path: $p})-[cc:CO_CHANGES]-(other:File {repo: $repo_name})
        OPTIONAL MATCH (other)-[:IN_MODULE]->(om:Module {repo: $repo_name})
        RETURN other.path, om.path, cc.count, cc.last_ts
        ORDER BY cc.count DESC
        """
        cc_res = graph.query(cc_q, {"p": target, "repo_name": repo_name})
        co_changing_files: List[Dict[str, Any]] = []
        related_modules: Set[str] = set()
        dependencies: List[DependencyContext] = []

        if cc_res and cc_res.result_set:
            raw_cc_count = len(cc_res.result_set)
            if raw_cc_count > max_files:
                truncated = True
                truncation_reasons.append(f"co-changing files ({raw_cc_count} > {max_files})")

            for idx, row in enumerate(cc_res.result_set[:max_files]):
                oth_path = str(row[0])
                oth_mod = str(row[1]) if row[1] else "root"
                cnt = int(row[2])
                last_ts = str(row[3]) if row[3] else ""

                if oth_mod:
                    related_modules.add(oth_mod)

                co_changing_files.append(
                    {
                        "file": oth_path,
                        "module": oth_mod,
                        "co_change_count": cnt,
                        "last_co_change": last_ts,
                    }
                )

                dependencies.append(
                    DependencyContext(
                        source=target,
                        target=oth_path,
                        relationship_type="CO_CHANGES",
                        strength=cnt,
                        evidence_ref=f"cc_{target}_{oth_path}",
                    )
                )

                if idx < 3 and len(evidence) < max_evidence:
                    ev_id = f"ev_cc_{idx + 1}"
                    evidence.append(
                        Evidence(
                            id=ev_id,
                            kind="co_change",
                            ref=f"{target}<->{oth_path}",
                            properties={"count": cnt, "last_ts": last_ts},
                        )
                    )
                    claims.append(
                        Claim(
                            claim_id=f"claim_cc_{idx + 1}",
                            claim_type="co_change",
                            subject=target,
                            predicate="frequently_changes_with",
                            value=oth_path,
                            evidence_ids=[ev_id],
                        )
                    )

        # 5. Attach Constraint Claims & Evidence
        for idx, c in enumerate(constraints):
            if c.subject in [p.name for p in contributors] or c.subject in [p.email for p in contributors]:
                if len(evidence) < max_evidence:
                    ev_id = f"ev_constraint_{c.id}"
                    evidence.append(
                        Evidence(
                            id=ev_id,
                            kind="constraint",
                            ref=c.id,
                            properties={"type": c.type, "subject": c.subject, "scope": c.scope},
                        )
                    )
                    claims.append(
                        Claim(
                            claim_id=f"claim_constraint_{idx + 1}",
                            claim_type="constraint",
                            subject=c.subject,
                            predicate="active_constraint",
                            value=c.scope,
                            evidence_ids=[ev_id],
                        )
                    )

        # 6. File & Architecture Context
        important_files = [
            FileContext(
                path=target,
                module=mod_path,
                commit_count=total_file_commits,
                lines_added=sum(c.lines_added for c in contributors),
                lines_deleted=sum(c.lines_deleted for c in contributors),
                top_authors=[c.name for c in contributors[:3]],
                co_change_count=len(co_changing_files),
                last_commit_at=last_commit_at,
            )
        ]

        module_context = [
            ModuleContext(
                name=mod_path,
                path=mod_path,
                file_count=1,
                top_contributors=[c.name for c in contributors[:2]],
                sample_files=[target],
            )
        ]

        architecture = ArchitectureContext(
            type="graph_derived_structure",
            modules=module_context,
            cross_module_connections=[],
            summary=f"File '{target}' resides in module '{mod_path}'. Connected via co-changes to {len(related_modules)} other modules.",
            limitations="Single-file context derived from commit touch lineage.",
        )

        impact_context = ImpactContext(
            target=target,
            impacted_files_count=len(co_changing_files),
            co_changing_files=co_changing_files,
            related_modules=sorted(list(related_modules)),
        )

        reviewer_context = ReviewerContext(
            target=target,
            recommended_reviewers=recommended_reviewers,
            excluded_reviewers=excluded_reviewers,
        )

        historical_context = HistoricalContext(
            total_commits=total_file_commits,
            first_commit_at="",
            last_commit_at=last_commit_at,
            top_churn_files=[{"path": target, "commits": total_file_commits}],
            frequent_co_changes=co_changing_files[:5],
        )

        # 7. Verification
        verification = self.verifier.verify(claims, evidence)

        # 8. Summary
        top_auth_name = contributors[0].name if contributors else "Unknown"
        top_auth_cnt = contributors[0].commit_count if contributors else 0
        rec_rev_name = recommended_reviewers[0]["person"] if recommended_reviewers else "None eligible"
        summary = (
            f"File '{target}' (module: '{mod_path}'). Authored primarily by {top_auth_name} ({top_auth_cnt} commits). "
            f"Blast radius: {len(co_changing_files)} co-changing files across modules: {', '.join(sorted(list(related_modules))) if related_modules else 'none'}. "
            f"Recommended reviewer: {rec_rev_name}."
        )
        if excluded_reviewers:
            summary += f" Excluded by constraint: {', '.join(r['person'] for r in excluded_reviewers)}."

        # 9. Limitations
        limitations = [
            "Git touch and co-change lineage only: runtime imports and dynamic method dispatches are not modeled.",
            "Historical blast radius reflects commit coupling probability.",
            "No source code was read or executed; reasoning is grounded purely in graph relationships.",
        ]

        truncation_reason = (
            f"Results bounded by limits: {', '.join(truncation_reasons)}" if truncated else None
        )

        return ContextPackage(
            repository=repo_name,
            subject=target,
            subject_type=SubjectType.FILE,
            freshness=freshness,
            summary=summary,
            architecture=architecture,
            modules=module_context,
            important_files=important_files,
            dependencies=dependencies,
            contributors=contributors,
            historical_context=historical_context,
            impact_context=impact_context,
            reviewer_context=reviewer_context,
            constraints=constraints,
            evidence=evidence,
            claims=claims,
            limitations=limitations,
            verification=verification,
            refusal=False,
            truncated=truncated,
            truncation_reason=truncation_reason,
            depth=depth,
        )

    # -------------------------------------------------------------------------
    # Builder: Module Context
    # -------------------------------------------------------------------------

    def _build_module_context(
        self,
        graph: Any,
        repo_name: str,
        target_module: str,
        freshness: FreshnessContext,
        depth: int,
        max_files: int,
        max_contributors: int,
        max_evidence: int,
    ) -> ContextPackage:
        m_check = graph.query(
            "MATCH (m:Module {repo: $repo_name, path: $p}) RETURN m.path",
            {"p": target_module, "repo_name": repo_name},
        )
        if not m_check or not m_check.result_set:
            return self._refuse(
                repository=repo_name,
                subject=target_module,
                subject_type=SubjectType.MODULE,
                reason_code=RefusalCode.NO_PATH,
                reason=f"Module '{target_module}' not found in FalkorDB knowledge graph.",
                limitations=["Target module has no recorded files in the knowledge graph."],
            )

        # Files in module
        files_q = """
        MATCH (f:File {repo: $repo_name})-[:IN_MODULE]->(m:Module {repo: $repo_name, path: $mod})
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f)
        WITH f, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted, max(c.ts) AS last_ts
        ORDER BY c_cnt DESC
        LIMIT $max_files
        RETURN f.path, c_cnt, added, deleted, last_ts
        """
        files_res = graph.query(files_q, {"repo_name": repo_name, "mod": target_module, "max_files": max_files})
        files: List[FileContext] = []
        if files_res and files_res.result_set:
            for row in files_res.result_set:
                files.append(
                    FileContext(
                        path=str(row[0]),
                        module=target_module,
                        commit_count=int(row[1]),
                        lines_added=int(row[2]),
                        lines_deleted=int(row[3]),
                        last_commit_at=str(row[4]),
                    )
                )

        # Contributors to module
        contrib_q = """
        MATCH (f:File {repo: $repo_name})-[:IN_MODULE]->(m:Module {repo: $repo_name, path: $mod})
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f)
        WITH p, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted, max(c.ts) AS last_ts
        ORDER BY c_cnt DESC
        LIMIT $max_contrib
        RETURN p.name, p.email, c_cnt, added, deleted, last_ts
        """
        contrib_res = graph.query(contrib_q, {"repo_name": repo_name, "mod": target_module, "max_contrib": max_contributors})
        contributors: List[ContributorContext] = []
        constraints = self._get_active_constraints(graph, repo_name)
        constrained_names = {c.subject for c in constraints}

        if contrib_res and contrib_res.result_set:
            for row in contrib_res.result_set:
                p_name = str(row[0])
                p_email = str(row[1])
                c_cnt = int(row[2])
                added = int(row[3])
                deleted = int(row[4])
                last_ts = str(row[5])
                is_constrained = (p_name in constrained_names or p_email in constrained_names)
                contributors.append(
                    ContributorContext(
                        name=p_name,
                        email=p_email,
                        commit_count=c_cnt,
                        lines_added=added,
                        lines_deleted=deleted,
                        last_active=last_ts,
                        is_constrained=is_constrained,
                        constraint_reason="reviewer_unavailable" if is_constrained else None,
                    )
                )

        module_context = [
            ModuleContext(
                name=target_module,
                path=target_module,
                file_count=len(files),
                top_contributors=[c.name for c in contributors[:3]],
                sample_files=[f.path for f in files[:5]],
            )
        ]

        architecture = ArchitectureContext(
            type="graph_derived_structure",
            modules=module_context,
            summary=f"Module '{target_module}' contains {len(files)} tracked files.",
        )

        historical = HistoricalContext(
            total_commits=sum(f.commit_count for f in files),
            top_churn_files=[{"path": f.path, "commits": f.commit_count} for f in files[:5]],
        )

        evidence: List[Evidence] = []
        claims: List[Claim] = []
        for idx, c in enumerate(contributors[:3]):
            if len(evidence) < max_evidence:
                ev_id = f"ev_mod_contrib_{idx + 1}_{c.email.replace('@', '_').replace('.', '_')}"
                evidence.append(
                    Evidence(
                        id=ev_id,
                        kind="commit",
                        ref=c.last_active or c.email,
                        properties={
                            "author": c.name,
                            "email": c.email,
                            "commits": c.commit_count,
                            "module": target_module,
                            "ts": c.last_active,
                        },
                    )
                )
                claims.append(
                    Claim(
                        claim_id=f"claim_mod_contrib_{idx + 1}",
                        claim_type="historical_contributor",
                        subject=c.name,
                        predicate="contributor_to_module",
                        value=target_module,
                        evidence_ids=[ev_id],
                    )
                )
                if c.last_active:
                    claims.append(
                        Claim(
                            claim_id=f"claim_mod_recency_{idx + 1}",
                            claim_type="recency",
                            subject=c.name,
                            predicate="last_active_in_module",
                            value=c.last_active,
                            evidence_ids=[ev_id],
                        )
                    )

        verification = self.verifier.verify(claims, evidence)
        summary = f"Module '{target_module}' context package ({len(files)} files, {len(contributors)} contributors)."

        return ContextPackage(
            repository=repo_name,
            subject=target_module,
            subject_type=SubjectType.MODULE,
            freshness=freshness,
            summary=summary,
            architecture=architecture,
            modules=module_context,
            important_files=files,
            contributors=contributors,
            historical_context=historical,
            constraints=constraints,
            evidence=evidence,
            claims=claims,
            verification=verification,
            limitations=["Module context derived from file-containment hierarchy in Git."],
            depth=depth,
        )

    # -------------------------------------------------------------------------
    # Helper: Active Constraints
    # -------------------------------------------------------------------------

    def _get_active_constraints(self, graph: Any, repo_name: str) -> List[ConstraintContext]:
        """Reads active persistent memory constraints from FalkorDB."""
        query = """
        MATCH (c:Constraint {active: true})
        WHERE c.scope = $scope OR c.scope = $raw_scope
        RETURN c.id, c.type, c.subject, c.scope, c.source_action_id
        """
        res = graph.query(query, {"scope": f"repo:{repo_name}", "raw_scope": repo_name})
        constraints: List[ConstraintContext] = []
        if res and res.result_set:
            for row in res.result_set:
                constraints.append(
                    ConstraintContext(
                        id=str(row[0]),
                        type=str(row[1]),
                        subject=str(row[2]),
                        scope=str(row[3]),
                        active=True,
                        source_action_id=str(row[4]) if row[4] else None,
                    )
                )
        return constraints

    # -------------------------------------------------------------------------
    # Helper: Refusal Package
    # -------------------------------------------------------------------------

    def _refuse(
        self,
        repository: str,
        subject: str,
        subject_type: SubjectType,
        reason_code: RefusalCode,
        reason: str,
        limitations: Optional[List[str]] = None,
    ) -> ContextPackage:
        now_iso = datetime.now(timezone.utc).isoformat()
        return ContextPackage(
            repository=repository,
            subject=subject,
            subject_type=subject_type,
            generated_at=now_iso,
            freshness=FreshnessContext(generated_at=now_iso, is_stale=False),
            summary=f"[Refusal: {reason_code.value}] {reason}",
            architecture=ArchitectureContext(
                type="graph_derived_structure",
                summary="Unavailable due to refusal.",
            ),
            historical_context=HistoricalContext(),
            refusal=True,
            reason_code=reason_code,
            reason=reason,
            limitations=limitations or [reason],
            verification=VerificationResult(verified=False),
        )


_context_service: Optional[ContextPackageService] = None


def get_context_service() -> ContextPackageService:
    """Returns singleton ContextPackageService instance."""
    global _context_service
    if _context_service is None:
        _context_service = ContextPackageService()
    return _context_service
