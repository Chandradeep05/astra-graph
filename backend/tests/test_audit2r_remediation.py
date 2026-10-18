import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager
from app.evidence.models import RefusalCode, VerificationResult
from app.actions.models import (
    Action,
    ActionCreate,
    ActionStatus,
    ActionType,
    ActionPersistenceError,
)
from app.actions.service import ActionService, get_action_service
from app.tools.registry import (
    ReviewersToolInput,
    ChangeImpactToolInput,
    ImpactReviewersToolInput,
)
from app.tools.reviewers import get_verified_reviewers
from app.tools.impact import change_impact
from app.tools.impact_reviewers import impact_reviewers
from app.context.service import ContextPackageService
from app.agent.llm.provider import TemplateProvider
from app.agent.coordinator import get_agent_coordinator
from app.agent.narrator import TemplateNarrator

client = TestClient(app)


# =============================================================================
# A2-001: Evidence Verifier Failure Fail-Closed Gating
# =============================================================================


def test_a2_001_evidence_verifier_failure_gates_tool_refusal():
    """When EvidenceVerifier fails, all intelligence tools and context services must fail-closed."""
    failed_verification = VerificationResult(
        verified=False,
        missing_evidence_ids=["ev_fake_123"],
        invalid_evidence_ids=[],
        reason="Cryptographic hash mismatch or evidence not present in graph.",
    )

    with patch("app.tools.reviewers.EvidenceVerifier.verify", return_value=failed_verification):
        rev_resp = get_verified_reviewers("backend/app/main.py", repo_name="arth")
        assert rev_resp.refused is True
        assert rev_resp.reason_code == RefusalCode.LOW_EVIDENCE
        assert rev_resp.candidates == []
        assert "Evidence verification failed" in rev_resp.reason

    with patch("app.tools.impact.EvidenceVerifier.verify", return_value=failed_verification):
        impact_resp = change_impact("backend/app/main.py", repo_name="arth")
        assert impact_resp.refused is True
        assert impact_resp.reason_code == RefusalCode.LOW_EVIDENCE
        assert impact_resp.impacted_files == []
        assert "Evidence verification failed" in impact_resp.reason

    with patch("app.tools.impact_reviewers.EvidenceVerifier.verify", return_value=failed_verification):
        ir_resp = impact_reviewers("backend/app/main.py", repo_name="arth")
        assert ir_resp.refused is True
        assert ir_resp.reason_code == RefusalCode.LOW_EVIDENCE
        assert ir_resp.candidates == []
        assert "Evidence verification failed" in ir_resp.reason

    with patch("app.context.service.EvidenceVerifier.verify", return_value=failed_verification):
        svc = ContextPackageService()
        pkg = svc.get_context_package(repository="arth", subject="backend/app/main.py", subject_type="file")
        assert pkg.refusal is True
        assert pkg.reason_code == RefusalCode.LOW_EVIDENCE
        assert "Evidence verification failed" in pkg.reason


def test_a2_001_evidence_verifier_failure_blocks_action_proposal_and_verifies_coordinator():
    """Unverified or refused tool results must NEVER propose an action."""
    coordinator = get_agent_coordinator()

    # When query routes to reviewers but tool is refused / unverified
    failed_verification = VerificationResult(
        verified=False,
        missing_evidence_ids=["ev_fake"],
        invalid_evidence_ids=[],
        reason="Evidence verification failed.",
    )
    with patch("app.tools.reviewers.EvidenceVerifier.verify", return_value=failed_verification):
        response = coordinator.ask("Who should review backend/app/main.py?")
        assert response.verified is False
        assert response.proposed_action is None


def test_a2_001_narrator_refuses_when_unverified():
    """TemplateNarrator must refuse and NEVER emit verified confirmation when verification fails."""
    narrator = TemplateNarrator()
    unverified = VerificationResult(
        verified=False,
        missing_evidence_ids=["ev_1"],
        invalid_evidence_ids=[],
        reason="Graph evidence unverified.",
    )
    mock_resp = MagicMock()
    mock_resp.refused = False
    mock_resp.refusal = False
    mock_resp.verification = unverified

    narration = narrator.narrate(
        question="Who should review backend/app/main.py?",
        response=mock_resp,
    )

    assert "[Refusal: LOW_EVIDENCE]" in narration
    assert "Evidence verification failed" in narration
    assert "Based on verified" not in narration


# =============================================================================
# A2-002: ActionService FalkorDB Persistence Fail-Closed & Zero Split-Brain
# =============================================================================


def test_a2_002_action_creation_db_failure_blocks_in_memory_state():
    """If FalkorDB fails during action creation, ActionPersistenceError is raised and no in-memory state is retained."""
    mock_mgr = MagicMock(spec=FalkorDBManager)
    mock_mgr.get_graph.side_effect = Exception("FalkorDB connection timeout")

    service = ActionService(db_manager=mock_mgr)
    payload = ActionCreate(
        type=ActionType.REQUEST_REVIEW,
        repo="arth",
        target="backend/app/main.py",
        reviewer="chandradeep05",
        reason="Historical changes.",
        evidence_ids=["ev_commit_1"],
        claim_ids=["claim_1"],
    )

    with pytest.raises(ActionPersistenceError) as exc_info:
        service.create_action(payload)

    assert "FalkorDB provenance write error" in str(exc_info.value)
    # Zero split-brain: action must NOT exist in service._actions
    assert len(service._actions) == 0


def test_a2_002_action_approval_db_failure_rolls_back_in_memory_status():
    """If FalkorDB status update fails during approval, status rolls back to PROPOSED and ActionPersistenceError is raised."""
    mock_mgr = MagicMock(spec=FalkorDBManager)
    mock_graph = MagicMock()
    mock_mgr.get_graph.return_value = mock_graph

    # Creation succeeds
    service = ActionService(db_manager=mock_mgr)
    payload = ActionCreate(
        type=ActionType.REQUEST_REVIEW,
        repo="arth",
        target="backend/app/main.py",
        reviewer="chandradeep05",
        reason="Historical changes.",
        evidence_ids=["ev_commit_1"],
        claim_ids=["claim_1"],
    )
    action = service.create_action(payload)
    assert action.status == ActionStatus.PROPOSED
    assert action.id in service._actions

    # Now make FalkorDB query fail on status update
    mock_graph.query.side_effect = Exception("FalkorDB network disconnected")

    with pytest.raises(ActionPersistenceError):
        service.approve_action(action.id)

    # In-memory status must be rolled back to PROPOSED
    assert service._actions[action.id].status == ActionStatus.PROPOSED
    assert service._actions[action.id].approved_at is None


def test_a2_002_action_rejection_db_failure_rolls_back_in_memory_status():
    """If FalkorDB status update fails during rejection, status rolls back to PROPOSED."""
    mock_mgr = MagicMock(spec=FalkorDBManager)
    mock_graph = MagicMock()
    mock_mgr.get_graph.return_value = mock_graph

    service = ActionService(db_manager=mock_mgr)
    payload = ActionCreate(
        type=ActionType.REQUEST_REVIEW,
        repo="arth",
        target="backend/app/main.py",
        reviewer="chandradeep05",
        reason="Historical changes.",
        evidence_ids=["ev_commit_1"],
        claim_ids=["claim_1"],
    )
    action = service.create_action(payload)
    assert action.status == ActionStatus.PROPOSED

    mock_graph.query.side_effect = Exception("FalkorDB network disconnected")

    with pytest.raises(ActionPersistenceError):
        service.reject_action(action.id)

    assert service._actions[action.id].status == ActionStatus.PROPOSED


def test_a2_002_action_execution_db_failure_blocks_sandbox_and_rolls_back():
    """If FalkorDB fails before sandbox execution, the executor is NEVER invoked and status rolls back."""
    mock_mgr = MagicMock(spec=FalkorDBManager)
    mock_graph = MagicMock()
    mock_mgr.get_graph.return_value = mock_graph

    mock_executor = MagicMock()
    service = ActionService(db_manager=mock_mgr, executor=mock_executor)

    payload = ActionCreate(
        type=ActionType.REQUEST_REVIEW,
        repo="arth",
        target="backend/app/main.py",
        reviewer="chandradeep05",
        reason="Historical changes.",
        evidence_ids=["ev_commit_1"],
        claim_ids=["claim_1"],
    )
    action = service.create_action(payload)
    service.approve_action(action.id)
    assert action.status == ActionStatus.APPROVED

    # Status update to EXECUTING fails in FalkorDB
    mock_graph.query.side_effect = Exception("FalkorDB down")

    with pytest.raises(ActionPersistenceError):
        service.execute_action(action.id)

    # Sandbox executor must NOT have been called
    mock_executor.execute.assert_not_called()
    # In-memory status must be rolled back to APPROVED
    assert service._actions[action.id].status == ActionStatus.APPROVED


def test_a2_002_action_execution_outcome_persistence_failure_raises():
    """If sandbox succeeds but FalkorDB fails to write outcome provenance, ActionPersistenceError is raised."""
    mock_mgr = MagicMock(spec=FalkorDBManager)
    mock_graph = MagicMock()
    mock_mgr.get_graph.return_value = mock_graph

    mock_executor = MagicMock()
    mock_executor.execute.return_value = {"dry_run": True}
    service = ActionService(db_manager=mock_mgr, executor=mock_executor)

    payload = ActionCreate(
        type=ActionType.REQUEST_REVIEW,
        repo="arth",
        target="backend/app/main.py",
        reviewer="chandradeep05",
        reason="Historical changes.",
        evidence_ids=["ev_commit_1"],
        claim_ids=["claim_1"],
    )
    action = service.create_action(payload)
    service.approve_action(action.id)

    # Allow transition to EXECUTING, but fail on outcome write
    call_count = 0

    def query_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:  # First call is status -> EXECUTING, second is outcome write
            raise Exception("Disk write error")
        return MagicMock()

    mock_graph.query.side_effect = query_side_effect

    with pytest.raises(ActionPersistenceError) as exc_info:
        service.execute_action(action.id)

    assert "durable outcome provenance could not be persisted" in str(exc_info.value)
    assert service._actions[action.id].status == ActionStatus.FAILED


def test_a2_002_api_returns_503_on_persistence_error():
    """FastAPI actions endpoint must catch ActionPersistenceError and return 503 Service Unavailable."""
    mock_svc = MagicMock()
    mock_svc.create_action.side_effect = ActionPersistenceError("FalkorDB is unreachable")
    app.dependency_overrides[get_action_service] = lambda: mock_svc

    try:
        resp = client.post(
            "/api/v1/actions",
            json={
                "type": "request_review",
                "repo": "arth",
                "target": "backend/app/main.py",
                "reviewer": "chandradeep05",
                "reason": "Test reason",
                "evidence_ids": ["ev_1"],
                "claim_ids": ["claim_1"],
            },
        )
        assert resp.status_code == 503
        assert "FalkorDB is unreachable" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_action_service, None)


# =============================================================================
# A2-003: Intent Hijacking & Negative Space Fallthrough
# =============================================================================


def test_a2_003_intent_hijacking_negative_space():
    """TemplateProvider must not route destructive or unrelated queries containing file paths to reviewers_for."""
    provider = TemplateProvider()

    # Destructive commands
    assert provider.select_tool("Delete repository immediately").tool == "out_of_scope"
    assert provider.select_tool("Drop table users from database").tool == "out_of_scope"
    assert provider.select_tool("Destroy file backend/app/main.py").tool == "out_of_scope"
    assert provider.select_tool("Delete backend/app/main.py now").tool == "out_of_scope"

    # Negative-space non-reviewer queries mentioning files
    assert provider.select_tool("Is backend/app/main.py buggy?").tool == "out_of_scope"
    assert provider.select_tool("backend/app/main.py").tool == "out_of_scope"
    assert provider.select_tool("Explain how backend/app/main.py works").tool == "out_of_scope"
    assert provider.select_tool("What does backend/app/main.py do?").tool == "out_of_scope"
    assert provider.select_tool("Refactor backend/app/main.py to use async").tool == "out_of_scope"

    # Valid queries should still route properly
    assert provider.select_tool("Who should review backend/app/main.py?").tool == "reviewers_for"
    assert provider.select_tool("Recommend reviewers for backend/app/main.py").tool == "reviewers_for"
    assert provider.select_tool("Who wrote backend/app/main.py?").tool == "reviewers_for"
    assert provider.select_tool("What is the blast radius of modifying backend/app/main.py?").tool == "change_impact"
    assert provider.select_tool("Show context package for backend/app/main.py").tool == "context_package"


# =============================================================================
# A2-004: Graceful Offline Handling in Reviewers API / Tool
# =============================================================================


def test_a2_004_offline_falkordb_returns_graceful_no_path_refusal():
    """When FalkorDB is offline, reviewers tool and API must return structured NO_PATH refusal without crashing."""
    mock_unhealthy = {"status": "unhealthy", "connected": False, "error": "Connection refused on localhost:6379"}

    with patch.object(FalkorDBManager, "check_health", return_value=mock_unhealthy):
        # 1. Direct tool call
        resp = get_verified_reviewers("backend/app/main.py", repo_name="arth")
        assert resp.refused is True
        assert resp.reason_code == RefusalCode.NO_PATH
        assert "FalkorDB connection unavailable" in resp.reason
        assert resp.candidates == []

        # 2. FastAPI endpoint
        api_resp = client.get("/api/v1/reviewers", params={"file": "backend/app/main.py", "repo": "arth"})
        assert api_resp.status_code == 200
        data = api_resp.json()
        assert data["refused"] is True
        assert data["reason_code"] == "NO_PATH"
        assert "FalkorDB connection unavailable" in data["reason"]


# =============================================================================
# A2-005: Pydantic Query Bounds
# =============================================================================


def test_a2_005_pydantic_bounds_enforced():
    """Pydantic schemas must enforce strict bounds on limit and max_evidence."""
    # ReviewersToolInput
    with pytest.raises(ValidationError):
        ReviewersToolInput(file="main.py", limit=0)

    with pytest.raises(ValidationError):
        ReviewersToolInput(file="main.py", limit=-5)

    with pytest.raises(ValidationError):
        ReviewersToolInput(file="main.py", limit=51)

    with pytest.raises(ValidationError):
        ReviewersToolInput(file="main.py", max_evidence=0)

    with pytest.raises(ValidationError):
        ReviewersToolInput(file="main.py", max_evidence=101)

    valid_rev = ReviewersToolInput(file="main.py", limit=10, max_evidence=20)
    assert valid_rev.limit == 10
    assert valid_rev.max_evidence == 20

    # ChangeImpactToolInput
    with pytest.raises(ValidationError):
        ChangeImpactToolInput(file="main.py", limit=0)

    with pytest.raises(ValidationError):
        ChangeImpactToolInput(file="main.py", limit=51)

    with pytest.raises(ValidationError):
        ChangeImpactToolInput(file="main.py", max_evidence=101)

    valid_impact = ChangeImpactToolInput(file="main.py", limit=10, max_evidence=30)
    assert valid_impact.limit == 10
    assert valid_impact.max_evidence == 30

    # ImpactReviewersToolInput
    with pytest.raises(ValidationError):
        ImpactReviewersToolInput(file="main.py", limit=0)

    with pytest.raises(ValidationError):
        ImpactReviewersToolInput(file="main.py", limit=51)

    with pytest.raises(ValidationError):
        ImpactReviewersToolInput(file="main.py", max_evidence=101)

    valid_ir = ImpactReviewersToolInput(file="main.py", limit=5, max_evidence=30)
    assert valid_ir.limit == 5
    assert valid_ir.max_evidence == 30
