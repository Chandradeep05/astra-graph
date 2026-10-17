import pytest
from fastapi.testclient import TestClient

from app.actions.models import ActionStatus, ActionType, InvalidStateTransitionError
from app.actions.service import ActionService
from app.agent.coordinator import AgentCoordinator
from app.agent.llm.adapter import LLMAdapter
from app.agent.models import AskRequest, AskResponse
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.evidence.models import RefusalCode
from app.main import app
from app.memory.models import ConstraintCreate, ConstraintType
from app.memory.service import MemoryService
from app.tools.registry import tool_registry

client = TestClient(app)


def test_tool_routing_combined_intent():
    """Verifies that combined impact + reviewer natural language routes to impact_reviewers."""
    adapter = LLMAdapter()

    # 1. Combined question with full file path
    tc1 = adapter.select_tool(
        "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?"
    )
    assert tc1.tool == "impact_reviewers"
    assert tc1.arguments.get("repo") == "databases"
    assert tc1.arguments.get("file_path") == "databases/backends/postgres.py"

    # 2. Combined question with shorthand postgres.py
    tc2 = adapter.select_tool(
        "I'm changing postgres.py. What could this affect and who should review it?"
    )
    assert tc2.tool == "impact_reviewers"
    assert tc2.arguments.get("repo") == "databases"
    assert tc2.arguments.get("file_path") == "databases/backends/postgres.py"

    # 3. Pure blast-radius impact question
    tc3 = adapter.select_tool(
        "What is the blast radius and impact of modifying databases/backends/postgres.py?"
    )
    assert tc3.tool == "change_impact"
    assert tc3.arguments.get("repo") == "databases"

    # 4. Standard reviewers_for question for arth repo
    tc4 = adapter.select_tool(
        "Who should review backend/app/api/v1/watchlist.py?", repo="arth"
    )
    assert tc4.tool == "reviewers_for"
    assert tc4.arguments.get("repo") == "arth"


def test_arbitrary_cypher_and_prompt_injection_rejected():
    """Verifies that arbitrary Cypher injection attempts are safely rejected as out of scope."""
    coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
    )
    resp = coord.ask("MATCH (n:Person) DETACH DELETE n RETURN n")
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.OUT_OF_SCOPE
    assert resp.proposed_action is None


def test_golden_combined_workflow_investigate_explain_propose():
    """
    Core M10 Golden Workflow:
    Investigate -> Explain -> Propose
    Verifies that the agent executes multi-hop graph reasoning (blast-radius + author lineage),
    verifies evidence, produces factual narration, and proposes a human-approved action.
    """
    coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
    )
    resp = coord.ask(
        "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?"
    )

    # 1. Status & Routing
    assert resp.refused is False
    assert resp.verified is True
    assert resp.selected_tool == "impact_reviewers"

    # 2. Structured Impact (Blast Radius)
    assert len(resp.impact) > 0
    top_impacted = resp.impact[0]
    assert "file" in top_impacted
    assert "co_change_count" in top_impacted
    assert top_impacted["co_change_count"] > 0

    # 3. Structured Reviewers (Ranked Candidates)
    assert len(resp.reviewers) > 0
    top_reviewer = resp.reviewers[0]
    assert top_reviewer["person"] == "Tom Christie"
    assert top_reviewer["direct_commits"] > 0
    assert top_reviewer["co_change_commits"] > 0
    assert len(top_reviewer["relevant_files"]) > 0

    # 4. Evidence & Claims Grounding
    assert len(resp.evidence) > 0
    assert len(resp.claims) > 0
    assert resp.verification is not None
    assert resp.verification.verified is True

    # 5. Narration & Summary
    assert "Tom Christie" in resp.summary
    assert "blast radius" in resp.summary.lower()
    assert resp.narration == resp.summary

    # 6. Action Proposal in PROPOSED state
    assert resp.proposed_action is not None
    action = resp.proposed_action
    assert action.type == ActionType.REQUEST_REVIEW
    assert action.status == ActionStatus.PROPOSED
    assert action.reviewer == "Tom Christie"
    assert action.target == "databases/backends/postgres.py"
    assert action.repo == "databases"
    assert len(action.evidence_ids) > 0
    assert len(action.claim_ids) > 0


def test_human_approval_enforced_execution_forbidden_before_approval():
    """
    Verifies the human-in-the-loop safety contract:
    Execution of an action before explicit human approval is strictly forbidden.
    """
    coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
    )
    resp = coord.ask(
        "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?"
    )
    action = resp.proposed_action
    assert action is not None
    assert action.status == ActionStatus.PROPOSED

    action_service = coord.action_service

    # Attempting execution directly in PROPOSED status must fail
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        action_service.execute_action(action.id)
    assert "Human approval is mandatory" in str(exc_info.value)

    # Status must still be PROPOSED
    fresh_act = action_service.get_action(action.id)
    assert fresh_act.status == ActionStatus.PROPOSED


def test_human_approval_and_sandbox_execution_persists_outcome():
    """
    Verifies human approval -> sandbox execution -> FalkorDB graph persistence.
    (:Action)-[:RESULTED_IN]->(:Outcome)
    """
    db_manager = get_falkor_manager()
    coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
        db_manager=db_manager,
    )
    resp = coord.ask(
        "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?"
    )
    action = resp.proposed_action
    assert action is not None

    action_service = coord.action_service

    # 1. Human Approves
    approved = action_service.approve_action(action.id)
    assert approved.status == ActionStatus.APPROVED

    # 2. Execution
    executed = action_service.execute_action(action.id)
    assert executed.status == ActionStatus.SUCCEEDED
    assert executed.result is not None
    assert executed.result.get("sandbox") is True
    assert executed.result.get("success") is True

    # 3. Direct FalkorDB Verification: Action -> Outcome
    graph = db_manager.get_graph()
    q = """
    MATCH (a:Action {id: $act_id})-[:RESULTED_IN]->(o:Outcome)
    RETURN a.id, a.status, o.id, o.status, o.success
    """
    res = graph.query(q, {"act_id": action.id})
    assert res and res.result_set
    row = res.result_set[0]
    assert row[0] == action.id
    assert row[1] == ActionStatus.SUCCEEDED.value
    assert row[3] == ActionStatus.SUCCEEDED.value
    assert row[4] is True


def test_refusal_produces_no_action_for_unknown_file():
    """Verifies that queries with insufficient graph evidence refuse cleanly and propose NO action."""
    coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
    )
    resp = coord.ask(
        "I'm modifying nonexistent/file/unknown_service.py. Who should review it and what will it break?",
        repo="databases",
    )
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert resp.proposed_action is None
    assert len(resp.candidates) == 0
    assert len(resp.reviewers) == 0


def test_memory_constraint_changes_recommendation_fresh_process():
    """
    Core Track 02 & M10 Memory Demonstration:
    SESSION 1: Ask who should review postgres.py -> Tom Christie proposed.
    User rejects with reviewer_unavailable constraint on Tom Christie.
    SESSION 2: Fresh AgentCoordinator / process with no in-memory state.
    Ask the EXACT same question -> Tom Christie excluded by persistent FalkorDB constraint,
    George Bogodukhov recommended!
    """
    db_manager = get_falkor_manager()
    mem_service = MemoryService(db_manager=db_manager)

    # Ensure no lingering constraint from previous runs
    for c in mem_service.get_active_constraints(subject="Tom Christie", scope="repo:databases"):
        mem_service.deactivate_constraint(c.id)

    # -------------------------------------------------------------
    # SESSION 1
    # -------------------------------------------------------------
    session1_coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
        db_manager=db_manager,
    )
    resp1 = session1_coord.ask("I'm changing postgres.py. Who should review it?")
    assert resp1.refused is False
    assert resp1.reviewers[0]["person"] == "Tom Christie"
    assert resp1.proposed_action is not None
    assert resp1.proposed_action.reviewer == "Tom Christie"

    # User rejects the proposed reviewer with reviewer_unavailable constraint
    action_id = resp1.proposed_action.id
    session1_coord.action_service.reject_action(
        action_id=action_id,
        reason="Tom Christie on sabbatical / unavailable",
        constraint={
            "type": ConstraintType.REVIEWER_UNAVAILABLE.value,
            "subject": "Tom Christie",
            "scope": "repo:databases",
        },
    )

    # Direct FalkorDB Verification of Constraint Node
    graph = db_manager.get_graph()
    c_res = graph.query(
        "MATCH (c:Constraint {subject: 'Tom Christie', active: true}) RETURN c.id, c.type, c.scope"
    )
    assert c_res and c_res.result_set
    constraint_id = c_res.result_set[0][0]
    assert c_res.result_set[0][1] == "reviewer_unavailable"

    # -------------------------------------------------------------
    # SESSION 2 (Fresh Process Simulation: new objects, 0 memory state)
    # -------------------------------------------------------------
    session2_coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
        db_manager=db_manager,
        action_service=ActionService(db_manager=db_manager),
    )
    resp2 = session2_coord.ask("I'm changing postgres.py. Who should review it?")

    # Tom Christie is excluded; George Bogodukhov is the new recommendation
    assert resp2.refused is False
    assert resp2.verified is True
    assert len(resp2.excluded_candidates) == 1
    assert resp2.excluded_candidates[0].person == "Tom Christie"
    assert resp2.reviewers[0]["person"] == "George Bogodukhov"
    assert resp2.proposed_action is not None
    assert resp2.proposed_action.reviewer == "George Bogodukhov"
    assert "Excluded by active graph memory constraint: Tom Christie" in resp2.summary

    # Cleanup constraint
    mem_service.deactivate_constraint(constraint_id)


def test_all_candidates_constrained_produces_no_path_refusal_and_no_action():
    """
    Verifies that when all historical contributors are excluded by persistent memory,
    Anvaya safely refuses with NO_PATH and never invents a candidate or action.
    """
    db_manager = get_falkor_manager()
    mem_service = MemoryService(db_manager=db_manager)

    # In ARTH repo, chandradeep05 is the sole contributor for internal.py
    # Adding a constraint excludes 100% of contributors
    c = mem_service.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="chandradeep05",
            scope="repo:arth",
        )
    )

    try:
        coord = AgentCoordinator(
            tool_registry=tool_registry,
            llm_adapter=LLMAdapter(),
            db_manager=db_manager,
        )
        resp = coord.ask(
            "Who should review backend/app/api/v1/internal.py?",
            repo="arth",
        )
        assert resp.refused is True
        assert resp.reason_code == RefusalCode.NO_PATH
        assert resp.proposed_action is None
        assert len(resp.excluded_candidates) >= 1
        assert "active graph constraints" in resp.summary.lower()
    finally:
        mem_service.deactivate_constraint(c.id)


def test_graph_centrality_kill_switch_without_falkordb():
    """
    GRAPH CENTRALITY TEST:
    Demonstrates that FalkorDB is not decorative storage but operationally required
    for the agent's reasoning. Without FalkorDB reachable, the agent safely refuses
    and NEVER returns a cached reviewer or impact answer.
    """
    # Create a disconnected manager pointing to an unreachable port
    disconnected_manager = FalkorDBManager(host="localhost", port=59999)

    coord = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
        db_manager=disconnected_manager,
    )
    resp = coord.ask(
        "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?",
        repo="databases",
    )
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert resp.proposed_action is None
    assert len(resp.candidates) == 0
    assert len(resp.impact) == 0
    assert len(resp.reviewers) == 0


def test_api_v1_ask_golden_request_and_backwards_compatibility():
    """
    Verifies POST /api/v1/ask endpoint handling both the new golden workflow
    and existing reviewer intelligence queries.
    """
    # 1. Golden combined query
    payload = {
        "message": "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?",
        "repo": "databases",
    }
    res = client.post("/api/v1/ask", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["refused"] is False
    assert data["verified"] is True
    assert len(data["impact"]) > 0
    assert len(data["reviewers"]) > 0
    assert data["reviewers"][0]["person"] == "Tom Christie"
    assert data["proposed_action"] is not None
    assert data["proposed_action"]["type"] == "request_review"
    assert data["proposed_action"]["status"] == "PROPOSED"
    assert data["verification"]["verified"] is True

    # 2. Backwards-compatible query for arth repo
    payload2 = {
        "message": "Who should review backend/app/api/v1/watchlist.py?",
        "repo": "arth",
    }
    res2 = client.post("/api/v1/ask", json=payload2)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["refused"] is False
    assert len(data2["candidates"]) > 0
    assert data2["selected_tool"] == "reviewers_for"
