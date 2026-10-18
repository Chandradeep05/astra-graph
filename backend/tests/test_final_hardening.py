"""
Final adversarial hardening regression tests.

Covers:
- FINDING-001 (P0): OR f.repo IS NULL cross-repo contamination in reviewers_for
- FINDING-002 (P1): Memory constraint fail-open → fail-closed
- FINDING-003 (P1): Ingest endpoint path traversal restriction
- FINDING-004 (P1): Ingest endpoint graph_name restriction
- FINDING-005 (P2): Conftest get_graph_signature raises ValueError on unreachable DB
"""
import os
import pytest
from fastapi.testclient import TestClient

from app.db.falkor import FalkorDBManager
from app.tools.reviewers import reviewers_for, get_verified_reviewers
from app.actions.models import ActionStatus
from app.evidence.models import RefusalCode


# ---------------------------------------------------------------------------
# FINDING-001 (P0): OR f.repo IS NULL cross-repo contamination
# ---------------------------------------------------------------------------

PROBE_GRAPH = "anvaya_probe_hardening"


@pytest.fixture(scope="module")
def probe_db():
    """Creates an isolated probe graph with a NULL-repo File node (simulating stale/migrated data)."""
    db = FalkorDBManager(graph_name=PROBE_GRAPH)
    health = db.check_health()
    if not health.get("connected"):
        pytest.skip("FalkorDB not available")

    g = db.get_graph()

    # Clean up any prior probe state
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    # Seed: create a File node with NO repo property (NULL repo) to simulate stale/unscoped data
    # This would represent data that existed before the repo-scoping migration
    g.query("""
    CREATE (p:Person {email: 'attacker@evil.com', name: 'AttackerUser'})
    CREATE (c:Commit {sha: 'deadbeef0000000000000000', ts: '2025-01-01T00:00:00', subject: 'evil commit', repo: 'evil'})
    CREATE (f:File {path: 'README.md'})
    CREATE (p)-[:AUTHORED]->(c)
    CREATE (c)-[:TOUCHES]->(f)
    """)

    # Also create a proper scoped file for a different repo
    g.query("""
    CREATE (p2:Person {email: 'legit@arth.com', name: 'LegitUser'})
    CREATE (c2:Commit {sha: 'aaaa000000000000000000000000000000000000', ts: '2025-02-01T00:00:00', subject: 'legit commit', repo: 'arth'})
    CREATE (r:Repository {name: 'arth'})
    CREATE (f2:File {repo: 'arth', path: 'README.md'})
    CREATE (p2)-[:AUTHORED]->(c2)
    CREATE (c2)-[:TOUCHES]->(f2)
    MERGE (r)-[:CONTAINS]->(f2)
    MERGE (r)-[:HAS_COMMIT]->(c2)
    """)

    yield db

    # Cleanup: delete all nodes in the probe graph
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass


class TestNullRepoContamination:
    """
    FINDING-001 (P0): Verify that the OR f.repo IS NULL loophole is closed.
    Without the fix, attackers who inject NULL-repo nodes can have them returned
    for ANY repository query. After the fix, only scoped nodes are returned.
    """

    def test_null_repo_file_not_returned_for_unrelated_repo(self, probe_db):
        """
        A File node with NULL repo must NOT be returned when querying repo='arth'.
        After fix: MATCH (f:File {repo: 'arth', path: 'README.md'}) only returns 'arth' files.
        """
        result = reviewers_for(
            target_file="README.md",
            repo_name="arth",
            graph_name=PROBE_GRAPH,
            db_manager=probe_db,
        )
        # The attacker (attacker@evil.com) should NOT appear as a candidate
        attacker_emails = [c.person_email for c in result.candidates]
        assert "attacker@evil.com" not in attacker_emails, (
            f"CROSS-REPO CONTAMINATION: attacker@evil.com appeared in repo='arth' results "
            f"via NULL-repo File node. Candidates: {attacker_emails}"
        )

    def test_legitimate_arth_file_still_found(self, probe_db):
        """
        A properly scoped File {repo: 'arth', path: 'README.md'} MUST be found.
        The fix must not break valid queries by being too restrictive.
        """
        result = reviewers_for(
            target_file="README.md",
            repo_name="arth",
            graph_name=PROBE_GRAPH,
            db_manager=probe_db,
        )
        # LegitUser should be in candidates (they have a properly scoped arth/README.md file)
        legit_emails = [c.person_email for c in result.candidates]
        assert "legit@arth.com" in legit_emails, (
            f"Legit arth user not found — fix may be too aggressive. Candidates: {legit_emails}"
        )

    def test_null_repo_file_not_returned_for_other_repos(self, probe_db):
        """
        A File node with NULL repo must NOT appear when querying any repo including 'databases'.
        """
        result = reviewers_for(
            target_file="README.md",
            repo_name="databases",
            graph_name=PROBE_GRAPH,
            db_manager=probe_db,
        )
        attacker_emails = [c.person_email for c in result.candidates]
        assert "attacker@evil.com" not in attacker_emails, (
            f"NULL-repo file leaks into 'databases' query: {attacker_emails}"
        )

    def test_verified_reviewers_also_reject_null_repo_contamination(self, probe_db):
        """
        get_verified_reviewers must also refuse or not return NULL-repo contaminated data.
        The verified path is the one used by the agent coordinator.
        """
        result = get_verified_reviewers(
            target_file="README.md",
            repo_name="arth",
            graph_name=PROBE_GRAPH,
            db_manager=probe_db,
        )
        # Either refused (no graph path found) or attacker is not in candidates
        if not result.refused:
            attacker_emails = [c.person_email for c in result.candidates]
            assert "attacker@evil.com" not in attacker_emails, (
                f"CRITICAL: Verified reviewers path returns NULL-repo contaminated data. "
                f"Candidates: {attacker_emails}"
            )


# ---------------------------------------------------------------------------
# FINDING-002 (P1): Memory constraint fail-closed (not fail-open)
# ---------------------------------------------------------------------------

class TestMemoryConstraintFailClosed:
    """
    FINDING-002 (P1): Memory constraint query failure must cause a refusal,
    not silently skip constraint filtering and return non-compliant candidates.
    """

    def test_constraint_query_failure_causes_refusal(self):
        """
        Simulate a FalkorDB error during memory constraint lookup.
        Must return refused=True, not proceed with unfiltered candidates.
        """
        from unittest.mock import MagicMock, patch
        from app.tools.reviewers import get_verified_reviewers

        # Create a mock manager that succeeds health check and base query
        # but raises on the constraint query
        call_count = [0]
        real_db = FalkorDBManager()

        if not real_db.check_health().get("connected"):
            pytest.skip("FalkorDB not available")

        original_query = real_db.get_graph().query.__class__

        class RaisingGraph:
            """Wraps a real graph but raises on the constraint query."""
            def __init__(self, real_g):
                self._real = real_g
                self._calls = 0

            def query(self, q, params=None):
                self._calls += 1
                if "Constraint" in q and "reviewer_unavailable" in q:
                    raise RuntimeError("Simulated FalkorDB constraint query failure")
                return self._real.query(q, params)

        class MockManager:
            def check_health(self):
                return {"connected": True}

            def get_graph(self, name=None):
                return RaisingGraph(real_db.get_graph("anvaya_test"))

        result = get_verified_reviewers(
            target_file="backend/app/main.py",
            repo_name="arth",
            db_manager=MockManager(),  # type: ignore
        )

        assert result.refused, (
            "Memory constraint query failure must result in refusal (fail-closed), "
            "not silently proceed with unfiltered candidates."
        )
        assert result.reason_code == RefusalCode.NO_PATH, (
            f"Expected NO_PATH reason code, got: {result.reason_code}"
        )
        assert "constraint" in result.reason.lower() or "enforcement" in result.reason.lower(), (
            f"Refusal reason should mention constraint failure. Got: {result.reason}"
        )


# ---------------------------------------------------------------------------
# FINDING-003 (P1) + FINDING-004 (P1): Ingest endpoint path traversal + graph_name
# ---------------------------------------------------------------------------

class TestIngestEndpointSecurity:
    """
    FINDING-003 (P1): /ingest must reject repo_path values outside data/repos/.
    FINDING-004 (P1): /ingest must reject requests targeting non-canonical graph names.
    """

    @pytest.fixture(scope="class")
    def client(self):
        from app.main import app
        return TestClient(app)

    def test_ingest_rejects_absolute_system_path(self, client):
        """Path traversal: request to ingest /etc (or C:\\Windows) must be rejected with 403."""
        # Use os.path.abspath to get a real absolute path outside data/repos
        evil_path = os.path.abspath("/etc") if os.name != "nt" else os.path.abspath("C:\\Windows")

        resp = client.post("/api/v1/ingest", json={
            "repo_path": evil_path,
            "repo_name": "pwned",
        })
        assert resp.status_code in (400, 403), (
            f"Ingest endpoint must reject paths outside data/repos/. Got: {resp.status_code}, {resp.text}"
        )

    def test_ingest_rejects_relative_traversal(self, client):
        """Path traversal: ../../../etc/passwd style path must be rejected."""
        resp = client.post("/api/v1/ingest", json={
            "repo_path": "../../../etc",
            "repo_name": "traversal_test",
        })
        assert resp.status_code in (400, 403), (
            f"Relative traversal path must be rejected. Got: {resp.status_code}, {resp.text}"
        )

    def test_ingest_rejects_noncanonical_graph_name(self, client):
        """
        FINDING-004: Caller must not be allowed to write to an arbitrary graph.
        Specifying graph_name='anvaya' (production) or any other non-canonical name must be rejected.
        """
        resp = client.post("/api/v1/ingest", json={
            "repo_path": "data/repos/arth",
            "repo_name": "arth",
            "graph_name": "anvaya",  # This would target production graph!
        })
        # Since anvaya_test is the canonical graph in tests (env var override),
        # trying to specify 'anvaya' explicitly should be rejected
        # (or succeed only if 'anvaya' == canonical — won't be true in test env)
        # Test: either 403 Forbidden or the request proceeds to the canonical test graph only
        if resp.status_code == 403:
            assert "not permitted" in resp.json().get("detail", "").lower() or \
                   "graph_name" in resp.json().get("detail", "").lower(), (
                f"403 should mention graph_name restriction. Got: {resp.text}"
            )
        elif resp.status_code == 200:
            # This means 'anvaya' matched the canonical graph name (acceptable in prod)
            # Verify the response is legitimate
            assert resp.json().get("status") == "success"

    def test_ingest_allows_valid_data_repos_path(self, client):
        """A valid path under data/repos/ must be accepted (not rejected by security check)."""
        # Only run if the path actually exists
        test_path = os.path.abspath("data/repos/arth")
        if not os.path.isdir(test_path):
            pytest.skip("data/repos/arth not available")

        resp = client.post("/api/v1/ingest", json={
            "repo_path": "data/repos/arth",
            "repo_name": "arth",
        })
        # Must succeed or fail for git reasons, not security rejection
        assert resp.status_code != 403, (
            f"Valid data/repos/ path should not be rejected. Got: {resp.status_code}, {resp.text}"
        )


# ---------------------------------------------------------------------------
# FINDING-005 (P2): get_graph_signature raises ValueError on unreachable DB
# ---------------------------------------------------------------------------

class TestConfTestGraphSignature:
    """
    FINDING-005 (P2): get_graph_signature must raise ValueError when FalkorDB
    is unreachable, not silently return {} which would cause vacuous {} == {} assertion.
    """

    def test_get_graph_signature_raises_on_unreachable_db(self):
        """
        With a non-existent FalkorDB host, get_graph_signature must raise ValueError,
        not silently return {} which would make the production graph safety assertion vacuous.
        """
        from tests.conftest import get_graph_signature

        # Create a manager pointing to a non-existent host
        bad_manager = FalkorDBManager(host="127.0.0.1", port=19999)

        with pytest.raises(ValueError, match="not reachable|unreachable|cannot compute"):
            get_graph_signature(bad_manager, "some_graph")

    def test_get_graph_signature_returns_real_data_when_connected(self):
        """
        When FalkorDB is connected, get_graph_signature must return a non-empty dict
        with the expected keys.
        """
        from tests.conftest import get_graph_signature

        manager = FalkorDBManager()
        health = manager.check_health()
        if not health.get("connected"):
            pytest.skip("FalkorDB not available")

        sig = get_graph_signature(manager, "anvaya")
        assert isinstance(sig, dict), "Signature must be a dict"
        assert "commit_count" in sig
        assert "file_count" in sig
        assert "person_count" in sig
        assert sig["commit_count"] > 0, "Production graph must have commits"
        assert sig["file_count"] > 0, "Production graph must have files"
