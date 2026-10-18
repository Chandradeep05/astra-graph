import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.actions.models import ActionStatus, ActionType
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.evidence.models import RefusalCode
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_demo_constraints():
    """Ensures test constraints are cleaned up before and after each integration test."""
    client.post("/api/v1/graph/memory/reset-demo?repo=databases")
    yield
    client.post("/api/v1/graph/memory/reset-demo?repo=databases")



# ---------------------------------------------------------------------------
# Test 1: Web UI Index Serving & Shell Integrity
# ---------------------------------------------------------------------------
def test_web_ui_index_serving():
    """Verifies that GET / with text/html serves the Anvaya engineering frontend shell."""
    resp = client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    html = resp.text
    assert "ANVAYA" in html
    assert "FalkorDB" in html
    assert "tab-ask" in html
    assert "tab-graph" in html
    assert "tab-context" in html
    assert "tab-memory" in html
    assert "cytoscape.min.js" in html


# ---------------------------------------------------------------------------
# Test 2: UI Alias Routes (/ui, /app)
# ---------------------------------------------------------------------------
def test_ui_alias_routes():
    """Verifies that /ui and /app routes serve the frontend application."""
    r_ui = client.get("/ui")
    assert r_ui.status_code == 200
    assert "ANVAYA" in r_ui.text

    r_app = client.get("/app")
    assert r_app.status_code == 200
    assert "ANVAYA" in r_app.text


# ---------------------------------------------------------------------------
# Test 3: Static Assets Availability
# ---------------------------------------------------------------------------
def test_static_assets_availability():
    """Verifies that all client-side assets (CSS, JS, Cytoscape) serve with HTTP 200."""
    r_css = client.get("/static/style.css")
    assert r_css.status_code == 200
    assert len(r_css.text) > 1000
    assert "--bg-main" in r_css.text

    r_js = client.get("/static/app.js")
    assert r_js.status_code == 200
    assert len(r_js.text) > 1000
    assert "initHealthCheck" in r_js.text

    r_cy = client.get("/static/vendor/cytoscape.min.js")
    assert r_cy.status_code == 200
    assert len(r_cy.content) > 100000


# ---------------------------------------------------------------------------
# Test 4: Ask Workflow End-to-End API Response
# ---------------------------------------------------------------------------
def test_ask_workflow_end_to_end():
    """Verifies that POST /api/v1/ask produces verified narration, impact, reviewers, and proposed action."""
    # Reset demo state first to ensure baseline
    client.post("/api/v1/graph/memory/reset-demo?repo=databases")

    resp = client.post(
        "/api/v1/ask",
        json={
            "message": "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?",
            "repo": "databases",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["refused"] is False
    assert data["verified"] is True
    assert data["selected_tool"] == "impact_reviewers"
    assert len(data["impact"]) > 0
    assert len(data["reviewers"]) > 0
    assert data["proposed_action"] is not None
    assert data["proposed_action"]["status"] == ActionStatus.PROPOSED.value


# ---------------------------------------------------------------------------
# Test 5: Impact Blast-Radius Data Rendering
# ---------------------------------------------------------------------------
def test_impact_data_structure():
    """Verifies that impact records contain co-changing file paths and co_change_count."""
    resp = client.post(
        "/api/v1/ask",
        json={
            "message": "What is the blast radius and impact of modifying databases/backends/postgres.py?",
            "repo": "databases",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["impact"]) > 0
    top = data["impact"][0]
    assert "file" in top
    assert top["file"] != ""
    assert (top.get("co_change_count") or top.get("strength") or 0) > 0


# ---------------------------------------------------------------------------
# Test 6: Reviewer Candidate Evidence Grounding
# ---------------------------------------------------------------------------
def test_reviewer_candidate_evidence_grounding():
    """Verifies reviewer candidates include direct commits, co-change commits, and line touches."""
    resp = client.post(
        "/api/v1/ask",
        json={
            "message": "Who should review databases/backends/postgres.py?",
            "repo": "databases",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["reviewers"]) > 0
    top_rev = data["reviewers"][0]
    assert "person" in top_rev
    assert top_rev["person"] != ""
    assert (top_rev.get("direct_commits") or top_rev.get("commits") or 0) > 0


# ---------------------------------------------------------------------------
# Test 7: Action Approval Lifecycle (PROPOSED -> APPROVED -> SUCCEEDED)
# ---------------------------------------------------------------------------
def test_action_approval_lifecycle():
    """Verifies human-in-the-loop approval and sandbox execution writing outcome to FalkorDB."""
    # 1. Ask creates action
    ask_resp = client.post(
        "/api/v1/ask",
        json={
            "message": "Who should review databases/backends/postgres.py?",
            "repo": "databases",
        },
    )
    action = ask_resp.json()["proposed_action"]
    assert action is not None
    action_id = action["id"]
    assert action["status"].lower() == "proposed"

    # 2. Approve
    app_resp = client.post(f"/api/v1/actions/{action_id}/approve")
    assert app_resp.status_code == 200
    assert app_resp.json()["status"].lower() == "approved"

    # 3. Execute
    exe_resp = client.post(f"/api/v1/actions/{action_id}/execute")
    assert exe_resp.status_code == 200
    exe_data = exe_resp.json()
    assert exe_data["status"].lower() == "succeeded"
    assert exe_data["result"]["success"] is True

    # 4. Verify FalkorDB provenance
    mgr = get_falkor_manager()
    graph = mgr.get_graph()
    q = "MATCH (a:Action {id: $id})-[:RESULTED_IN]->(o:Outcome) RETURN o.status, o.success"
    res = graph.query(q, {"id": action_id})
    assert res and res.result_set
    assert res.result_set[0][0].lower() == "succeeded"
    assert res.result_set[0][1] is True


# ---------------------------------------------------------------------------
# Test 8: Action Rejection and Constraint Memory Persistence
# ---------------------------------------------------------------------------
def test_action_rejection_and_constraint_memory():
    """Verifies rejection records a constraint in FalkorDB persistent memory."""
    # 1. Create action via ask
    ask_resp = client.post(
        "/api/v1/ask",
        json={
            "message": "Who should review databases/backends/postgres.py?",
            "repo": "databases",
        },
    )
    action = ask_resp.json()["proposed_action"]
    assert action is not None
    action_id = action["id"]

    # 2. Reject with constraint
    rej_resp = client.post(
        f"/api/v1/actions/{action_id}/reject",
        json={
            "reason": "Reviewer on sabbatical",
            "constraint": {
                "type": "reviewer_unavailable",
                "subject": action["reviewer"],
                "scope": f"repo:{action['repo']}",
            },
        },
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"].lower() == "rejected"

    # 3. Verify constraint in FalkorDB
    mgr = get_falkor_manager()
    graph = mgr.get_graph()
    c_q = "MATCH (c:Constraint {subject: $subj, active: true}) RETURN c.type, c.scope"
    res = graph.query(c_q, {"subj": action["reviewer"]})
    assert res and res.result_set
    assert res.result_set[0][0] == "reviewer_unavailable"


# ---------------------------------------------------------------------------
# Test 9: Refusal Rendering on Unknown File
# ---------------------------------------------------------------------------
def test_refusal_rendering_unknown_file():
    """Verifies that querying an unknown file refuses cleanly without action proposal."""
    resp = client.post(
        "/api/v1/ask",
        json={
            "message": "Who should review nonexistent/virtual/module.py?",
            "repo": "databases",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["refused"] is True
    assert data["reason_code"] == RefusalCode.NO_PATH.value
    assert data["proposed_action"] is None
    assert "not found" in data["reason"].lower() or "no path" in data["reason"].lower()


# ---------------------------------------------------------------------------
# Test 10: FalkorDB Kill-Switch / Unavailable State
# ---------------------------------------------------------------------------
def test_falkordb_kill_switch_unavailable_handling():
    """Verifies that when FalkorDB is offline, graph endpoint returns 503 fail-closed."""
    with patch.object(FalkorDBManager, "check_health") as mock_health:
        mock_health.return_value = {"connected": False, "error": "Connection refused"}
        resp = client.get("/api/v1/graph?repo=databases")
        assert resp.status_code == 503
        assert "offline" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test 11: Graph Exploration Endpoint Bounded Data
# ---------------------------------------------------------------------------
def test_graph_endpoint_bounded_data():
    """Verifies that GET /api/v1/graph returns bounded nodes and relationships within limits."""
    resp = client.get("/api/v1/graph?repo=databases&max_nodes=50&max_edges=80")
    assert resp.status_code == 200
    data = resp.json()
    assert data["repository"] == "databases"
    assert data["node_count"] <= 50
    assert data["edge_count"] <= 80
    assert len(data["nodes"]) == data["node_count"]
    assert len(data["relationships"]) == data["edge_count"]

    node_types = {n["type"] for n in data["nodes"]}
    assert "Repository" in node_types
    assert "File" in node_types or "Module" in node_types


# ---------------------------------------------------------------------------
# Test 12: Subject-Focused Graph Exploration
# ---------------------------------------------------------------------------
def test_subject_focused_graph_exploration():
    """Verifies that GET /api/v1/graph with subject focuses on target file and its co-changes."""
    target = "databases/backends/postgres.py"
    resp = client.get(f"/api/v1/graph?repo=databases&subject={target}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subject"] == target
    file_nodes = [n for n in data["nodes"] if n["type"] == "File"]
    assert any(n["properties"].get("path") == target for n in file_nodes)


# ---------------------------------------------------------------------------
# Test 13: Evidence Path Tracing API
# ---------------------------------------------------------------------------
def test_evidence_path_tracing_api():
    """Verifies that GET /api/v1/graph/evidence-path extracts the exact multi-hop lineage path."""
    target = "databases/backends/postgres.py"
    reviewer = "Tom Christie"
    resp = client.get(
        f"/api/v1/graph/evidence-path?target_file={target}&reviewer_name={reviewer}&repo=databases"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path_found"] is True
    assert data["path_type"] in ("direct", "co_change")
    assert len(data["steps"]) >= 2
    assert any(s["source_type"] in ("Person", "File", "Commit") for s in data["steps"])


# ---------------------------------------------------------------------------
# Test 14: Node Inspection Properties Integrity
# ---------------------------------------------------------------------------
def test_node_inspection_properties_integrity():
    """Verifies that nodes returned by graph exploration contain structured properties."""
    resp = client.get("/api/v1/graph?repo=databases")
    assert resp.status_code == 200
    data = resp.json()

    for node in data["nodes"]:
        assert "id" in node
        assert "label" in node
        assert "type" in node
        assert isinstance(node["properties"], dict)


# ---------------------------------------------------------------------------
# Test 15: Context Package Rendering for ARTH
# ---------------------------------------------------------------------------
def test_context_package_rendering_arth():
    """Verifies that POST /api/v1/context returns rich structured context for ARTH."""
    resp = client.post(
        "/api/v1/context",
        json={"repository": "arth", "depth": 3, "max_files": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["repository"] == "arth"
    assert len(data["modules"]) >= 3
    assert len(data["important_files"]) > 0
    assert len(data["contributors"]) > 0
    assert data["verification"]["verified"] is True
    assert len(data["limitations"]) > 0


# ---------------------------------------------------------------------------
# Test 16: Persistent Memory State API
# ---------------------------------------------------------------------------
def test_persistent_memory_state_api():
    """Verifies that GET /api/v1/graph/memory returns active constraints and actions."""
    resp = client.get("/api/v1/graph/memory?repo=databases")
    assert resp.status_code == 200
    data = resp.json()
    assert data["repository"] == "databases"
    assert "active_constraints" in data
    assert "recent_actions" in data


# ---------------------------------------------------------------------------
# Test 17: Demo Reset Endpoint
# ---------------------------------------------------------------------------
def test_demo_reset_endpoint():
    """Verifies that POST /api/v1/graph/memory/reset-demo clears constraints for Tom Christie."""
    resp = client.post("/api/v1/graph/memory/reset-demo?repo=databases")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "Tom Christie" in data["message"]


# ---------------------------------------------------------------------------
# Test 18: Security: Cypher Injection Safety on Graph Endpoints
# ---------------------------------------------------------------------------
def test_cypher_injection_safety_on_graph_endpoints():
    """Verifies that malicious Cypher strings are treated safely as literals without injection."""
    malicious = "databases' OR 1=1; MATCH (n) DETACH DELETE n; //"
    resp = client.get(f"/api/v1/graph?repo={malicious}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_count"] <= 2  # Only repo node or empty, no unauthorized traversal


# ---------------------------------------------------------------------------
# Test 19: Security: No Secrets in API Payloads
# ---------------------------------------------------------------------------
def test_no_secrets_in_api_payloads():
    """Verifies that health and graph payloads do not leak API keys, passwords, or credentials."""
    r_health = client.get("/health")
    text_health = json.dumps(r_health.json()).lower()
    assert "api_key" not in text_health
    assert "password" not in text_health
    assert "secret" not in text_health

    r_graph = client.get("/api/v1/graph?repo=databases")
    text_graph = json.dumps(r_graph.json()).lower()
    assert "api_key" not in text_graph
    assert "password" not in text_graph


# ---------------------------------------------------------------------------
# Test 20: Golden Workflow 2-Session Demo Integration
# ---------------------------------------------------------------------------
def test_golden_workflow_two_session_demo():
    """
    Verifies the entire M12 judge-facing golden workflow:
    1. Reset demo state
    2. Session 1: Query postgres.py -> Tom Christie recommended
    3. User rejects Tom Christie with reviewer_unavailable constraint
    4. Session 2: Query postgres.py -> Tom Christie excluded, George Bogodukhov elevated!
    """
    # 1. Reset
    client.post("/api/v1/graph/memory/reset-demo?repo=databases")

    # 2. Session 1
    s1_resp = client.post(
        "/api/v1/ask",
        json={
            "message": "I'm changing databases/backends/postgres.py. Who should review it?",
            "repo": "databases",
        },
    )
    s1_data = s1_resp.json()
    assert s1_data["reviewers"][0]["person"] == "Tom Christie"
    action_id = s1_data["proposed_action"]["id"]

    # 3. Reject with constraint
    client.post(
        f"/api/v1/actions/{action_id}/reject",
        json={
            "reason": "Tom Christie on sabbatical / unavailable",
            "constraint": {
                "type": "reviewer_unavailable",
                "subject": "Tom Christie",
                "scope": "repo:databases",
            },
        },
    )

    # 4. Session 2 (fresh query)
    s2_resp = client.post(
        "/api/v1/ask",
        json={
            "message": "I'm changing databases/backends/postgres.py. Who should review it?",
            "repo": "databases",
        },
    )
    s2_data = s2_resp.json()
    assert s2_data["refused"] is False
    assert s2_data["reviewers"][0]["person"] == "George Bogodukhov"
    assert any(e["person"] == "Tom Christie" for e in s2_data["excluded_candidates"])
