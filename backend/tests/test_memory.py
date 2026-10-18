import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.memory.models import (
    Constraint,
    ConstraintCreate,
    ConstraintType,
    InvalidConstraintError,
)
from app.memory.service import MemoryService, get_memory_service
from app.actions.models import ActionCreate, ActionType, ActionStatus
from app.actions.service import ActionService
from app.agent.coordinator import AgentCoordinator, get_agent_coordinator
from app.agent.llm.adapter import LLMAdapter
from app.tools.registry import tool_registry
from app.tools.reviewers import get_verified_reviewers
from app.evidence.models import RefusalCode

client = TestClient(app)


def test_memory_constraint_creation_valid():
    """Verify valid constraint writes to FalkorDB and creates (:Constraint) node."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    service = MemoryService(db_manager=db)
    payload = ConstraintCreate(
        type=ConstraintType.REVIEWER_UNAVAILABLE,
        subject="alice_test",
        predicate="unavailable_for_review",
        value=True,
        scope="repo:test_repo",
    )
    constraint = service.create_constraint(payload)

    assert constraint.id.startswith("constraint_")
    assert constraint.type == ConstraintType.REVIEWER_UNAVAILABLE
    assert constraint.subject == "alice_test"
    assert constraint.scope == "repo:test_repo"
    assert constraint.active is True

    # Verify directly in FalkorDB via Cypher
    graph = db.get_graph()
    res = graph.query(
        "MATCH (c:Constraint {id: $id}) RETURN c.type, c.subject, c.scope, c.active",
        {"id": constraint.id},
    )
    assert len(res.result_set) == 1
    assert res.result_set[0][0] == "reviewer_unavailable"
    assert res.result_set[0][1] == "alice_test"
    assert res.result_set[0][2] == "repo:test_repo"
    assert res.result_set[0][3] is True

    # Cleanup test constraint
    service.deactivate_constraint(constraint.id)


def test_memory_constraint_creation_invalid_rejected():
    """Verify invalid constraints (empty subject, unsupported type) are rejected."""
    service = MemoryService()

    # Empty subject
    with pytest.raises(InvalidConstraintError):
        service.create_constraint(
            ConstraintCreate(
                type=ConstraintType.REVIEWER_UNAVAILABLE,
                subject="",
            )
        )

    # Unsupported type
    with pytest.raises(Exception):
        service.create_constraint(
            ConstraintCreate(
                type="arbitrary_unsupported_type",  # type: ignore
                subject="alice",
            )
        )


def test_memory_constraint_scoping_and_activity():
    """Verify active constraints are returned only when scope matches, and inactive are ignored."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    service = MemoryService(db_manager=db)

    # 1. Constraint for repo:repo_alpha
    c1 = service.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="bob_test",
            scope="repo:repo_alpha",
        )
    )

    # 2. Constraint for repo:repo_beta
    c2 = service.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="bob_test",
            scope="repo:repo_beta",
        )
    )

    # Matching alpha
    alpha_constraints = service.get_active_constraints(
        type=ConstraintType.REVIEWER_UNAVAILABLE.value,
        scope="repo:repo_alpha",
    )
    assert any(c.id == c1.id for c in alpha_constraints)
    assert not any(c.id == c2.id for c in alpha_constraints)

    # Deactivate c1
    service.deactivate_constraint(c1.id)
    alpha_after = service.get_active_constraints(
        type=ConstraintType.REVIEWER_UNAVAILABLE.value,
        scope="repo:repo_alpha",
    )
    assert not any(c.id == c1.id for c in alpha_after)

    # Cleanup c2
    service.deactivate_constraint(c2.id)


def test_memory_constraint_linked_to_action():
    """Verify (:Action)-[:RESULTED_IN]->(:Constraint) provenance link in FalkorDB."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    action_svc = ActionService(db_manager=db)
    mem_svc = MemoryService(db_manager=db)

    # Create action
    action = action_svc.create_action(
        ActionCreate(
            repo="test_repo",
            target="test_file.py",
            reviewer="charlie_test",
            reason="Authored commits",
            evidence_ids=["ev_1"],
        )
    )

    # Reject action with structured constraint
    action_svc.reject_action(
        action.id,
        reason="Reviewer unavailable for this sprint",
        constraint=ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="charlie_test",
            scope="repo:test_repo",
        ),
    )

    # Check graph relationship
    graph = db.get_graph()
    q = """
    MATCH (a:Action {id: $act_id})-[:RESULTED_IN]->(c:Constraint)
    RETURN c.id, c.subject, c.active
    """
    res = graph.query(q, {"act_id": action.id})
    assert len(res.result_set) >= 1
    assert res.result_set[0][1] == "charlie_test"
    assert res.result_set[0][2] is True

    # Cleanup constraint
    c_id = res.result_set[0][0]
    mem_svc.deactivate_constraint(c_id)


def test_synthetic_multi_contributor_exclusion_and_changed_decision():
    """
    Synthetic multi-contributor test:
    Setup a graph with Alice (10 commits) and Bob (5 commits).
    Session 1: Alice is selected.
    Alice is constrained as unavailable.
    Session 2: Bob is selected instead, and Alice is in excluded_candidates!
    """
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    graph_name = "test_multi_contributor_m7"
    graph = db.get_graph(graph_name)

    # Clean previous run
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.get_graph(graph_name)

    # Setup Synthetic Graph
    setup_q = """
    CREATE (r:Repository {name: 'demo_multi'})
    CREATE (f:File {repo: 'demo_multi', path: 'src/main.py'})
    CREATE (r)-[:CONTAINS]->(f)
    CREATE (p1:Person {name: 'Alice', email: 'alice@example.com'})
    CREATE (p2:Person {name: 'Bob', email: 'bob@example.com'})
    CREATE (c1:Commit {sha: 'sha_a1', ts: '2026-09-01T10:00:00Z'})
    CREATE (c2:Commit {sha: 'sha_b1', ts: '2026-09-01T09:00:00Z'})
    CREATE (p1)-[:AUTHORED]->(c1)
    CREATE (c1)-[:TOUCHES {added: 50, deleted: 5}]->(f)
    CREATE (p2)-[:AUTHORED]->(c2)
    CREATE (c2)-[:TOUCHES {added: 20, deleted: 2}]->(f)
    """
    graph.query(setup_q)

    # Session 1: No constraints -> Alice should be top candidate
    resp1 = get_verified_reviewers(
        target_file="src/main.py",
        repo_name="demo_multi",
        graph_name=graph_name,
        db_manager=db,
    )
    assert resp1.refused is False
    assert len(resp1.candidates) == 2
    assert resp1.candidates[0].person_name == "Alice"
    assert len(resp1.excluded_candidates) == 0

    # Human establishes constraint: Alice unavailable
    mem_svc = MemoryService(db_manager=db, graph_name=graph_name)
    # create action to link
    act_svc = ActionService(db_manager=db, memory_service=mem_svc, graph_name=graph_name)
    act = act_svc.create_action(
        ActionCreate(
            repo="demo_multi",
            target="src/main.py",
            reviewer="Alice",
            reason="Top candidate",
            evidence_ids=["ev_commit_sha_a1"],
        )
    )
    act_svc.reject_action(
        act.id,
        reason="Alice is on leave",
        constraint=ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="Alice",
            scope="repo:demo_multi",
            source_action_id=act.id,
        ),
    )

    # Session 2: Fresh query -> Alice is excluded, Bob is selected!
    resp2 = get_verified_reviewers(
        target_file="src/main.py",
        repo_name="demo_multi",
        graph_name=graph_name,
        db_manager=db,
    )
    assert resp2.refused is False
    assert len(resp2.candidates) == 1
    assert resp2.candidates[0].person_name == "Bob"
    assert len(resp2.excluded_candidates) == 1
    assert resp2.excluded_candidates[0].person == "Alice"
    assert resp2.excluded_candidates[0].reason == "reviewer_unavailable"
    assert len(resp2.memory_evidence) >= 1

    # Cleanup test graph
    try:
        graph.delete()
    except Exception:
        pass


def test_cypher_injection_safety_in_memory_constraints():
    """Verify malicious Cypher statements in constraints are treated as literal values."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    service = MemoryService(db_manager=db)
    malicious_subject = "hacker'; MATCH (n) DETACH DELETE n; //"
    malicious_scope = "repo:arth'; RETURN 1; //"

    constraint = service.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject=malicious_subject,
            scope=malicious_scope,
        )
    )

    graph = db.get_graph()
    res = graph.query("MATCH (c:Constraint {id: $id}) RETURN c.subject, c.scope", {"id": constraint.id})
    assert len(res.result_set) == 1
    assert res.result_set[0][0] == malicious_subject
    assert res.result_set[0][1] == malicious_scope

    # Verify repository was not deleted
    r_res = graph.query("MATCH (r:Repository {name: 'arth'}) RETURN r.name")
    assert len(r_res.result_set) >= 1

    # Cleanup
    service.deactivate_constraint(constraint.id)


def test_falkordb_kill_switch_prevents_stale_local_memory():
    """When FalkorDB is disconnected, memory cannot be loaded from in-process cache; refuses cleanly."""
    mock_db = MagicMock()
    mock_db.check_health.return_value = {"connected": False, "error": "Connection refused"}

    resp = get_verified_reviewers(
        target_file="backend/app/api/v1/internal.py",
        repo_name="arth",
        db_manager=mock_db,
    )
    # Must refuse rather than use stale in-memory cached candidates
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert "FalkorDB" in resp.reason or "No commit history found" in resp.reason


def test_golden_demo_persistent_memory_closed_loop():
    """
    PRIMARY DEMO WE ARE BUILDING (M7 End-to-End):
    Session 1:
      1. Ask: "Who should review backend/app/api/v1/internal.py?"
      2. Graph reasoning produces candidate: chandradeep05
      3. Action proposed: request_review(chandradeep05)
      4. Human rejects with structured constraint: chandradeep05 is unavailable for repo:arth.
      5. Constraint persisted into FalkorDB.
    Fresh Session:
      6. Process / coordinator / service memory is reset.
      7. Ask exact same question: "Who should review backend/app/api/v1/internal.py?"
      8. Anvaya consults FalkorDB graph memory.
      9. chandradeep05 is excluded due to active graph constraint.
      10. Since ARTH has no other contributor, Anvaya refuses rather than inventing one.
      11. Response contains excluded_candidates and constraint evidence.
    """
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # ==================== SESSION 1 ====================
    coordinator_s1 = get_agent_coordinator()
    resp_s1 = coordinator_s1.ask("Who should review backend/app/api/v1/internal.py?", repo="arth")

    assert resp_s1.refused is False
    assert len(resp_s1.candidates) >= 1
    assert resp_s1.candidates[0].person_name == "chandradeep05"
    assert resp_s1.proposed_action is not None
    action_id = resp_s1.proposed_action.id

    # Human rejects recommendation with structured constraint: reviewer_unavailable
    action_svc_s1 = coordinator_s1.action_service
    action_svc_s1.reject_action(
        action_id,
        reason="chandradeep05 is currently unavailable for code reviews",
        constraint=ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="chandradeep05",
            scope="repo:arth",
            source_action_id=action_id,
        ),
    )

    # Verify constraint is in FalkorDB
    graph = db.get_graph()
    q_check = """
    MATCH (c:Constraint {subject: 'chandradeep05', active: true})
    RETURN c.id, c.scope
    """
    res_c = graph.query(q_check)
    assert len(res_c.result_set) >= 1
    constraint_id = res_c.result_set[0][0]

    # ==================== FRESH SESSION ====================
    # Reset all process-level coordinators, singletons, and local service references
    fresh_mem_svc = MemoryService(db_manager=db)
    fresh_act_svc = ActionService(db_manager=db, memory_service=fresh_mem_svc)
    fresh_coordinator = AgentCoordinator(
        tool_registry=tool_registry,
        llm_adapter=LLMAdapter(),
        db_manager=db,
        action_service=fresh_act_svc,
    )

    # Ask the exact same question in the fresh session
    resp_s2 = fresh_coordinator.ask("Who should review backend/app/api/v1/internal.py?", repo="arth")

    # chandradeep05 MUST be excluded by graph memory
    assert len(resp_s2.candidates) == 0
    assert len(resp_s2.excluded_candidates) >= 1
    assert resp_s2.excluded_candidates[0].person == "chandradeep05"
    assert resp_s2.excluded_candidates[0].reason == "reviewer_unavailable"
    # Anvaya refuses rather than inventing a hallucinated reviewer
    assert resp_s2.refused is True
    assert resp_s2.reason_code == RefusalCode.NO_PATH
    assert "excluded due to active graph constraints" in resp_s2.reason
    assert "[Refusal: NO_PATH]" in resp_s2.narration
    # Must propose zero actions
    assert resp_s2.proposed_action is None

    # Cleanup constraint so ARTH remains usable for other tests
    fresh_mem_svc.deactivate_constraint(constraint_id)

    # Verify that deactivating the constraint restores chandradeep05 in Session 3
    resp_s3 = fresh_coordinator.ask("Who should review backend/app/api/v1/internal.py?", repo="arth")
    assert resp_s3.refused is False
    assert len(resp_s3.candidates) >= 1
    assert resp_s3.candidates[0].person_name == "chandradeep05"
