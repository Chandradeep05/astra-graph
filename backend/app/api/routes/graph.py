from typing import Any, Dict, List, Optional
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db.falkor import FalkorDBManager, get_falkor_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Graph"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class GraphNode(BaseModel):
    id: str
    label: str
    type: str  # Repository, Module, File, Commit, Person, Constraint, Action, Outcome
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str  # IN_MODULE, TOUCHES, AUTHORED, CO_CHANGES, RESULTED_IN, APPLIES_TO, SNAPSHOT
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    repository: str
    subject: Optional[str] = None
    nodes: List[GraphNode] = Field(default_factory=list)
    relationships: List[GraphEdge] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    truncated: bool = False
    freshness: Dict[str, Any] = Field(default_factory=dict)


class EvidencePathStep(BaseModel):
    step_index: int
    source_type: str
    source_label: str
    relationship_type: str
    target_type: str
    target_label: str
    description: str
    evidence_properties: Dict[str, Any] = Field(default_factory=dict)


class EvidencePathResponse(BaseModel):
    target_file: str
    candidate_reviewer: str
    repository: str
    path_found: bool
    path_type: str  # "direct", "co_change", "none"
    steps: List[EvidencePathStep] = Field(default_factory=list)
    nodes: List[GraphNode] = Field(default_factory=list)
    relationships: List[GraphEdge] = Field(default_factory=list)
    is_constrained: bool = False
    constraint_info: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Route: GET /graph
# ---------------------------------------------------------------------------

@router.get("/graph", response_model=GraphResponse)
def get_graph_data(
    repo: str = Query("databases", description="Repository name ('databases' or 'arth')"),
    subject: Optional[str] = Query(None, description="Optional focus subject (file or module path)"),
    depth: int = Query(2, ge=1, le=4, description="Traversal depth"),
    max_nodes: int = Query(80, ge=10, le=100, description="Max nodes to return"),
    max_edges: int = Query(150, ge=10, le=200, description="Max edges to return"),
    include_memory: bool = Query(True, description="Include persistent constraints and actions"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
) -> GraphResponse:
    """
    Returns a bounded, query-parameterized subgraph from FalkorDB for visual exploration.
    Strictly enforces node and edge limits to guarantee high rendering performance.
    Fails closed if FalkorDB is unreachable.
    """
    health = db_manager.check_health()
    if not health.get("connected"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is offline; graph exploration is unavailable.",
        )

    repo_name = repo.strip().lower()
    graph = db_manager.get_graph()

    # Check repository existence in graph
    repo_exists_res = graph.query(
        "MATCH (r:Repository) WHERE toLower(r.name) = toLower($name) RETURN r.name",
        {"name": repo_name},
    )
    if not (repo_exists_res and repo_exists_res.result_set):
        return GraphResponse(
            repository=repo_name,
            subject=subject,
            nodes=[],
            relationships=[],
            node_count=0,
            edge_count=0,
            truncated=False,
            freshness={},
        )

    nodes_map: Dict[str, GraphNode] = {}
    edges_map: Dict[str, GraphEdge] = {}
    truncated = False

    # 1. Freshness metadata
    fresh_q = """
    MATCH (r:Repository)-[:SNAPSHOT]->(s:Sync)
    WHERE toLower(r.name) = toLower($repo)
    RETURN s.id, s.head_sha, s.synced_at, s.commit_count
    ORDER BY s.synced_at DESC LIMIT 1
    """
    fresh_res = graph.query(fresh_q, {"repo": repo_name})
    freshness: Dict[str, Any] = {}
    if fresh_res and fresh_res.result_set:
        row = fresh_res.result_set[0]
        freshness = {
            "sync_id": str(row[0]) if row[0] else "",
            "head_sha": str(row[1]) if row[1] else "",
            "synced_at": str(row[2]) if row[2] else "",
            "commit_count": int(row[3]) if row[3] else 0,
        }

    bounded_max_nodes = max_nodes if isinstance(max_nodes, int) else 80
    bounded_max_edges = max_edges if isinstance(max_edges, int) else 150

    # Helper to add node
    def add_node(nid: str, label: str, ntype: str, props: Dict[str, Any]) -> None:
        nonlocal truncated
        if nid not in nodes_map:
            if len(nodes_map) >= bounded_max_nodes:
                truncated = True
                return
            nodes_map[nid] = GraphNode(id=nid, label=label, type=ntype, properties=props)

    # Helper to add edge
    def add_edge(eid: str, src: str, tgt: str, rel_type: str, props: Dict[str, Any]) -> None:
        nonlocal truncated
        if eid not in edges_map:
            if len(edges_map) >= bounded_max_edges:
                truncated = True
                return
            if src in nodes_map and tgt in nodes_map:
                edges_map[eid] = GraphEdge(
                    id=eid, source=src, target=tgt, type=rel_type, properties=props
                )

    # Add Repository node
    repo_node_id = f"repo:{repo_name}"
    add_node(repo_node_id, repo_name, "Repository", {"name": repo_name, **freshness})

    # Target subject focus
    target_subject = subject.strip() if (isinstance(subject, str) and subject.strip()) else None

    if target_subject and ("/" in target_subject or "." in target_subject):
        # -------------------------------------------------------------
        # Subject File Focused Exploration
        # -------------------------------------------------------------
        f_res = graph.query(
            "MATCH (f:File {repo: $repo, path: $p}) OPTIONAL MATCH (f)-[:IN_MODULE]->(m:Module {repo: $repo}) RETURN f.path, m.path",
            {"repo": repo_name, "p": target_subject},
        )
        if f_res and f_res.result_set:
            f_path = str(f_res.result_set[0][0])
            m_path = str(f_res.result_set[0][1]) if f_res.result_set[0][1] else "root"
            f_id = f"file:{f_path}"
            m_id = f"module:{m_path}"

            add_node(f_id, f_path.split("/")[-1], "File", {"path": f_path, "module": m_path})
            add_node(m_id, m_path, "Module", {"name": m_path, "path": m_path})
            add_edge(f"edge_{f_id}_{m_id}", f_id, m_id, "IN_MODULE", {})
            add_edge(f"edge_{m_id}_{repo_node_id}", m_id, repo_node_id, "CONTAINS", {})

            # 2. Co-changes around subject file
            cc_q = """
            MATCH (f:File {repo: $repo, path: $p})-[cc:CO_CHANGES]-(other:File {repo: $repo})
            OPTIONAL MATCH (other)-[:IN_MODULE]->(om:Module {repo: $repo})
            RETURN other.path, om.path, cc.count, cc.last_ts
            ORDER BY cc.count DESC
            LIMIT 15
            """
            cc_res = graph.query(cc_q, {"repo": repo_name, "p": target_subject})
            if cc_res and cc_res.result_set:
                for row in cc_res.result_set:
                    oth_path = str(row[0])
                    oth_mod = str(row[1]) if row[1] else "root"
                    cnt = int(row[2])
                    last_ts = str(row[3]) if row[3] else ""

                    oth_id = f"file:{oth_path}"
                    add_node(oth_id, oth_path.split("/")[-1], "File", {"path": oth_path, "module": oth_mod})
                    add_edge(
                        f"cc_{min(f_path, oth_path)}_{max(f_path, oth_path)}",
                        f_id,
                        oth_id,
                        "CO_CHANGES",
                        {"count": cnt, "last_ts": last_ts},
                    )

            # 3. Direct Authors and Commits on subject file
            auth_q = """
            MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f:File {repo: $repo, path: $p})
            RETURN p.name, p.email, c.sha, c.committed_at, t.lines_added, t.lines_deleted
            ORDER BY c.committed_at DESC
            LIMIT 10
            """
            auth_res = graph.query(auth_q, {"repo": repo_name, "p": target_subject})
            if auth_res and auth_res.result_set:
                for row in auth_res.result_set:
                    p_name = str(row[0])
                    p_email = str(row[1])
                    sha = str(row[2])
                    ts = str(row[3]) if row[3] else ""
                    added = int(row[4]) if row[4] else 0
                    deleted = int(row[5]) if row[5] else 0

                    p_id = f"person:{p_email}"
                    c_id = f"commit:{sha[:10]}"

                    add_node(p_id, p_name, "Person", {"name": p_name, "email": p_email})
                    add_node(c_id, sha[:8], "Commit", {"sha": sha, "date": ts})

                    add_edge(f"auth_{p_id}_{c_id}", p_id, c_id, "AUTHORED", {})
                    add_edge(f"touch_{c_id}_{f_id}", c_id, f_id, "TOUCHES", {"added": added, "deleted": deleted})

    else:
        # -------------------------------------------------------------
        # Repository-Level Graph Overview
        # -------------------------------------------------------------
        # Modules
        mod_q = """
        MATCH (r:Repository {name: $repo})-[:CONTAINS]->(m:Module)
        OPTIONAL MATCH (f:File {repo: $repo})-[:IN_MODULE]->(m)
        RETURN DISTINCT m.path, count(DISTINCT f) AS f_cnt
        ORDER BY f_cnt DESC
        LIMIT 8
        """
        mod_res = graph.query(mod_q, {"repo": repo_name})
        if mod_res and mod_res.result_set:
            for row in mod_res.result_set:
                m_path = str(row[0])
                f_cnt = int(row[1])
                m_id = f"module:{m_path}"
                add_node(m_id, m_path, "Module", {"name": m_path, "files": f_cnt})
                add_edge(f"mod_{repo_node_id}_{m_id}", repo_node_id, m_id, "CONTAINS", {})

        # Top files
        files_q = """
        MATCH (f:File {repo: $repo})-[:IN_MODULE]->(m:Module {repo: $repo})
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHES]->(f)
        WITH f, m, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted
        ORDER BY c_cnt DESC
        LIMIT 20
        RETURN f.path, m.path, c_cnt, added, deleted
        """
        files_res = graph.query(files_q, {"repo": repo_name})
        top_file_ids = set()
        if files_res and files_res.result_set:
            for row in files_res.result_set:
                f_path = str(row[0])
                m_path = str(row[1])
                c_cnt = int(row[2])
                added = int(row[3])
                deleted = int(row[4])

                f_id = f"file:{f_path}"
                m_id = f"module:{m_path}"
                top_file_ids.add(f_path)

                add_node(f_id, f_path.split("/")[-1], "File", {
                    "path": f_path, "module": m_path, "commits": c_cnt, "added": added, "deleted": deleted
                })
                if m_id in nodes_map:
                    add_edge(f"in_mod_{f_id}_{m_id}", f_id, m_id, "IN_MODULE", {})

        # Top co-changes among files
        if top_file_ids:
            cc_q = """
            MATCH (f1:File {repo: $repo})-[cc:CO_CHANGES]->(f2:File {repo: $repo})
            WHERE f1.path IN $paths AND f2.path IN $paths AND cc.count >= 2
            RETURN f1.path, f2.path, cc.count, cc.last_ts
            ORDER BY cc.count DESC
            LIMIT 25
            """
            cc_res = graph.query(cc_q, {"repo": repo_name, "paths": list(top_file_ids)})
            if cc_res and cc_res.result_set:
                for row in cc_res.result_set:
                    f1 = str(row[0])
                    f2 = str(row[1])
                    cnt = int(row[2])
                    last_ts = str(row[3]) if row[3] else ""

                    f1_id = f"file:{f1}"
                    f2_id = f"file:{f2}"
                    add_edge(f"cc_{f1}_{f2}", f1_id, f2_id, "CO_CHANGES", {"count": cnt, "last_ts": last_ts})

        # Top contributors
        contrib_q = """
        MATCH (f:File {repo: $repo})<-[t:TOUCHES]-(c:Commit)<-[:AUTHORED]-(p:Person)
        WITH p, count(DISTINCT c) AS c_cnt, sum(t.added) AS added, sum(t.deleted) AS deleted
        ORDER BY c_cnt DESC
        LIMIT 6
        RETURN p.name, p.email, c_cnt, added, deleted
        """
        contrib_res = graph.query(contrib_q, {"repo": repo_name})
        if contrib_res and contrib_res.result_set:
            for row in contrib_res.result_set:
                p_name = str(row[0])
                p_email = str(row[1])
                c_cnt = int(row[2])
                added = int(row[3])
                deleted = int(row[4])

                p_id = f"person:{p_email}"
                add_node(p_id, p_name, "Person", {
                    "name": p_name, "email": p_email, "commits": c_cnt, "added": added, "deleted": deleted
                })
                add_edge(f"contrib_{repo_node_id}_{p_id}", repo_node_id, p_id, "CONTRIBUTOR", {"commits": c_cnt})

    # 4. Optional: Persistent Memory Constraints and Actions
    if include_memory:
        # Constraints
        const_q = """
        MATCH (c:Constraint {active: true})
        WHERE c.scope = $scope OR c.scope = $raw_scope
        RETURN c.id, c.type, c.subject, c.scope, c.source_action_id
        LIMIT 5
        """
        const_res = graph.query(const_q, {"scope": f"repo:{repo_name}", "raw_scope": repo_name})
        if const_res and const_res.result_set:
            for row in const_res.result_set:
                cid = str(row[0])
                ctype = str(row[1])
                subj = str(row[2])
                scope = str(row[3])

                c_node_id = f"constraint:{cid}"
                add_node(c_node_id, f"Constraint: {subj}", "Constraint", {
                    "id": cid, "type": ctype, "subject": subj, "scope": scope, "active": True
                })

                # Link constraint to person if person node exists
                p_matches = [n for n in nodes_map.values() if n.type == "Person" and (n.properties.get("name") == subj or n.properties.get("email") == subj)]
                for p_match in p_matches:
                    add_edge(f"const_link_{c_node_id}_{p_match.id}", c_node_id, p_match.id, "APPLIES_TO", {"type": ctype})

        # Recent Actions and Outcomes
        act_q = """
        MATCH (a:Action)
        WHERE a.repo = $repo
        OPTIONAL MATCH (a)-[:RESULTED_IN]->(o:Outcome)
        RETURN a.id, a.type, a.status, a.reviewer, a.target, o.id, o.status, o.success
        ORDER BY a.created_at DESC
        LIMIT 4
        """
        act_res = graph.query(act_q, {"repo": repo_name})
        if act_res and act_res.result_set:
            for row in act_res.result_set:
                aid = str(row[0])
                atype = str(row[1])
                astatus = str(row[2])
                rev = str(row[3])
                tgt = str(row[4])
                oid = str(row[5]) if row[5] else None
                osuccess = bool(row[7]) if row[7] is not None else False

                a_node_id = f"action:{aid}"
                add_node(a_node_id, f"Action: {astatus.upper()}", "Action", {
                    "id": aid, "type": atype, "status": astatus, "reviewer": rev, "target": tgt
                })

                if oid:
                    o_node_id = f"outcome:{oid}"
                    add_node(o_node_id, f"Outcome: {'SUCCESS' if osuccess else 'FAIL'}", "Outcome", {
                        "id": oid, "success": osuccess
                    })
                    add_edge(f"act_out_{a_node_id}_{o_node_id}", a_node_id, o_node_id, "RESULTED_IN", {})

    return GraphResponse(
        repository=repo_name,
        subject=target_subject,
        nodes=list(nodes_map.values()),
        relationships=list(edges_map.values()),
        node_count=len(nodes_map),
        edge_count=len(edges_map),
        truncated=truncated,
        freshness=freshness,
    )


# ---------------------------------------------------------------------------
# Route: GET /graph/evidence-path
# ---------------------------------------------------------------------------

@router.get("/graph/evidence-path", response_model=EvidencePathResponse)
def get_evidence_path(
    target_file: str = Query(..., description="Target repository file"),
    reviewer_name: str = Query(..., description="Candidate reviewer person name"),
    repo: str = Query("databases", description="Repository name"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
) -> EvidencePathResponse:
    """
    Traces and visualizes the exact graph lineage hops connecting target_file to candidate_reviewer:
    1. Direct commit touch path: File <-[:TOUCHES]- Commit <-[:AUTHORED]- Person
    2. Co-change blast-radius path: File -[:CO_CHANGES]-> CoChangedFile <-[:TOUCHES]- Commit <-[:AUTHORED]- Person
    3. Persistent constraint state (if candidate is currently excluded)
    """
    health = db_manager.check_health()
    if not health.get("connected"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is offline; cannot trace evidence path.",
        )

    graph = db_manager.get_graph()
    steps: List[EvidencePathStep] = []
    nodes: Dict[str, GraphNode] = {}
    edges: Dict[str, GraphEdge] = {}

    target_id = f"file:{target_file}"
    nodes[target_id] = GraphNode(id=target_id, label=target_file.split("/")[-1], type="File", properties={"path": target_file})

    rev_id = f"person:{reviewer_name}"
    nodes[rev_id] = GraphNode(id=rev_id, label=reviewer_name, type="Person", properties={"name": reviewer_name})

    # Check constraint on reviewer
    const_q = """
    MATCH (c:Constraint {active: true, subject: $name})
    WHERE c.scope = $scope OR c.scope = $raw_scope
    RETURN c.id, c.type, c.scope, c.source_action_id
    LIMIT 1
    """
    const_res = graph.query(const_q, {"name": reviewer_name, "scope": f"repo:{repo}", "raw_scope": repo})
    is_constrained = bool(const_res and const_res.result_set)
    constraint_info = None
    if is_constrained:
        c_row = const_res.result_set[0]
        constraint_info = {
            "id": str(c_row[0]),
            "type": str(c_row[1]),
            "scope": str(c_row[2]),
            "source_action_id": str(c_row[3]) if c_row[3] else None,
        }
        c_node_id = f"constraint:{c_row[0]}"
        nodes[c_node_id] = GraphNode(id=c_node_id, label=f"Constraint: {c_row[1]}", type="Constraint", properties=constraint_info)
        edges[f"edge_c_{c_node_id}_{rev_id}"] = GraphEdge(id=f"edge_c_{c_node_id}_{rev_id}", source=c_node_id, target=rev_id, type="APPLIES_TO", properties={})

    # 1. Check direct commit path
    dir_q = """
    MATCH (f:File {repo: $repo, path: $file})<-[t:TOUCHES]-(c:Commit)<-[:AUTHORED]-(p:Person {name: $name})
    RETURN c.sha, c.committed_at, t.lines_added, t.lines_deleted
    ORDER BY c.committed_at DESC
    LIMIT 3
    """
    dir_res = graph.query(dir_q, {"repo": repo, "file": target_file, "name": reviewer_name})
    if dir_res and dir_res.result_set:
        row = dir_res.result_set[0]
        sha = str(row[0])
        ts = str(row[1]) if row[1] else ""
        added = int(row[2]) if row[2] else 0
        deleted = int(row[3]) if row[3] else 0

        c_node_id = f"commit:{sha[:10]}"
        nodes[c_node_id] = GraphNode(id=c_node_id, label=sha[:8], type="Commit", properties={"sha": sha, "date": ts})

        edges[f"touch_{c_node_id}_{target_id}"] = GraphEdge(
            id=f"touch_{c_node_id}_{target_id}", source=c_node_id, target=target_id, type="TOUCHES", properties={"added": added, "deleted": deleted}
        )
        edges[f"auth_{rev_id}_{c_node_id}"] = GraphEdge(
            id=f"auth_{rev_id}_{c_node_id}", source=rev_id, target=c_node_id, type="AUTHORED", properties={}
        )

        steps.append(EvidencePathStep(
            step_index=1,
            source_type="Person",
            source_label=reviewer_name,
            relationship_type="AUTHORED",
            target_type="Commit",
            target_label=sha[:8],
            description=f"{reviewer_name} authored commit {sha[:8]}",
            evidence_properties={"sha": sha, "date": ts},
        ))
        steps.append(EvidencePathStep(
            step_index=2,
            source_type="Commit",
            source_label=sha[:8],
            relationship_type="TOUCHES",
            target_type="File",
            target_label=target_file.split("/")[-1],
            description=f"Commit {sha[:8]} modified {target_file} (+{added}, -{deleted} lines)",
            evidence_properties={"added": added, "deleted": deleted, "file": target_file},
        ))

        return EvidencePathResponse(
            target_file=target_file,
            candidate_reviewer=reviewer_name,
            repository=repo,
            path_found=True,
            path_type="direct",
            steps=steps,
            nodes=list(nodes.values()),
            relationships=list(edges.values()),
            is_constrained=is_constrained,
            constraint_info=constraint_info,
        )

    # 2. Check co-change blast-radius path
    cc_path_q = """
    MATCH (f:File {repo: $repo, path: $file})-[cc:CO_CHANGES]-(other:File {repo: $repo})<-[t:TOUCHES]-(c:Commit)<-[:AUTHORED]-(p:Person {name: $name})
    RETURN other.path, cc.count, c.sha, c.committed_at, t.lines_added, t.lines_deleted
    ORDER BY cc.count DESC, c.committed_at DESC
    LIMIT 1
    """
    cc_path_res = graph.query(cc_path_q, {"repo": repo, "file": target_file, "name": reviewer_name})
    if cc_path_res and cc_path_res.result_set:
        row = cc_path_res.result_set[0]
        other_path = str(row[0])
        cc_count = int(row[1])
        sha = str(row[2])
        ts = str(row[3]) if row[3] else ""
        added = int(row[4]) if row[4] else 0
        deleted = int(row[5]) if row[5] else 0

        other_id = f"file:{other_path}"
        nodes[other_id] = GraphNode(id=other_id, label=other_path.split("/")[-1], type="File", properties={"path": other_path})

        c_node_id = f"commit:{sha[:10]}"
        nodes[c_node_id] = GraphNode(id=c_node_id, label=sha[:8], type="Commit", properties={"sha": sha, "date": ts})

        edges[f"cc_{target_id}_{other_id}"] = GraphEdge(
            id=f"cc_{target_id}_{other_id}", source=target_id, target=other_id, type="CO_CHANGES", properties={"count": cc_count}
        )
        edges[f"touch_{c_node_id}_{other_id}"] = GraphEdge(
            id=f"touch_{c_node_id}_{other_id}", source=c_node_id, target=other_id, type="TOUCHES", properties={"added": added, "deleted": deleted}
        )
        edges[f"auth_{rev_id}_{c_node_id}"] = GraphEdge(
            id=f"auth_{rev_id}_{c_node_id}", source=rev_id, target=c_node_id, type="AUTHORED", properties={}
        )

        steps.append(EvidencePathStep(
            step_index=1,
            source_type="File",
            source_label=target_file.split("/")[-1],
            relationship_type="CO_CHANGES",
            target_type="File",
            target_label=other_path.split("/")[-1],
            description=f"File historically co-changes with {other_path} across {cc_count} shared commits",
            evidence_properties={"co_change_count": cc_count},
        ))
        steps.append(EvidencePathStep(
            step_index=2,
            source_type="Commit",
            source_label=sha[:8],
            relationship_type="TOUCHES",
            target_type="File",
            target_label=other_path.split("/")[-1],
            description=f"Commit {sha[:8]} modified related file {other_path} (+{added}, -{deleted} lines)",
            evidence_properties={"sha": sha, "added": added, "deleted": deleted},
        ))
        steps.append(EvidencePathStep(
            step_index=3,
            source_type="Person",
            source_label=reviewer_name,
            relationship_type="AUTHORED",
            target_type="Commit",
            target_label=sha[:8],
            description=f"{reviewer_name} authored commit {sha[:8]} on related blast-radius file",
            evidence_properties={"sha": sha, "date": ts},
        ))

        return EvidencePathResponse(
            target_file=target_file,
            candidate_reviewer=reviewer_name,
            repository=repo,
            path_found=True,
            path_type="co_change",
            steps=steps,
            nodes=list(nodes.values()),
            relationships=list(edges.values()),
            is_constrained=is_constrained,
            constraint_info=constraint_info,
        )

    # No path found
    return EvidencePathResponse(
        target_file=target_file,
        candidate_reviewer=reviewer_name,
        repository=repo,
        path_found=False,
        path_type="none",
        steps=[],
        nodes=list(nodes.values()),
        relationships=[],
        is_constrained=is_constrained,
        constraint_info=constraint_info,
    )


# ---------------------------------------------------------------------------
# Route: GET /graph/memory
# ---------------------------------------------------------------------------

@router.get("/graph/memory")
def get_graph_memory(
    repo: str = Query("databases", description="Repository name"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
) -> Dict[str, Any]:
    """
    Returns active persistent memory constraints, recent actions, and outcomes
    stored in FalkorDB to make engineering memory fully inspectable.
    """
    health = db_manager.check_health()
    if not health.get("connected"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is offline; memory inspection unavailable.",
        )

    graph = db_manager.get_graph()
    repo_name = repo.strip().lower()

    # Active constraints
    const_q = """
    MATCH (c:Constraint {active: true})
    WHERE c.scope = $scope OR c.scope = $raw_scope
    RETURN c.id, c.type, c.subject, c.scope, c.created_at, c.source_action_id
    ORDER BY c.created_at DESC
    LIMIT 20
    """
    const_res = graph.query(const_q, {"scope": f"repo:{repo_name}", "raw_scope": repo_name})
    active_constraints = []
    if const_res and const_res.result_set:
        for r in const_res.result_set:
            active_constraints.append({
                "id": str(r[0]),
                "type": str(r[1]),
                "subject": str(r[2]),
                "scope": str(r[3]),
                "created_at": str(r[4]) if r[4] else "",
                "source_action_id": str(r[5]) if r[5] else None,
            })

    # Recent actions
    act_q = """
    MATCH (a:Action)
    WHERE a.repo = $repo
    OPTIONAL MATCH (a)-[:RESULTED_IN]->(o:Outcome)
    RETURN a.id, a.type, a.status, a.reviewer, a.target, a.created_at, o.id, o.status, o.success
    ORDER BY a.created_at DESC
    LIMIT 10
    """
    act_res = graph.query(act_q, {"repo": repo_name})
    recent_actions = []
    if act_res and act_res.result_set:
        for r in act_res.result_set:
            recent_actions.append({
                "action_id": str(r[0]),
                "type": str(r[1]),
                "status": str(r[2]),
                "reviewer": str(r[3]),
                "target": str(r[4]),
                "created_at": str(r[5]) if r[5] else "",
                "outcome": {
                    "id": str(r[6]),
                    "status": str(r[7]),
                    "success": bool(r[8]),
                } if r[6] else None,
            })

    return {
        "repository": repo_name,
        "active_constraints": active_constraints,
        "recent_actions": recent_actions,
    }


# ---------------------------------------------------------------------------
# Route: POST /graph/memory/reset-demo
# ---------------------------------------------------------------------------

@router.post("/graph/memory/reset-demo")
def reset_demo_constraints(
    repo: str = Query("databases", description="Repository name to clear demo constraints for"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
) -> Dict[str, Any]:
    """
    Deactivates test constraints for 'Tom Christie' in databases repo to allow judges
    and testers to re-run the Golden Workflow demonstration from scratch cleanly.
    """
    health = db_manager.check_health()
    if not health.get("connected"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is offline; cannot reset constraints.",
        )

    graph = db_manager.get_graph()
    q = """
    MATCH (c:Constraint {subject: 'Tom Christie'})
    SET c.active = false
    RETURN count(c)
    """
    res = graph.query(q)
    count = res.result_set[0][0] if (res and res.result_set) else 0

    return {
        "status": "success",
        "deactivated_constraints_count": count,
        "message": f"Deactivated {count} constraint(s) on Tom Christie. Demo state is reset to Session 1.",
    }
