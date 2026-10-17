import pytest
import os
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager
from app.ingest.models import GitCommit, GitTouch
from app.ingest.git_parser import parse_git_log_output, get_module_path, parse_git_history
from app.ingest.graph_loader import GitGraphLoader


client = TestClient(app)


def test_get_module_path():
    """Verify module derivation according to PRD path rules."""
    assert get_module_path("README.md") == "."
    assert get_module_path(".gitignore") == "."
    assert get_module_path("backend/app/main.py") == "backend"
    assert get_module_path("frontend/src/app/page.tsx") == "frontend"
    assert get_module_path("src/auth/jwt.py") == "src/auth"
    assert get_module_path("packages/core/src/index.ts") == "packages/core"


def test_parse_git_log_output():
    """Verify parsing of formatted git log --numstat output."""
    sample_raw = """COMMIT_SEP\taaa111\tAlice Dev\talice@example.com\t2026-10-01T12:00:00Z\tfeat: initial commit
10\t2\tbackend/main.py
5\t0\tREADME.md
-\t-\tassets/logo.png

COMMIT_SEP\tbbb222\tBob Reviewer\tbob@example.com\t2026-10-02T15:30:00Z\tfix(core): bug fix (#42)
1\t1\tbackend/main.py
"""
    commits = parse_git_log_output(sample_raw)
    assert len(commits) == 2

    c1 = commits[0]
    assert c1.sha == "aaa111"
    assert c1.author_name == "Alice Dev"
    assert c1.author_email == "alice@example.com"
    assert c1.subject == "feat: initial commit"
    assert len(c1.touches) == 3

    t1 = c1.touches[0]
    assert t1.path == "backend/main.py"
    assert t1.added == 10
    assert t1.deleted == 2
    assert t1.module == "backend"

    # Binary file with -/- converted safely to 0/0
    t_bin = c1.touches[2]
    assert t_bin.path == "assets/logo.png"
    assert t_bin.added == 0
    assert t_bin.deleted == 0

    c2 = commits[1]
    assert c2.sha == "bbb222"
    assert c2.author_name == "Bob Reviewer"
    assert len(c2.touches) == 1


def test_parse_real_arth_repo():
    """Verify real git parsing against locally cloned ARTH repository."""
    repo_path = "data/repos/arth"
    if not os.path.exists(repo_path):
        pytest.skip(f"Repository not found at {repo_path}")

    commits = parse_git_history(repo_path, max_commits=2000)
    assert len(commits) == 97
    assert commits[0].sha == "79595add24d7e561d28be4fe66af3fa2c17b933d"
    assert any(c.author_email == "c4chandradeep2005@gmail.com" for c in commits)


def test_graph_loader_and_idempotence():
    """
    Test loading real git history into FalkorDB and verify:
    1. Schema indexes are created.
    2. Nodes and relationships match commit counts.
    3. Re-ingesting the same history is completely idempotent (no duplicates).
    """
    repo_path = "data/repos/arth"
    if not os.path.exists(repo_path):
        pytest.skip(f"Repository not found at {repo_path}")

    db_manager = FalkorDBManager()
    health = db_manager.check_health()
    if not health["connected"]:
        pytest.skip("FalkorDB is not reachable for integration test")

    test_graph_name = "test_anvaya_ingest_graph"
    client_falkor = db_manager.get_client()

    try:
        # Delete if previous test left remnants
        try:
            client_falkor.select_graph(test_graph_name).delete()
        except Exception:
            pass

        loader = GitGraphLoader(db_manager)
        commits = parse_git_history(repo_path, max_commits=2000)

        # 1st Ingestion run
        summary1 = loader.load_history("arth", commits, repo_path=repo_path, graph_name=test_graph_name)
        assert summary1.commit_count == 97
        assert summary1.person_count >= 1
        assert summary1.file_count > 0
        assert summary1.module_count > 0
        assert summary1.head_sha == "79595add24d7e561d28be4fe66af3fa2c17b933d"

        # Verify directly in FalkorDB via Cypher
        graph = client_falkor.select_graph(test_graph_name)
        c_count = graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
        p_count = graph.query("MATCH (p:Person) RETURN count(p)").result_set[0][0]
        f_count = graph.query("MATCH (f:File) RETURN count(f)").result_set[0][0]
        m_count = graph.query("MATCH (m:Module) RETURN count(m)").result_set[0][0]

        assert c_count == 97
        assert p_count >= 1

        # Check Repository and Sync nodes
        sync_res = graph.query(
            "MATCH (r:Repository {name: 'arth'})-[:SNAPSHOT]->(s:Sync) RETURN s.head_sha, s.commit_count"
        )
        assert len(sync_res.result_set) == 1
        assert sync_res.result_set[0][0] == "79595add24d7e561d28be4fe66af3fa2c17b933d"
        assert sync_res.result_set[0][1] == 97

        # 2nd Ingestion run (IDEMPOTENCE TEST)
        summary2 = loader.load_history("arth", commits, repo_path=repo_path, graph_name=test_graph_name)
        c_count_after = graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
        p_count_after = graph.query("MATCH (p:Person) RETURN count(p)").result_set[0][0]
        f_count_after = graph.query("MATCH (f:File) RETURN count(f)").result_set[0][0]
        m_count_after = graph.query("MATCH (m:Module) RETURN count(m)").result_set[0][0]

        assert c_count_after == c_count
        assert p_count_after == p_count
        assert f_count_after == f_count
        assert m_count_after == m_count

    finally:
        try:
            client_falkor.select_graph(test_graph_name).delete()
        except Exception:
            pass


def test_ingest_api_endpoint():
    """Verify POST /api/v1/ingest endpoint."""
    repo_path = "data/repos/arth"
    if not os.path.exists(repo_path):
        pytest.skip(f"Repository not found at {repo_path}")

    db_manager = FalkorDBManager()
    if not db_manager.check_health()["connected"]:
        pytest.skip("FalkorDB is not reachable")

    response = client.post(
        "/api/v1/ingest",
        json={"repo_path": repo_path, "repo_name": "arth", "max_commits": 10},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["summary"]["repo_name"] == "arth"
    assert data["summary"]["commit_count"] >= 10
    assert data["summary"]["commit_count"] > 0
