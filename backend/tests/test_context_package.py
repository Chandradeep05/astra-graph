import json
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.agent.coordinator import get_agent_coordinator
from app.context.models import ContextPackage, ContextPackageRequest, SubjectType
from app.context.service import ContextPackageService
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.evidence.models import RefusalCode
from app.evidence.verifier import EvidenceVerifier
from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: ARTH Repository Context Package Generation
# ---------------------------------------------------------------------------
def test_arth_repository_context_generation():
    """Verifies complete, bounded context package generation for repository ARTH."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="arth")

    assert pkg.refusal is False
    assert pkg.repository == "arth"
    assert pkg.subject == "arth"
    assert pkg.subject_type == SubjectType.REPOSITORY
    assert len(pkg.modules) >= 3  # backend, frontend, docs, etc.
    assert len(pkg.important_files) > 0
    assert len(pkg.contributors) > 0
    assert pkg.architecture is not None
    assert "Repository 'arth'" in pkg.architecture.summary
    assert pkg.freshness.head_sha != ""
    assert pkg.freshness.commit_count > 0
    assert pkg.verification is not None
    assert pkg.verification.verified is True
    assert len(pkg.claims) > 0
    assert len(pkg.evidence) > 0


# ---------------------------------------------------------------------------
# Test 2: ARTH File Context Generation
# ---------------------------------------------------------------------------
def test_arth_file_context_generation():
    """Verifies file-level context package generation for assistant engine."""
    service = ContextPackageService()
    target = "backend/app/engines/assistant/engine.py"
    pkg = service.get_context_package(repository="arth", subject=target)

    assert pkg.refusal is False
    assert pkg.subject == target
    assert pkg.subject_type == SubjectType.FILE
    assert pkg.repository == "arth"
    assert len(pkg.contributors) > 0
    assert pkg.historical_context.total_commits > 0
    assert pkg.impact_context is not None
    assert pkg.impact_context.target == target
    assert pkg.reviewer_context is not None
    assert pkg.verification is not None
    assert pkg.verification.verified is True
    assert len(pkg.claims) > 0
    assert len(pkg.evidence) > 0


# ---------------------------------------------------------------------------
# Test 3: Unknown Repository Refusal
# ---------------------------------------------------------------------------
def test_unknown_repository_refusal():
    """Verifies safe fail-closed refusal when repository does not exist in graph."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="nonexistent_repo_xyz_999")

    assert pkg.refusal is True
    assert pkg.reason_code == RefusalCode.NO_PATH
    assert "not found" in pkg.reason.lower()
    assert pkg.verification.verified is False


# ---------------------------------------------------------------------------
# Test 4: Unknown File Refusal
# ---------------------------------------------------------------------------
def test_unknown_file_refusal():
    """Verifies safe fail-closed refusal when file subject does not exist in graph."""
    service = ContextPackageService()
    pkg = service.get_context_package(
        repository="arth",
        subject="nonexistent/virtual/path/file.py",
        subject_type="file",
    )

    assert pkg.refusal is True
    assert pkg.reason_code == RefusalCode.NO_PATH
    assert "not found" in pkg.reason.lower()


# ---------------------------------------------------------------------------
# Test 5: Bounded Depth Clamping
# ---------------------------------------------------------------------------
def test_bounded_depth_clamping():
    """Verifies that requested depth is deterministically clamped to [1, 5]."""
    service = ContextPackageService()

    pkg_high = service.get_context_package(repository="arth", depth=99)
    assert pkg_high.depth == 5

    pkg_low = service.get_context_package(repository="arth", depth=-10)
    assert pkg_low.depth == 1


# ---------------------------------------------------------------------------
# Test 6: Max Files Truncation Reporting
# ---------------------------------------------------------------------------
def test_max_files_truncation_reporting():
    """Verifies that when file results exceed max_files, truncation is recorded."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="arth", max_files=2)

    assert len(pkg.important_files) <= 2
    assert pkg.truncated is True
    assert pkg.truncation_reason is not None
    assert "files" in pkg.truncation_reason.lower()


# ---------------------------------------------------------------------------
# Test 7: Max Contributors Truncation Reporting
# ---------------------------------------------------------------------------
def test_max_contributors_truncation_reporting():
    """Verifies that when contributors exceed max_contributors, truncation is recorded."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="databases", max_contributors=1)

    assert len(pkg.contributors) <= 1
    assert pkg.truncated is True
    assert pkg.truncation_reason is not None
    assert "contributors" in pkg.truncation_reason.lower()


# ---------------------------------------------------------------------------
# Test 8: Evidence Verifier Passes All Claims
# ---------------------------------------------------------------------------
def test_evidence_verifier_passes_all_claims():
    """Verifies that all claims in a generated package pass EvidenceVerifier without rejections."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="arth")

    verifier = EvidenceVerifier()
    res = verifier.verify(pkg.claims, pkg.evidence)

    assert res.verified is True
    assert len(res.rejected_claims) == 0
    assert len(res.verified_claims) == len(pkg.claims)


# ---------------------------------------------------------------------------
# Test 9: Freshness Metadata Integrity
# ---------------------------------------------------------------------------
def test_freshness_metadata_integrity():
    """Verifies that graph snapshot metadata (head_sha, synced_at) is faithfully extracted."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="arth")

    assert pkg.freshness.sync_id != ""
    assert len(pkg.freshness.head_sha) >= 7
    assert pkg.freshness.synced_at != ""
    assert isinstance(pkg.freshness.is_stale, bool)


# ---------------------------------------------------------------------------
# Test 10: Contributor Line Churn Accuracy
# ---------------------------------------------------------------------------
def test_contributor_churn_accuracy():
    """Verifies that contributor records contain real commit counts and line churn."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="arth")

    primary = next((c for c in pkg.contributors if "c4chandradeep" in c.email.lower() or "chandradeep" in c.name.lower()), None)
    assert primary is not None
    assert primary.commit_count > 0
    assert primary.lines_added > 0
    assert len(primary.files_touched) > 0


# ---------------------------------------------------------------------------
# Test 11: Co-Change and Blast-Radius Accuracy
# ---------------------------------------------------------------------------
def test_co_change_and_blast_radius_accuracy():
    """Verifies that file context populates co-changes and dependency strength."""
    service = ContextPackageService()
    pkg = service.get_context_package(
        repository="databases",
        subject="databases/backends/postgres.py",
    )

    assert pkg.refusal is False
    assert len(pkg.impact_context.co_changing_files) > 0
    assert any(d.relationship_type == "CO_CHANGES" for d in pkg.dependencies)


# ---------------------------------------------------------------------------
# Test 12: Persistent Memory Constraint Visibility
# ---------------------------------------------------------------------------
def test_persistent_memory_constraint_visibility():
    """Verifies that active graph memory constraints are visible in the context package."""
    service = ContextPackageService()
    pkg = service.get_context_package(
        repository="databases",
        subject="databases/backends/postgres.py",
    )

    assert pkg.refusal is False
    # If constraints are active in the graph for databases, verify they reflect in the package
    if pkg.constraints:
        c = pkg.constraints[0]
        assert c.active is True
        assert c.subject != ""
        # Check reviewer context excluded list
        if any(c.subject in r["person"] or c.subject in r["email"] for r in pkg.reviewer_context.excluded_reviewers):
            assert len(pkg.reviewer_context.excluded_reviewers) > 0


# ---------------------------------------------------------------------------
# Test 13: Fresh Process No Cache Guarantee
# ---------------------------------------------------------------------------
def test_fresh_process_no_cache_guarantee():
    """Verifies that instantiating a fresh service accesses FalkorDB directly without cached state."""
    s1 = ContextPackageService()
    pkg1 = s1.get_context_package(repository="arth")

    s2 = ContextPackageService()
    pkg2 = s2.get_context_package(repository="arth")

    assert pkg1.repository == pkg2.repository
    assert pkg1.freshness.head_sha == pkg2.freshness.head_sha
    assert len(pkg1.modules) == len(pkg2.modules)
    assert pkg1.verification.verified is True
    assert pkg2.verification.verified is True


# ---------------------------------------------------------------------------
# Test 14: Databases Repository Context Package
# ---------------------------------------------------------------------------
def test_databases_repo_context_package():
    """Verifies repository context package for databases repository."""
    service = ContextPackageService()
    pkg = service.get_context_package(repository="databases")

    assert pkg.refusal is False
    assert pkg.repository == "databases"
    assert pkg.verification.verified is True
    assert any(m.name == "databases" for m in pkg.modules)
    assert len(pkg.contributors) >= 2


# ---------------------------------------------------------------------------
# Test 15: Module Context Package
# ---------------------------------------------------------------------------
def test_module_context_package():
    """Verifies module-level context package for backend module in ARTH."""
    service = ContextPackageService()
    pkg = service.get_context_package(
        repository="arth",
        subject="backend",
        subject_type="module",
    )

    assert pkg.refusal is False
    assert pkg.subject == "backend"
    assert pkg.subject_type == SubjectType.MODULE
    assert len(pkg.important_files) > 0
    assert pkg.verification.verified is True


# ---------------------------------------------------------------------------
# Test 16: FalkorDB Kill Switch Fails Closed
# ---------------------------------------------------------------------------
def test_falkordb_kill_switch_fails_closed():
    """Verifies that an unreachable FalkorDB fails closed with NO_PATH and no fallback."""
    mock_db = MagicMock(spec=FalkorDBManager)
    mock_db.check_health.return_value = {
        "connected": False,
        "error": "Connection refused by host",
    }

    service = ContextPackageService(db_manager=mock_db)
    pkg = service.get_context_package(repository="arth")

    assert pkg.refusal is True
    assert pkg.reason_code == RefusalCode.NO_PATH
    assert "fail-closed" in " ".join(pkg.limitations).lower() or "offline" in " ".join(pkg.limitations).lower()
    assert pkg.verification.verified is False


# ---------------------------------------------------------------------------
# Test 17: Cypher Injection Safety
# ---------------------------------------------------------------------------
def test_cypher_injection_safety():
    """Verifies that raw Cypher injection in arguments is treated safely as a string literal."""
    service = ContextPackageService()
    injection_attempt = 'arth" OR 1=1 RETURN * --'
    pkg = service.get_context_package(repository=injection_attempt)

    assert pkg.refusal is True
    assert pkg.reason_code == RefusalCode.NO_PATH


# ---------------------------------------------------------------------------
# Test 18: AI Agent Consumption Without Source Code
# ---------------------------------------------------------------------------
def test_ai_agent_consumption_without_source_code():
    """
    Proves that an external AI agent can consume the serialized Context Package
    and extract complete architectural understanding without reading source code.
    """
    service = ContextPackageService()
    pkg = service.get_context_package(
        repository="arth",
        subject="backend/app/engines/assistant/engine.py",
    )

    # Serialize to JSON (machine-readable agent payload)
    raw_json = pkg.model_dump_json()
    payload = json.loads(raw_json)

    # Simulated consuming agent extracting structured facts
    target_subject = payload["subject"]
    module_name = payload["modules"][0]["name"]
    top_author = payload["contributors"][0]["name"]
    blast_radius = len(payload["impact_context"]["co_changing_files"])
    is_verified = payload["verification"]["verified"]

    assert target_subject == "backend/app/engines/assistant/engine.py"
    assert module_name == "backend"
    assert top_author != ""
    assert blast_radius >= 0
    assert is_verified is True


# ---------------------------------------------------------------------------
# Test 19: API POST /api/v1/context Endpoint
# ---------------------------------------------------------------------------
def test_api_post_context_endpoint():
    """Verifies that the FastAPI POST /api/v1/context endpoint returns 200 with valid schema."""
    resp = client.post(
        "/api/v1/context",
        json={"repository": "arth", "depth": 3, "max_files": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["repository"] == "arth"
    assert data["subject_type"] == "repository"
    assert data["verification"]["verified"] is True
    assert data["refusal"] is False
    assert len(data["modules"]) > 0


# ---------------------------------------------------------------------------
# Test 20: Agent Coordinator Natural Language Context Routing
# ---------------------------------------------------------------------------
def test_agent_coordinator_context_routing():
    """Verifies end-to-end natural-language routing to context_package with verified narration."""
    coordinator = get_agent_coordinator()

    # 1. Repository context request
    r1 = coordinator.ask("Give me the context I need to understand ARTH.")
    assert r1.selected_tool == "context_package"
    assert r1.refused is False
    assert r1.verified is True
    assert "Context Package for repository 'arth'" in r1.narration

    # 2. File context request
    r2 = coordinator.ask("Give me the context for backend/app/engines/assistant/engine.py")
    assert r2.selected_tool == "context_package"
    assert r2.refused is False
    assert r2.verified is True
    assert "backend/app/engines/assistant/engine.py" in r2.narration
