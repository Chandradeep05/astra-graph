import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager
from app.actions.models import (
    Action,
    ActionCreate,
    ActionStatus,
    ActionType,
    InvalidActionError,
    InvalidStateTransitionError,
    ActionNotFoundError,
)
from app.actions.service import ActionService
from app.actions.executor import SandboxActionExecutor
from app.agent.coordinator import get_agent_coordinator
from app.evidence.models import RefusalCode

client = TestClient(app)


def test_action_creation_valid():
    """Valid action creation transitions to PROPOSED with evidence grounding."""
    service = ActionService()
    payload = ActionCreate(
        type=ActionType.REQUEST_REVIEW,
        repo="arth",
        target="backend/app/api/v1/internal.py",
        reviewer="chandradeep05",
        reviewer_email="c4chandradeep2005@gmail.com",
        reason="Reviewer has 10 historical commits touching this file.",
        evidence_ids=["ev_commit_79595add24d7"],
        claim_ids=["claim_1_commits"],
    )

    action = service.create_action(payload)
    assert action.id.startswith("act_")
    assert action.status == ActionStatus.PROPOSED
    assert action.type == ActionType.REQUEST_REVIEW
    assert action.reviewer == "chandradeep05" or action.arguments["reviewer"] == "chandradeep05"
    assert "ev_commit_79595add24d7" in action.evidence_ids
    assert action.approved_at is None
    assert action.executed_at is None


def test_action_creation_missing_evidence_rejected():
    """Action without evidence IDs must be rejected immediately."""
    service = ActionService()
    with pytest.raises(Exception):
        ActionCreate(
            type=ActionType.REQUEST_REVIEW,
            repo="arth",
            target="internal.py",
            reviewer="chandradeep05",
            reason="No evidence provided",
            evidence_ids=[],  # Empty evidence must be rejected by Pydantic / service
        )


def test_action_creation_invalid_type_rejected():
    """Unsupported action types must be rejected."""
    service = ActionService()
    with pytest.raises(Exception):
        payload = ActionCreate(
            type="delete_database",  # type: ignore
            repo="arth",
            target="internal.py",
            reviewer="chandradeep05",
            reason="Malicious operation",
            evidence_ids=["ev_1"],
        )
        service.create_action(payload)


def test_action_approval_lifecycle():
    """Test standard approval transition: PROPOSED -> APPROVED."""
    service = ActionService()
    action = service.create_action(
        ActionCreate(
            repo="arth",
            target="render.yaml",
            reviewer="chandradeep05",
            reason="Author of file",
            evidence_ids=["ev_commit_9af07c8d1d69"],
        )
    )
    assert action.status == ActionStatus.PROPOSED

    approved = service.approve_action(action.id)
    assert approved.status == ActionStatus.APPROVED
    assert approved.approved_at is not None


def test_action_rejection_lifecycle():
    """Test rejection transition: PROPOSED -> REJECTED."""
    service = ActionService()
    action = service.create_action(
        ActionCreate(
            repo="arth",
            target="render.yaml",
            reviewer="chandradeep05",
            reason="Author of file",
            evidence_ids=["ev_commit_9af07c8d1d69"],
        )
    )
    assert action.status == ActionStatus.PROPOSED

    rejected = service.reject_action(action.id, reason="Not required at this time")
    assert rejected.status == ActionStatus.REJECTED
    assert rejected.rejected_at is not None
    assert "Not required at this time" in (rejected.error_message or "")


def test_illegal_state_transitions_rejected():
    """Enforce state machine boundaries: invalid transitions must raise errors."""
    service = ActionService()
    action = service.create_action(
        ActionCreate(
            repo="arth",
            target="render.yaml",
            reviewer="chandradeep05",
            reason="Author of file",
            evidence_ids=["ev_commit_9af07c8d1d69"],
        )
    )

    # 1. Unapproved action cannot be executed directly: PROPOSED -> EXECUTING must FAIL
    with pytest.raises(InvalidStateTransitionError) as exc:
        service.execute_action(action.id)
    assert "Human approval is mandatory" in str(exc.value)

    # 2. Reject action: PROPOSED -> REJECTED
    service.reject_action(action.id)

    # 3. Rejected action cannot be approved: REJECTED -> APPROVED must FAIL
    with pytest.raises(InvalidStateTransitionError):
        service.approve_action(action.id)

    # 4. Rejected action cannot be executed: REJECTED -> EXECUTING must FAIL
    with pytest.raises(InvalidStateTransitionError):
        service.execute_action(action.id)


def test_action_execution_success():
    """Test valid execution: APPROVED -> EXECUTING -> SUCCEEDED."""
    service = ActionService()
    action = service.create_action(
        ActionCreate(
            repo="arth",
            target="render.yaml",
            reviewer="chandradeep05",
            reason="Author of file",
            evidence_ids=["ev_commit_9af07c8d1d69"],
        )
    )
    service.approve_action(action.id)

    executed = service.execute_action(action.id)
    assert executed.status == ActionStatus.SUCCEEDED
    assert executed.executed_at is not None
    assert executed.result is not None
    assert executed.result["success"] is True
    assert executed.result["sandbox"] is True
    assert executed.result["reviewer"] == "chandradeep05"

    # Cannot approve or execute again
    with pytest.raises(InvalidStateTransitionError):
        service.approve_action(action.id)

    with pytest.raises(InvalidStateTransitionError):
        service.execute_action(action.id)


def test_action_execution_failure_recorded():
    """Failing executor sets action status to FAILED and records error message."""
    mock_executor = MagicMock()
    mock_executor.execute.side_effect = RuntimeError("Sandbox connection reset")

    service = ActionService(executor=mock_executor)
    action = service.create_action(
        ActionCreate(
            repo="arth",
            target="render.yaml",
            reviewer="chandradeep05",
            reason="Author of file",
            evidence_ids=["ev_commit_9af07c8d1d69"],
        )
    )
    service.approve_action(action.id)

    failed = service.execute_action(action.id)
    assert failed.status == ActionStatus.FAILED
    assert "Sandbox connection reset" in (failed.error_message or "")


def test_graph_provenance_recording():
    """Verify that Action, Evidence, Outcome, and Person relationships exist in FalkorDB."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    service = ActionService(db_manager=db)
    action = service.create_action(
        ActionCreate(
            repo="arth",
            target="backend/app/api/v1/internal.py",
            reviewer="chandradeep05",
            reviewer_email="c4chandradeep2005@gmail.com",
            reason="Authored 10 commits",
            evidence_ids=["ev_commit_79595add24d7"],
            claim_ids=["claim_1"],
        )
    )
    service.approve_action(action.id)
    service.execute_action(action.id)

    # Verify directly in FalkorDB via graph query
    graph = db.get_graph()

    # 1. Action node
    res_a = graph.query("MATCH (a:Action {id: $id}) RETURN a.status, a.target", {"id": action.id})
    assert len(res_a.result_set) == 1
    assert res_a.result_set[0][0] == "SUCCEEDED"
    assert res_a.result_set[0][1] == "backend/app/api/v1/internal.py"

    # 2. Evidence relationship
    res_e = graph.query("MATCH (a:Action {id: $id})-[:BASED_ON]->(e:Evidence) RETURN e.id", {"id": action.id})
    assert len(res_e.result_set) >= 1
    assert res_e.result_set[0][0] == "ev_commit_79595add24d7"

    # 3. Outcome relationship
    res_o = graph.query("MATCH (a:Action {id: $id})-[:RESULTED_IN]->(o:Outcome) RETURN o.status, o.success", {"id": action.id})
    assert len(res_o.result_set) == 1
    assert res_o.result_set[0][0] == "SUCCEEDED"
    assert res_o.result_set[0][1] is True

    # 4. Target Person relationship
    res_p = graph.query("MATCH (a:Action {id: $id})-[:TARGETS]->(p:Person) RETURN p.name", {"id": action.id})
    assert len(res_p.result_set) >= 1
    assert res_p.result_set[0][0] == "chandradeep05"


def test_cypher_injection_safety_in_actions():
    """Malicious strings in arguments or reasons must never execute arbitrary Cypher."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    service = ActionService(db_manager=db)
    injection_target = "internal.py'; MATCH (n) DETACH DELETE n; //"
    injection_reason = "') RETURN 1; MATCH (x) DELETE x; //"

    action = service.create_action(
        ActionCreate(
            repo="arth",
            target=injection_target,
            reviewer="chandradeep05",
            reason=injection_reason,
            evidence_ids=["ev_commit_test_1"],
        )
    )
    # The action must be safely persisted as literal strings without graph deletion
    graph = db.get_graph()
    res = graph.query("MATCH (a:Action {id: $id}) RETURN a.target, a.reason", {"id": action.id})
    assert len(res.result_set) == 1
    assert res.result_set[0][0] == injection_target
    assert res.result_set[0][1] == injection_reason

    # Verify repository still exists and was not deleted by injection
    repo_res = graph.query("MATCH (r:Repository {name: 'arth'}) RETURN r.name")
    assert len(repo_res.result_set) >= 1


def test_api_action_endpoints_full_cycle():
    """Verify HTTP REST API endpoints for Action proposal, approval, execution, and retrieval."""
    # 1. Propose Action
    resp_create = client.post(
        "/api/v1/actions",
        json={
            "type": "request_review",
            "repo": "arth",
            "target": "render.yaml",
            "reviewer": "chandradeep05",
            "reason": "Author of render.yaml",
            "evidence_ids": ["ev_commit_9af07c8d1d69"],
        },
    )
    assert resp_create.status_code == 201
    act_data = resp_create.json()
    act_id = act_data["id"]
    assert act_data["status"] == "PROPOSED"

    # 2. Retrieve Action
    resp_get = client.get(f"/api/v1/actions/{act_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == act_id

    # 3. Unapproved execution must fail with 400
    resp_exec_fail = client.post(f"/api/v1/actions/{act_id}/execute")
    assert resp_exec_fail.status_code == 400
    assert "Human approval is mandatory" in resp_exec_fail.json()["detail"]

    # 4. Approve Action
    resp_app = client.post(f"/api/v1/actions/{act_id}/approve")
    assert resp_app.status_code == 200
    assert resp_app.json()["status"] == "APPROVED"

    # 5. Execute Action
    resp_exec = client.post(f"/api/v1/actions/{act_id}/execute")
    assert resp_exec.status_code == 200
    final_data = resp_exec.json()
    assert final_data["status"] == "SUCCEEDED"
    assert final_data["result"]["success"] is True

    # 6. List actions
    resp_list = client.get("/api/v1/actions?repo=arth")
    assert resp_list.status_code == 200
    assert any(a["id"] == act_id for a in resp_list.json())


def test_ask_integration_refusal_creates_no_action():
    """Refusal on nonexistent file must NEVER produce a proposed action."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    coordinator = get_agent_coordinator()
    resp = coordinator.ask("Who should review non_existent_file.py?", repo="arth")

    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert resp.proposed_action is None


def test_golden_demo_end_to_end_closed_loop():
    """
    GOLDEN DEMO INTEGRATION TEST:
    1. Ask: "Who should review backend/app/api/v1/internal.py?"
    2. Anvaya finds reviewer through FalkorDB graph reasoning.
    3. Evidence is verified.
    4. Anvaya proposes: request_review action (status: PROPOSED).
    5. Approve action (human gate) -> status: APPROVED.
    6. Execute action -> status: SUCCEEDED.
    7. Outcome is written to FalkorDB.
    8. Verify graph contains: Action, Evidence link, Outcome link, Person link.
    """
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    coordinator = get_agent_coordinator()

    # Step 1: User asks question
    resp = coordinator.ask("Who should review backend/app/api/v1/internal.py?", repo="arth")

    # Step 2 & 3: Verified reviewers found through FalkorDB
    assert resp.refused is False
    assert resp.verified is True
    assert len(resp.candidates) >= 1
    assert resp.candidates[0].person_name == "chandradeep05"
    assert len(resp.evidence) >= 1

    # Step 4: Proposed action is generated
    proposed_act = resp.proposed_action
    assert proposed_act is not None
    assert proposed_act.type == ActionType.REQUEST_REVIEW
    assert proposed_act.target == "backend/app/api/v1/internal.py"
    assert proposed_act.arguments["reviewer"] == "chandradeep05"
    assert len(proposed_act.evidence_ids) >= 1

    # Step 5: Action is in PROPOSED status (cannot execute without approval)
    assert proposed_act.status == ActionStatus.PROPOSED
    action_svc = coordinator.action_service
    with pytest.raises(InvalidStateTransitionError):
        action_svc.execute_action(proposed_act.id)

    # Step 6: Human Approval
    approved_act = action_svc.approve_action(proposed_act.id)
    assert approved_act.status == ActionStatus.APPROVED
    assert approved_act.approved_at is not None

    # Step 7 & 8: Execution via Sandbox Executor
    executed_act = action_svc.execute_action(proposed_act.id)
    assert executed_act.status == ActionStatus.SUCCEEDED
    assert executed_act.executed_at is not None
    assert executed_act.result["success"] is True
    assert executed_act.result["sandbox"] is True

    # Step 9: Verify graph provenance in FalkorDB
    graph = db.get_graph()

    # Verify Action node
    q_act = graph.query(
        "MATCH (a:Action {id: $id}) RETURN a.status, a.target, a.type",
        {"id": proposed_act.id},
    )
    assert len(q_act.result_set) == 1
    assert q_act.result_set[0][0] == "SUCCEEDED"
    assert q_act.result_set[0][1] == "backend/app/api/v1/internal.py"
    assert q_act.result_set[0][2] == "request_review"

    # Verify Evidence provenance link
    q_ev = graph.query(
        "MATCH (a:Action {id: $id})-[:BASED_ON]->(e:Evidence) RETURN count(e)",
        {"id": proposed_act.id},
    )
    assert q_ev.result_set[0][0] >= 1

    # Verify Outcome link
    q_out = graph.query(
        "MATCH (a:Action {id: $id})-[:RESULTED_IN]->(o:Outcome) RETURN o.status, o.success",
        {"id": proposed_act.id},
    )
    assert len(q_out.result_set) == 1
    assert q_out.result_set[0][0] == "SUCCEEDED"
    assert q_out.result_set[0][1] is True

    # Verify Target Person link
    q_pers = graph.query(
        "MATCH (a:Action {id: $id})-[:TARGETS]->(p:Person) RETURN p.name",
        {"id": proposed_act.id},
    )
    assert len(q_pers.result_set) >= 1
    assert q_pers.result_set[0][0] == "chandradeep05"
