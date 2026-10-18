import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager
from app.tools.reviewers import reviewers_for
from app.tools.models import ReviewersResponse


client = TestClient(app)


def test_reviewers_for_file_real_arth():
    """Verify reviewer retrieval for a specific real file in ARTH."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # render.yaml has exactly 2 commits in ARTH
    result: ReviewersResponse = reviewers_for(
        target_file="render.yaml",
        repo_name="arth",
        db_manager=db_manager,
    )

    assert result.target == "render.yaml"
    assert result.target_type == "file"
    assert result.total_candidates >= 1

    top = result.candidates[0]
    assert top.person_name == "chandradeep05"
    assert top.person_email == "c4chandradeep2005@gmail.com"
    assert top.commit_count == 2
    assert top.lines_added == 36
    assert top.lines_deleted == 0
    assert len(top.evidence_commits) == 2
    assert "9af07c8d1d695aae0900ccfacb1b9e41cb3bd28a" in top.evidence_commits
    assert "c789bef26542e3cf2507da59301a1d06fea43770" in top.evidence_commits
    assert top.last_commit_sha == "9af07c8d1d695aae0900ccfacb1b9e41cb3bd28a"
    assert result.sync_info is not None


def test_reviewers_for_module_real_arth():
    """Verify reviewer retrieval for an entire module in ARTH."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    result: ReviewersResponse = reviewers_for(
        target_module="backend",
        repo_name="arth",
        db_manager=db_manager,
    )

    assert result.target == "backend"
    assert result.target_type == "module"
    assert result.total_candidates >= 1

    top = result.candidates[0]
    assert top.person_name == "chandradeep05"
    assert top.commit_count == 76
    assert top.lines_added > 20000
    assert len(top.evidence_commits) > 0
    assert top.last_commit_sha == "79595add24d7e561d28be4fe66af3fa2c17b933d"


def test_reviewers_nonexistent_file_and_module():
    """Verify graceful handling and empty results for nonexistent file/module."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    res_file = reviewers_for(target_file="nonexistent/fake_file.py", db_manager=db_manager)
    assert res_file.total_candidates == 0
    assert len(res_file.candidates) == 0

    res_mod = reviewers_for(target_module="nonexistent_module", db_manager=db_manager)
    assert res_mod.total_candidates == 0
    assert len(res_mod.candidates) == 0


def test_reviewers_exclusion():
    """Verify that candidate exclusion filters out specified emails."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # In ARTH, excluding the sole contributor should return 0 candidates
    res = reviewers_for(
        target_file="render.yaml",
        exclude_emails=["c4chandradeep2005@gmail.com"],
        db_manager=db_manager,
    )
    assert res.total_candidates == 0
    assert len(res.candidates) == 0


def test_reviewers_multiple_contributors_synthetic():
    """
    Verify ranking and aggregation when multiple contributors exist.
    Uses an isolated synthetic test graph so real ARTH data remains untouched.
    """
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    test_graph_name = "test_synthetic_reviewers"
    falkor_client = db_manager.get_client()

    try:
        try:
            falkor_client.select_graph(test_graph_name).delete()
        except Exception:
            pass

        graph = falkor_client.select_graph(test_graph_name)

        # Seed synthetic multi-contributor history:
        # Alice: 5 commits, added 100, deleted 20 (most recent ts: 2026-10-05)
        # Bob: 2 commits, added 40, deleted 10 (most recent ts: 2026-10-02)
        # Carol: 1 commit, added 5, deleted 2 (most recent ts: 2026-09-30)
        seed_cypher = """
        CREATE (f:File {repo: 'arth', path: 'src/core/router.py'})
        CREATE (m:Module {repo: 'arth', path: 'src/core'})
        CREATE (f)-[:IN_MODULE]->(m)

        CREATE (p1:Person {name: 'Alice Lead', email: 'alice@example.com'})
        CREATE (p2:Person {name: 'Bob Peer', email: 'bob@example.com'})
        CREATE (p3:Person {name: 'Carol Junior', email: 'carol@example.com'})

        CREATE (c1:Commit {sha: 'c1', ts: '2026-10-05T10:00:00Z', subject: 'alice commit 1'})
        CREATE (c2:Commit {sha: 'c2', ts: '2026-10-04T10:00:00Z', subject: 'alice commit 2'})
        CREATE (c3:Commit {sha: 'c3', ts: '2026-10-03T10:00:00Z', subject: 'alice commit 3'})
        CREATE (c4:Commit {sha: 'c4', ts: '2026-10-02T10:00:00Z', subject: 'bob commit 1'})
        CREATE (c5:Commit {sha: 'c5', ts: '2026-10-01T10:00:00Z', subject: 'bob commit 2'})
        CREATE (c6:Commit {sha: 'c6', ts: '2026-09-30T10:00:00Z', subject: 'carol commit 1'})

        CREATE (p1)-[:AUTHORED]->(c1)
        CREATE (p1)-[:AUTHORED]->(c2)
        CREATE (p1)-[:AUTHORED]->(c3)
        CREATE (p2)-[:AUTHORED]->(c4)
        CREATE (p2)-[:AUTHORED]->(c5)
        CREATE (p3)-[:AUTHORED]->(c6)

        CREATE (c1)-[:TOUCHES {added: 50, deleted: 10}]->(f)
        CREATE (c2)-[:TOUCHES {added: 30, deleted: 5}]->(f)
        CREATE (c3)-[:TOUCHES {added: 20, deleted: 5}]->(f)
        CREATE (c4)-[:TOUCHES {added: 25, deleted: 5}]->(f)
        CREATE (c5)-[:TOUCHES {added: 15, deleted: 5}]->(f)
        CREATE (c6)-[:TOUCHES {added: 5, deleted: 2}]->(f)
        """
        graph.query(seed_cypher)

        # Query file reviewers on synthetic graph
        res = reviewers_for(
            target_file="src/core/router.py",
            graph_name=test_graph_name,
            db_manager=db_manager,
        )

        assert res.total_candidates == 3
        cands = res.candidates

        # Rank 1: Alice (3 commits, 100 added, 20 deleted)
        assert cands[0].person_name == "Alice Lead"
        assert cands[0].commit_count == 3
        assert cands[0].lines_added == 100
        assert cands[0].lines_deleted == 20
        assert cands[0].last_commit_sha == "c1"
        assert set(cands[0].evidence_commits) == {"c1", "c2", "c3"}

        # Rank 2: Bob (2 commits, 40 added, 10 deleted)
        assert cands[1].person_name == "Bob Peer"
        assert cands[1].commit_count == 2
        assert cands[1].lines_added == 40
        assert cands[1].lines_deleted == 10
        assert cands[1].last_commit_sha == "c4"
        assert set(cands[1].evidence_commits) == {"c4", "c5"}

        # Rank 3: Carol (1 commit, 5 added, 2 deleted)
        assert cands[2].person_name == "Carol Junior"
        assert cands[2].commit_count == 1
        assert cands[2].last_commit_sha == "c6"

    finally:
        try:
            falkor_client.select_graph(test_graph_name).delete()
        except Exception:
            pass


def test_reviewers_injection_safety():
    """Verify that malicious input strings cannot alter query structure."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    malicious_inputs = [
        "' OR 1=1 RETURN * //",
        '"; DROP ALL;',
        "render.yaml' OR '1'='1",
        "../../etc/passwd",
    ]

    for attack_str in malicious_inputs:
        res = reviewers_for(target_file=attack_str, db_manager=db_manager)
        assert res.total_candidates == 0
        assert len(res.candidates) == 0


def test_reviewers_api_endpoints():
    """Verify HTTP GET /api/v1/reviewers endpoint."""
    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # 1. File query
    resp_file = client.get("/api/v1/reviewers?repo=arth&file=render.yaml")
    assert resp_file.status_code == 200
    data_file = resp_file.json()
    assert data_file["target"] == "render.yaml"
    assert data_file["target_type"] == "file"
    assert len(data_file["candidates"]) >= 1

    # 2. Module query
    resp_mod = client.get("/api/v1/reviewers?repo=arth&module=backend")
    assert resp_mod.status_code == 200
    data_mod = resp_mod.json()
    assert data_mod["target"] == "backend"
    assert data_mod["target_type"] == "module"
    assert len(data_mod["candidates"]) >= 1

    # 3. Missing both file and module -> 400 Bad Request
    resp_bad = client.get("/api/v1/reviewers?repo=arth")
    assert resp_bad.status_code == 400
    assert "Either 'file' or 'module' must be provided" in resp_bad.json()["detail"]
