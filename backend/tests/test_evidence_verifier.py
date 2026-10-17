import pytest
import datetime
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager
from app.evidence.models import (
    Claim,
    Evidence,
    RefusalCode,
    RefusalResponse,
    VerificationResult,
)
from app.evidence.verifier import EvidenceVerifier
from app.tools.reviewers import reviewers_for, get_verified_reviewers


client = TestClient(app)


def test_verifier_valid_claims_accepted():
    """Test A: Valid claim with valid matching evidence is accepted."""
    verifier = EvidenceVerifier()

    evidence_list = [
        Evidence(
            id="ev_commit_1",
            kind="commit",
            ref="sha111",
            properties={"sha": "sha111", "author": "Alice", "ts": "2026-10-01T10:00:00Z"},
        ),
        Evidence(
            id="ev_commit_2",
            kind="commit",
            ref="sha222",
            properties={"sha": "sha222", "author": "Alice", "ts": "2026-10-02T12:00:00Z"},
        ),
    ]

    claim = Claim(
        claim_id="claim_1",
        claim_type="commit_count",
        subject="Alice",
        predicate="made_commits_touching",
        value=2,
        evidence_ids=["ev_commit_1", "ev_commit_2"],
    )

    result = verifier.verify([claim], evidence_list)
    assert result.verified is True
    assert len(result.verified_claims) == 1
    assert len(result.rejected_claims) == 0


def test_verifier_claim_with_missing_evidence_rejected():
    """Test B: Claim referencing non-existent evidence ID is rejected."""
    verifier = EvidenceVerifier()

    claim = Claim(
        claim_id="claim_2",
        claim_type="commit_count",
        subject="Alice",
        predicate="made_commits_touching",
        value=1,
        evidence_ids=["ev_does_not_exist"],
    )

    result = verifier.verify([claim], [])
    assert result.verified is False
    assert len(result.rejected_claims) == 1
    assert "not found in provided evidence" in result.rejected_claims[0].reason


def test_verifier_count_mismatch_rejected():
    """Test C: Claim whose evidence count does not support claimed value is rejected."""
    verifier = EvidenceVerifier()

    evidence = [
        Evidence(
            id="ev_c1",
            kind="commit",
            ref="sha1",
            properties={"sha": "sha1"},
        )
    ]

    # Asserts 10 commits but only 1 evidence ID provided
    claim = Claim(
        claim_id="claim_3",
        claim_type="commit_count",
        subject="Alice",
        predicate="made_commits_touching",
        value=10,
        evidence_ids=["ev_c1"],
    )

    result = verifier.verify([claim], evidence)
    assert result.verified is False
    assert len(result.rejected_claims) == 1
    assert "Mismatch" in result.rejected_claims[0].reason


def test_verifier_fake_confidence_rejected():
    """Test G: Arbitrary confidence percentages or probability numbers are rejected."""
    verifier = EvidenceVerifier()

    evidence = [
        Evidence(id="ev_1", kind="commit", ref="sha1", properties={"sha": "sha1"})
    ]

    claim = Claim(
        claim_id="claim_prob",
        claim_type="probability",
        subject="Alice",
        predicate="confidence_score",
        value="94%",
        evidence_ids=["ev_1"],
    )

    result = verifier.verify([claim], evidence)
    assert result.verified is False
    assert any("Forbidden confidence" in r.reason for r in result.rejected_claims)


def test_verifier_unsupported_expert_owner_rejected():
    """Test H: Subjective adjectives like 'expert' or 'owner' are rejected."""
    verifier = EvidenceVerifier()

    evidence = [
        Evidence(id="ev_1", kind="commit", ref="sha1", properties={"sha": "sha1"})
    ]

    claim = Claim(
        claim_id="claim_expert",
        claim_type="status",
        subject="Alice",
        predicate="is_expert_in",
        value="internal.py",
        evidence_ids=["ev_1"],
    )

    result = verifier.verify([claim], evidence)
    assert result.verified is False
    assert any("Unsupported subjective claim" in r.reason for r in result.rejected_claims)


def test_hard_fail_safe_weak_evidence_cannot_assert_best_reviewer():
    """
    Test 7 (Hard Fail-Safe):
    Prove the trust boundary: Claim 'Person X is the best reviewer' with evidence
    only showing 2 commit touches MUST be rejected.
    """
    verifier = EvidenceVerifier()

    evidence = [
        Evidence(id="ev_1", kind="commit", ref="sha1", properties={"sha": "sha1"}),
        Evidence(id="ev_2", kind="commit", ref="sha2", properties={"sha": "sha2"}),
    ]

    # Strong claim not backed by graph facts
    claim = Claim(
        claim_id="claim_best",
        claim_type="ranking",
        subject="chandradeep05",
        predicate="is_the_best_reviewer_for",
        value="render.yaml",
        evidence_ids=["ev_1", "ev_2"],
    )

    result = verifier.verify([claim], evidence)
    assert result.verified is False
    assert len(result.rejected_claims) == 1
    assert "Unsupported subjective claim" in result.rejected_claims[0].reason


def test_refusal_unsupported_file_no_path():
    """Test D: Querying a nonexistent file refuses with NO_PATH before any downstream generation."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    res = get_verified_reviewers(
        target_file="nonexistent/unknown_file.py",
        repo_name="arth",
        db_manager=db_manager,
    )
    assert res.refused is True
    assert res.reason_code == RefusalCode.NO_PATH
    assert "No commit history found" in res.reason


def test_refusal_unsupported_module_no_path():
    """Test E: Querying a nonexistent module refuses with NO_PATH."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    res = get_verified_reviewers(
        target_module="nonexistent_module_xyz",
        repo_name="arth",
        db_manager=db_manager,
    )
    assert res.refused is True
    assert res.reason_code == RefusalCode.NO_PATH


def test_refusal_empty_graph():
    """Test F: An empty graph returns NO_PATH refusal."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    test_empty_graph = "test_empty_graph_milestone4"
    client = db_manager.get_client()
    try:
        try:
            client.select_graph(test_empty_graph).delete()
        except Exception:
            pass

        # Graph has no data
        res = get_verified_reviewers(
            target_file="any_file.py",
            graph_name=test_empty_graph,
            db_manager=db_manager,
        )
        assert res.refused is True
        assert res.reason_code == RefusalCode.NO_PATH
    finally:
        try:
            client.select_graph(test_empty_graph).delete()
        except Exception:
            pass


def test_refusal_stale_data():
    """Test J: Simulated stale sync timestamp triggers STALE_DATA refusal."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    test_stale_graph = "test_stale_graph_milestone4"
    client = db_manager.get_client()
    try:
        try:
            client.select_graph(test_stale_graph).delete()
        except Exception:
            pass

        graph = client.select_graph(test_stale_graph)
        # Seed graph with an old sync from 2 years ago
        old_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=700)).isoformat()
        q = """
        CREATE (r:Repository {name: 'old_repo'})
        CREATE (s:Sync {id: 'sync_old', head_sha: 'sha_old', synced_at: $old_date, commit_count: 5, source: 'git'})
        CREATE (r)-[:SNAPSHOT]->(s)
        CREATE (p:Person {name: 'Dev', email: 'dev@example.com'})
        CREATE (c:Commit {sha: 'sha_old', ts: $old_date, subject: 'old commit'})
        CREATE (f:File {path: 'old_file.py'})
        CREATE (p)-[:AUTHORED]->(c)
        CREATE (c)-[:TOUCHES {added: 1, deleted: 0}]->(f)
        """
        graph.query(q, {"old_date": old_date})

        # Max allowed age: 180 days -> should refuse with STALE_DATA
        res = get_verified_reviewers(
            target_file="old_file.py",
            repo_name="old_repo",
            graph_name=test_stale_graph,
            max_sync_age_days=180,
            db_manager=db_manager,
        )
        assert res.refused is True
        assert res.reason_code == RefusalCode.STALE_DATA
        assert "stale" in res.reason.lower()
    finally:
        try:
            client.select_graph(test_stale_graph).delete()
        except Exception:
            pass


def test_real_arth_reviewer_with_verified_evidence():
    """Test I: Valid reviewer recommendation from real ARTH graph has verified claims and evidence."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    res = get_verified_reviewers(
        target_file="render.yaml",
        repo_name="arth",
        db_manager=db_manager,
    )

    assert res.refused is False
    assert res.verification.verified is True
    assert len(res.claims) > 0
    assert len(res.evidence) == 2  # exactly 2 commits touching render.yaml
    assert len(res.candidates) == 1
    assert res.candidates[0].person_name == "chandradeep05"
    assert res.candidates[0].commit_count == 2
    assert res.sync_info is not None


def test_api_reviewers_contract_and_refusal():
    """Verify HTTP API endpoint adheres to refusal and evidence contract."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # 1. Nonexistent file returns 200 with refused: true and NO_PATH
    resp_refuse = client.get("/api/v1/reviewers?repo=arth&file=unknown_script.py")
    assert resp_refuse.status_code == 200
    data_refuse = resp_refuse.json()
    assert data_refuse["refused"] is True
    assert data_refuse["reason_code"] == "NO_PATH"
    assert data_refuse["target"] == "unknown_script.py"

    # 2. Valid file returns 200 with refused: false, verified claims, and evidence
    resp_ok = client.get("/api/v1/reviewers?repo=arth&file=render.yaml")
    assert resp_ok.status_code == 200
    data_ok = resp_ok.json()
    assert data_ok["refused"] is False
    assert "claims" in data_ok
    assert "evidence" in data_ok
    assert len(data_ok["candidates"]) == 1
    assert data_ok["candidates"][0]["commit_count"] == 2
